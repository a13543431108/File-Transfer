package com.p2p.filetransfer.room

import java.io.InputStream
import java.io.OutputStream
import java.net.SocketTimeoutException
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit

/**
 * UDP 可靠传输层（UDP-RTP v1）—— 共享 socket 模式。
 *
 * 协议（12 字节包头，大端）：
 *   [1B type][4B seq][4B ack][2B length][1B reserved]
 *     type: 0=DATA, 1=ACK, 2=FIN
 *
 * 与电脑端 udp_reliable.py 协议一致。
 *
 * 数据路由：
 *   · 发送：调用 sendFn(peer, data)（由上层提供，实际写到共享 udp socket）
 *   · 接收：上层全局接收循环收到本对端的包后，调用 onPacket(data)
 *
 * 本类不持有 socket，也不启动接收线程。
 */
class UdpReliableSocket(
    private val peerAddr: java.net.InetSocketAddress,
    private val sendFn: (java.net.InetSocketAddress, ByteArray) -> Unit,
    private val log: (String) -> Unit,
    private val onPeerDead: (() -> Unit)? = null,   // P0: 对端失联回调
    private val onSyncReady: ((Long) -> Unit)? = null   // P1: SYNC 完成回调(T_go_local_ms)
) : com.p2p.filetransfer.protocol.StreamSocket {

    /** P2: 收到主导方轮次对齐时刻的回调 (roundNo, tGoR)。由 RoomManager 挂载。 */
    @Volatile var onPunchRound: ((Long, Long) -> Unit)? = null

    /** P2 #6: 对端 TCP 映射就绪回调。 */
    @Volatile var onTcpReady: (() -> Unit)? = null

    /** P2 #8: 对端放弃 TCP 回调。 */
    @Volatile var onPunchFail: (() -> Unit)? = null
    companion object {
        private const val TYPE_DATA = 0
        private const val TYPE_ACK = 1
        private const val TYPE_FIN = 2
        private const val TYPE_KEEPALIVE = 3   // P0
        private const val TYPE_SYNC_REQ = 4    // P1
        private const val TYPE_SYNC_ACK = 5    // P1
        private const val TYPE_SYNC_COMMIT = 6 // P1
        private const val TYPE_PUNCH_ROUND = 7 // P2: 打洞轮次对齐
        private const val TYPE_TCP_READY = 8   // P2 #6: TCP 映射就绪信号
        private const val TYPE_PUNCH_FAIL = 9  // P2 #8: 打洞彻底失败通知
        private const val MAX_PAYLOAD = 1200
        // 自适应窗口（AIMD）：发送端根据 ACK / 丢包动态调在途包数。
        //   收到有效 ACK → 窗口 +1（加性增，封顶 WINDOW_MAX）
        //   超时重传   → 窗口减半（乘性减，下限 WINDOW_MIN）
        // 接收端乱序接受范围固定 WINDOW_MAX，保证发送端怎么调都能被接住。
        private const val WINDOW_MIN = 1
        private const val WINDOW_MAX = 256
        private const val WINDOW_INIT = 64
        private const val RTO_MS = 300L
        private const val RTX_INTERVAL_MS = 50L
        private const val HEADER_LEN = 12
        private const val KEEPALIVE_INTERVAL_MS = 15_000L
        private const val PEER_DEAD_TIMEOUT_MS = 90_000L
        private const val KA_CHECK_INTERVAL_MS = 5_000L
        private const val SYNC_SAMPLE_COUNT = 4
        private const val SYNC_TIMEOUT_MS = 1_000L
        private const val SYNC_COMMIT_DELAY_MS = 500L
        private const val SYNC_COMMIT_RESEND = 3       // P2: COMMIT 重传次数
        private const val SYNC_COMMIT_RESEND_INTERVAL_MS = 100L  // P2: 重传间隔
        private const val PROTO_VER = 2                // P2: 协议版本
    }

    /** 供上层识别传输类型。 */
    val isUdpRtp = true

    /** 收发串行化锁（与 TCP 一致，避免接收循环和发送方争抢同一队列）。 */
    val ioLock = kotlinx.coroutines.sync.Mutex()

    val peer: java.net.InetSocketAddress get() = peerAddr

    @Volatile private var sendSeq = 0
    private val sendQueue = HashMap<Int, Pair<ByteArray, Long>>()
    private val sendLock: java.lang.Object = java.lang.Object()
    /** 自适应发送窗口（AIMD，受 sendLock 保护）。 */
    private var window = WINDOW_INIT

    @Volatile private var recvNext = 0
    private val outOfOrder = HashMap<Int, ByteArray>()
    private val recvLock: java.lang.Object = java.lang.Object()

    /** 已按序重组的数据块队列。 */
    private val readyChunks = LinkedBlockingQueue<ByteArray>()
    private var curChunk: ByteArray? = null
    private var curOffset = 0

    @Volatile private var closed = false
    @Volatile private var peerClosed = false

    @Volatile var timeoutMs: Int = 0

    // P0：keepalive + 失联检测
    @Volatile private var lastSendTime = System.currentTimeMillis()
    @Volatile private var lastRecvTime = System.currentTimeMillis()
    @Volatile private var peerDeadFired = false

    // P1：SYNC 状态
    private val syncLock = Object()
    @Volatile private var syncInProgress = false
    private val syncPending = HashMap<Long, java.util.concurrent.CountDownLatch>()
    private val syncReplies = HashMap<Long, LongArray>()

    // P2: 轮次对齐 + 版本
    @Volatile private var syncOffset: Long? = null      // responder钟 - 本机钟
    @Volatile private var syncRtt: Long? = null         // P2 #9: SYNC 最小 RTT（毫秒）
    @Volatile private var peerVer = 1                   // 对端协议版本（默认 1）
    @Volatile private var commitSeq = 0L                // COMMIT 自增轮次号
    @Volatile private var lastCommitId = -1L            // 已处理的 commit_id（去重）
    private val commitLock = Object()

    /** StreamSocket 接口：soTimeout 映射到 timeoutMs。 */
    override var soTimeout: Int
        get() = timeoutMs
        set(v) { timeoutMs = v }

    private val rtxThread: Thread = Thread({ rtxLoop() }, "UdpRtp-Rtx").also {
        it.isDaemon = true
        it.start()
    }
    private val kaThread: Thread = Thread({ keepaliveLoop() }, "UdpRtp-KA").also {
        it.isDaemon = true
        it.start()
    }

    // ---------- 对外接口 ----------

    fun sendall(data: ByteArray) {
        if (data.isEmpty() || closed) return
        var offset = 0
        while (offset < data.size) {
            val len = minOf(MAX_PAYLOAD, data.size - offset)
            val chunk = data.copyOfRange(offset, offset + len)
            offset += len
            val pkt: ByteArray
            val seq: Int
            synchronized(sendLock) {
                seq = sendSeq
                sendSeq += 1
                pkt = buildPacket(TYPE_DATA, seq, recvNext, chunk)
                sendQueue[seq] = Pair(pkt, System.currentTimeMillis())
                while (sendQueue.size >= window && !closed) {
                    try { sendLock.wait(5) } catch (_: InterruptedException) { return }
                }
            }
            try { sendFn(peerAddr, pkt); lastSendTime = System.currentTimeMillis() } catch (_: Exception) { return }
        }
    }

    fun recv(n: Int): ByteArray {
        val deadline = if (timeoutMs > 0) System.currentTimeMillis() + timeoutMs else 0L
        while (true) {
            synchronized(recvLock) {
                val c = curChunk
                if (c != null && curOffset < c.size) {
                    val take = minOf(n, c.size - curOffset)
                    val out = c.copyOfRange(curOffset, curOffset + take)
                    curOffset += take
                    if (curOffset >= c.size) { curChunk = null; curOffset = 0 }
                    return out
                }
                if (peerClosed) {
                    log("[UDP-RTP] recv 返回空（peerClosed）")
                    return ByteArray(0)
                }
            }
            val remain = if (deadline > 0) deadline - System.currentTimeMillis() else 500L
            if (deadline > 0 && remain <= 0) throw SocketTimeoutException("recv timeout")
            val chunk = try {
                readyChunks.poll(remain.coerceAtLeast(1), TimeUnit.MILLISECONDS)
            } catch (_: InterruptedException) { null }
            if (chunk == null) {
                if (deadline > 0) throw SocketTimeoutException("recv timeout")
            } else {
                synchronized(recvLock) { curChunk = chunk; curOffset = 0 }
            }
        }
    }

    fun recvExact(n: Int): ByteArray {
        val buf = ByteArray(n)
        var got = 0
        while (got < n) {
            val chunk = recv(n - got)
            if (chunk.isEmpty()) throw java.io.IOException("UDP-RTP 对端关闭")
            System.arraycopy(chunk, 0, buf, got, chunk.size)
            got += chunk.size
        }
        return buf
    }

    override fun close() {
        if (closed) return
        closed = true
        try { sendFn(peerAddr, buildPacket(TYPE_FIN, 0, recvNext, ByteArray(0))) } catch (_: Exception) {}
        synchronized(recvLock) { recvLock.notifyAll() }
    }

    /**
     * P1：发起 SYNC 采样 → 发 SYNC_COMMIT 约定 TCP 打洞时刻。
     * 阻塞执行（约 1-4 秒）。由上层作为 initiator 时在后台线程调用。
     */
    fun startSync() {
        synchronized(syncLock) {
            if (syncInProgress) {
                log("[SYNC] 已有采样在进行，跳过")
                return
            }
            syncInProgress = true
            syncPending.clear()
            syncReplies.clear()
        }
        val samples = ArrayList<LongArray>()
        try {
            for (i in 0 until SYNC_SAMPLE_COUNT) {
                if (closed) return
                val t1 = System.currentTimeMillis()
                val latch = java.util.concurrent.CountDownLatch(1)
                synchronized(syncLock) { syncPending[t1] = latch }
                try {
                    val payload = ByteBuffer.allocate(9).order(ByteOrder.BIG_ENDIAN)
                        .putLong(t1).put(PROTO_VER.toByte()).array()
                    sendFn(peerAddr, buildPacket(TYPE_SYNC_REQ, 0, recvNext, payload))
                    lastSendTime = System.currentTimeMillis()
                } catch (e: Exception) {
                    log("[SYNC] 发送 REQ 失败: " + e.message)
                    break
                }
                val ok = latch.await(SYNC_TIMEOUT_MS, java.util.concurrent.TimeUnit.MILLISECONDS)
                val reply = synchronized(syncLock) {
                    val r = syncReplies.remove(t1)
                    syncPending.remove(t1)
                    r
                }
                if (ok && reply != null) {
                    samples.add(reply)
                    log("[SYNC] 采样 " + (i + 1) + "/" + SYNC_SAMPLE_COUNT +
                            " RTT=" + reply[0] + "ms offset=" + reply[1] + "ms")
                } else {
                    log("[SYNC] 采样 " + (i + 1) + " 超时")
                }
                Thread.sleep(50)
            }
            if (samples.isEmpty()) {
                log("[SYNC] 无有效采样，放弃")
                return
            }
            val best = samples.minByOrNull { it[0] }!!
            syncOffset = best[1]
            syncRtt = best[0]
            log("[SYNC] 最佳 RTT=" + best[0] + "ms offset=" + best[1] + "ms peerVer=" + peerVer)
            // 发 SYNC_COMMIT
            val deltaMs = SYNC_COMMIT_DELAY_MS
            val tSend = System.currentTimeMillis()
            val tGo = tSend + deltaMs
            commitSeq += 1
            val commitId = commitSeq
            try {
                if (peerVer >= PROTO_VER) {
                    // v2：绝对时刻(responder钟) + commit_id，重传多次
                    val tGoR = tGo + (syncOffset ?: 0L)
                    val payload = ByteBuffer.allocate(16).order(ByteOrder.BIG_ENDIAN)
                        .putLong(tGoR).putLong(commitId).array()
                    for (k in 0 until SYNC_COMMIT_RESEND) {
                        sendFn(peerAddr, buildPacket(TYPE_SYNC_COMMIT, 0, recvNext, payload))
                        lastSendTime = System.currentTimeMillis()
                        Thread.sleep(SYNC_COMMIT_RESEND_INTERVAL_MS)
                    }
                    log("[SYNC] 已发 COMMIT(v2) T_go_I=" + tGo + " T_go_R=" + tGoR +
                            " id=" + commitId + " x" + SYNC_COMMIT_RESEND)
                } else {
                    val payload = ByteBuffer.allocate(8).order(ByteOrder.BIG_ENDIAN)
                        .putLong(deltaMs).array()
                    sendFn(peerAddr, buildPacket(TYPE_SYNC_COMMIT, 0, recvNext, payload))
                    lastSendTime = System.currentTimeMillis()
                    log("[SYNC] 已发 COMMIT(v1) T_go=" + tGo + "（" + deltaMs + "ms 后）")
                }
            } catch (e: Exception) {
                log("[SYNC] 发送 COMMIT 失败: " + e.message)
                return
            }
            try { onSyncReady?.invoke(tGo) } catch (_: Exception) {}
        } catch (e: Exception) {
            log("[SYNC] 异常: " + e.message)
        } finally {
            synchronized(syncLock) { syncInProgress = false }
        }
    }

    /** 对端是否已判定失联（供 RoomManager 保活循环检查）。 */
    fun isDead(): Boolean {
        if (closed) return true
        if (peerDeadFired) return true
        return (System.currentTimeMillis() - lastRecvTime) > PEER_DEAD_TIMEOUT_MS
    }

    /** 由上层全局接收循环调用，传入来自本对端的 UDP 数据。 */
    fun onPacket(data: ByteArray) {
        if (data.size < HEADER_LEN || closed) return
        val bb = ByteBuffer.wrap(data).order(ByteOrder.BIG_ENDIAN)
        val type = bb.get().toInt() and 0xFF
        val seq = bb.getInt()
        val ack = bb.getInt()
        val len = bb.getShort().toInt() and 0xFFFF
        bb.get()
        if (len > data.size - HEADER_LEN) return
        val payload = ByteArray(len)
        bb.get(payload, 0, len)
        // P0：记录对端最后活动时间
        lastRecvTime = System.currentTimeMillis()

        if (ack > 0) {
            synchronized(sendLock) {
                var advanced = 0
                val it = sendQueue.entries.iterator()
                while (it.hasNext()) {
                    if (it.next().key < ack) { it.remove(); advanced++ }
                }
                // AIMD 加性增：有确认推进 → 窗口 +advanced，封顶 WINDOW_MAX
                if (advanced > 0) {
                    window = minOf(WINDOW_MAX, window + advanced)
                }
            }
        }
        when (type) {
            TYPE_KEEPALIVE -> {
                // 收到对端 keepalive：回空 ACK，让对端也更新 lastRecvTime
                // （双向存活检测：A 发 keepalive，B 回 ACK，A 就知道 B 还活着）
                try {
                    sendFn(peerAddr, buildPacket(TYPE_ACK, 0, recvNext, ByteArray(0)))
                    lastSendTime = System.currentTimeMillis()
                } catch (_: Exception) {}
                return
            }
            TYPE_SYNC_REQ -> {
                // P1: 收到 SYNC_REQ(t1[,ver]) → 立即回 SYNC_ACK(t1, t2, t3)
                if (payload.size >= 8) {
                    val bb1 = ByteBuffer.wrap(payload).order(ByteOrder.BIG_ENDIAN)
                    val t1 = bb1.getLong()
                    if (payload.size >= 9) peerVer = payload[8].toInt() and 0xFF
                    val t2 = System.currentTimeMillis()
                    val t3 = System.currentTimeMillis()
                    try {
                        val ackPayload = ByteBuffer.allocate(25).order(ByteOrder.BIG_ENDIAN)
                            .putLong(t1).putLong(t2).putLong(t3).put(PROTO_VER.toByte()).array()
                        sendFn(peerAddr, buildPacket(TYPE_SYNC_ACK, 0, recvNext, ackPayload))
                        lastSendTime = System.currentTimeMillis()
                    } catch (_: Exception) {}
                }
                return
            }
            TYPE_SYNC_ACK -> {
                // P1: 收到 SYNC_ACK(t1, t2, t3) → 算 RTT/offset，唤醒等待者
                if (payload.size >= 24) {
                    val bb1 = ByteBuffer.wrap(payload).order(ByteOrder.BIG_ENDIAN)
                    val t1 = bb1.getLong()
                    val t2 = bb1.getLong()
                    val t3 = bb1.getLong()
                    if (payload.size >= 25) peerVer = payload[24].toInt() and 0xFF
                    val t4 = System.currentTimeMillis()
                    val rtt = (t4 - t1) - (t3 - t2)
                    val offset = ((t2 - t1) + (t3 - t4)) / 2
                    synchronized(syncLock) {
                        val latch = syncPending[t1]
                        if (latch != null) {
                            syncReplies[t1] = longArrayOf(rtt, offset)
                            latch.countDown()
                        }
                    }
                }
                return
            }
            TYPE_SYNC_COMMIT -> {
                if (payload.size >= 16) {
                    // P2 v2: t_go_R 是 responder 钟绝对时刻，commit_id 去重
                    val bb1 = ByteBuffer.wrap(payload).order(ByteOrder.BIG_ENDIAN)
                    val tGoR = bb1.getLong()
                    val commitId = bb1.getLong()
                    synchronized(commitLock) {
                        if (commitId == lastCommitId) return   // 重传包
                        lastCommitId = commitId
                    }
                    log("[SYNC] 收到 COMMIT(v2) T_go=" + tGoR + " id=" + commitId)
                    try { onSyncReady?.invoke(tGoR) } catch (_: Exception) {}
                } else if (payload.size >= 8) {
                    // v1 兼容
                    val bb1 = ByteBuffer.wrap(payload).order(ByteOrder.BIG_ENDIAN)
                    val deltaMs = bb1.getLong()
                    val tGo = System.currentTimeMillis() + deltaMs
                    log("[SYNC] 收到 COMMIT(v1) T_go=" + tGo + "（" + deltaMs + "ms 后）")
                    try { onSyncReady?.invoke(tGo) } catch (_: Exception) {}
                }
                return
            }
            TYPE_PUNCH_ROUND -> {
                // P2: 主导方下发下一轮打洞时刻（responder 钟绝对毫秒）
                if (payload.size >= 16) {
                    val bb1 = ByteBuffer.wrap(payload).order(ByteOrder.BIG_ENDIAN)
                    val roundNo = bb1.getLong()
                    val tGoR = bb1.getLong()
                    log("[打洞] 收到轮次对齐 round=" + roundNo + " T_go=" + tGoR)
                    try { onPunchRound?.invoke(roundNo, tGoR) } catch (_: Exception) {}
                }
                return
            }
            TYPE_TCP_READY -> {
                // P2 #6: 对端 TCP 映射已就绪
                log("[打洞] 收到对端 TCP_READY（可开始打洞）")
                try { onTcpReady?.invoke() } catch (_: Exception) {}
                return
            }
            TYPE_PUNCH_FAIL -> {
                // P2 #8: 对端放弃 TCP
                log("[打洞] 收到对端 PUNCH_FAIL（对端已放弃 TCP）")
                try { onPunchFail?.invoke() } catch (_: Exception) {}
                return
            }
            TYPE_DATA -> {
                var ackSend = recvNext
                synchronized(recvLock) {
                    when {
                        seq == recvNext -> {
                            readyChunks.offer(payload)
                            recvNext += 1
                            while (outOfOrder.containsKey(recvNext)) {
                                readyChunks.offer(outOfOrder.remove(recvNext)!!)
                                recvNext += 1
                            }
                            recvLock.notifyAll()
                            ackSend = recvNext
                        }
                        seq > recvNext && seq < recvNext + WINDOW_MAX -> {
                            outOfOrder[seq] = payload
                            ackSend = recvNext
                        }
                        else -> ackSend = recvNext
                    }
                }
                try { sendFn(peerAddr, buildPacket(TYPE_ACK, 0, ackSend, ByteArray(0))); lastSendTime = System.currentTimeMillis() } catch (_: Exception) {}
            }
            TYPE_FIN -> {
                log("[UDP-RTP] 收到 FIN（peer 关闭连接）")
                synchronized(recvLock) {
                    peerClosed = true
                    recvLock.notifyAll()
                }
            }
        }
    }

    /** P2: 主导方下发下一轮打洞时刻（responder 钟绝对毫秒）。 */
    fun sendPunchRound(roundNo: Long, tGoR: Long): Boolean {
        return try {
            val payload = ByteBuffer.allocate(16).order(ByteOrder.BIG_ENDIAN)
                .putLong(roundNo).putLong(tGoR).array()
            sendFn(peerAddr, buildPacket(TYPE_PUNCH_ROUND, 0, recvNext, payload))
            lastSendTime = System.currentTimeMillis()
            true
        } catch (e: Exception) {
            log("[打洞] 发送 PUNCH_ROUND 失败: " + e.message)
            false
        }
    }

    /** P2 #6: 告知对端本机 TCP 映射已就绪。 */
    fun sendTcpReady(): Boolean {
        return try {
            sendFn(peerAddr, buildPacket(TYPE_TCP_READY, 0, recvNext, ByteArray(0)))
            lastSendTime = System.currentTimeMillis()
            true
        } catch (e: Exception) {
            log("[打洞] 发送 TCP_READY 失败: " + e.message)
            false
        }
    }

    /** P2 #8: 告知对端本机 TCP 打洞彻底失败。 */
    fun sendPunchFail(): Boolean {
        return try {
            sendFn(peerAddr, buildPacket(TYPE_PUNCH_FAIL, 0, recvNext, ByteArray(0)))
            lastSendTime = System.currentTimeMillis()
            true
        } catch (e: Exception) {
            log("[打洞] 发送 PUNCH_FAIL 失败: " + e.message)
            false
        }
    }

    /** P2: 返回最近一次 SYNC offset（responder钟-本机钟），无则 null。 */
    fun getSyncOffset(): Long? = syncOffset

    /** P2 #9: 返回最近一次 SYNC 最小 RTT（毫秒），无则 null。 */
    fun getSyncRtt(): Long? = syncRtt

    /** P2: 返回对端协议版本（默认 1）。 */
    fun getPeerVer(): Int = peerVer

    /** 包装成 InputStream（供 ProtoReader 使用）。 */
    override val inputStream: InputStream = object : InputStream() {
        override fun read(): Int {
            val b = recv(1)
            return if (b.isEmpty()) -1 else b[0].toInt() and 0xFF
        }
        override fun read(b: ByteArray, off: Int, len: Int): Int {
            val data = recv(len)
            if (data.isEmpty()) return -1
            System.arraycopy(data, 0, b, off, data.size)
            return data.size
        }
    }

    /** 包装成 OutputStream（供文件发送使用）。 */
    override val outputStream: OutputStream = object : OutputStream() {
        override fun write(b: Int) { sendall(byteArrayOf(b.toByte())) }
        override fun write(b: ByteArray) { sendall(b) }
        override fun write(b: ByteArray, off: Int, len: Int) {
            sendall(b.copyOfRange(off, off + len))
        }
    }

    // ---------- 内部 ----------

    private fun buildPacket(type: Int, seq: Int, ack: Int, payload: ByteArray): ByteArray {
        val bb = ByteBuffer.allocate(HEADER_LEN + payload.size).order(ByteOrder.BIG_ENDIAN)
        bb.put(type.toByte()); bb.putInt(seq); bb.putInt(ack)
        bb.putShort(payload.size.toShort()); bb.put(0); bb.put(payload)
        return bb.array()
    }

    private fun rtxLoop() {
        while (!closed) {
            Thread.sleep(RTX_INTERVAL_MS)
            val now = System.currentTimeMillis()
            val toResend: List<Pair<Int, ByteArray>>
            synchronized(sendLock) {
                toResend = sendQueue.entries
                    .filter { now - it.value.second > RTO_MS }
                    .map { Pair(it.key, it.value.first) }
            }
            if (toResend.isNotEmpty()) {
                // AIMD 乘性减：出现超时重传（疑似丢包/拥塞）→ 窗口减半
                synchronized(sendLock) {
                    window = maxOf(WINDOW_MIN, window / 2)
                }
            }
            for ((seq, pkt) in toResend) {
                try { sendFn(peerAddr, pkt); lastSendTime = System.currentTimeMillis() } catch (_: Exception) { break }
                synchronized(sendLock) {
                    sendQueue[seq]?.let { sendQueue[seq] = Pair(it.first, now) }
                }
            }
        }
    }

    // ---------- P0: keepalive + 失联检测 ----------

    private fun keepaliveLoop() {
        while (!closed) {
            Thread.sleep(KA_CHECK_INTERVAL_MS)
            if (closed) break
            val now = System.currentTimeMillis()
            // 1) 空闲 → 发 KEEPALIVE 刷新 conntrack
            if (now - lastSendTime >= KEEPALIVE_INTERVAL_MS) {
                if (closed) break   // 双检：sleep 期间可能已被 close
                try {
                    sendFn(peerAddr, buildPacket(TYPE_KEEPALIVE, 0, recvNext, ByteArray(0)))
                    lastSendTime = now
                } catch (_: Exception) {}
            }
            // 2) 失联检测（只触发一次）
            if (!peerDeadFired && now - lastRecvTime > PEER_DEAD_TIMEOUT_MS) {
                peerDeadFired = true
                log("[UDP-RTP] 对端 " + peerAddr + " 失联（" +
                        ((now - lastRecvTime) / 1000) + " 秒无包）")
                try { onPeerDead?.invoke() } catch (_: Exception) {}
            }
        }
    }
}


