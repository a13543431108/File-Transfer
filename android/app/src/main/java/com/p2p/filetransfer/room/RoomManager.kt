package com.p2p.filetransfer.room

import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.Semaphore
import kotlinx.coroutines.sync.withPermit
import java.net.Socket

/** 单条到某成员的长连接。 */
class RoomConn(
    val member: RoomMember,
    @Volatile var state: RoomConnState = RoomConnState.CONNECTING,
    @Volatile var socket: Socket? = null,
    @Volatile var addr: String? = null
) {
    /** 读写锁（协程 Mutex）：发送时持锁，接收循环空闲时持锁读，串行化收发。 */
    val ioLock = kotlinx.coroutines.sync.Mutex()

    /** B：建连时间戳 —— 保活宽限期内不判死，避免刚连上被误杀。 */
    val createdAt: Long = System.currentTimeMillis()
}

/**
 * 房间管理器 —— 与电脑端 net/room.py 对应。
 *
 * 单一职责：维护房间成员表与“每对设备”的连接状态，调度打洞，对外提供长连接。
 *   - 一进房间即对房间内所有成员发起打洞（全连接）
 *   - 成员加入/离开时增删连接
 *   - 受并发上限约束调度打洞任务
 *   - 打洞成功后持有长连接 socket
 *   - 用 keepalive 保活，失效则重新打洞
 * 不做：信令收发（SignalingClient）、打洞算法（HolePuncher）、文件传输。
 */
class RoomManager(
    private val localTcpPort: Int,
    private val scope: CoroutineScope,
    private val log: (String) -> Unit
) {
    /** 打洞成功、长连接就绪时回调（用于启动接收循环）。 */
    var onSocketReady: ((String, Socket, RoomMember) -> Unit)? = null

    /** UDP-RTP 通道就绪回调(peerId, UdpReliableSocket)。 */
    var onUdpReady: ((String, UdpReliableSocket) -> Unit)? = null

    // UDP-RTP 连接表
    private val udpConns = HashMap<String, UdpReliableSocket>()

    // 自动重建冷却：{peerId: last_rebuild_ts_ms}
    // 避免同一 peer 短时间内重复触发打洞
    private val rebuildCooldown = HashMap<String, Long>()
    private val REBUILD_COOLDOWN_MS = 30_000L

    // P2: 打洞轮次对齐（对端下发 PUNCH_ROUND → 存入，供 punchTask 等待）
    private val punchRoundLock: java.lang.Object = java.lang.Object()
    private val punchRoundEvt = HashMap<String, Pair<Long, Long>>()  // peerId -> (roundNo, tGoR)

    // P2 #6: 本机 TCP 映射是否就绪
    @Volatile private var mappingReady = false

    // 梯度冗余多路径调度：{peerId: PeerPathScheduler}
    private val pathSchedulers = HashMap<String, PeerPathScheduler>()

    // 打洞并发限制：同一 peer 最多 2 个 punchTask 并发
    // （保留适度冗余，又不至于泛滥）
    private val punchActive = HashMap<String, Int>()

    // 连接池：{peerId: 最后活跃时间戳} —— 空闲降频保活用
    private val peerLastActive = HashMap<String, Long>()

    // 接收代际：{peerKey: int} —— 新通道接管时递增，旧接收循环据此退出
    private val recvEpoch = HashMap<String, Int>()

    // B：TCP 保活健壮性 —— 连续失败计数 + 新建连接宽限期
    private val tcpAliveFail = HashMap<String, Int>()
    private val TCP_KEEPALIVE_GRACE_MS = 10_000L
    private val TCP_KEEPALIVE_FAIL_THRESHOLD = 2

    /** 取（或创建）某 peer 的路径调度器。 */
    private fun getPathSched(peerId: String): PeerPathScheduler = synchronized(this) {
        pathSchedulers.getOrPut(peerId) { PeerPathScheduler(peerId, log) }
    }

    /** 供 UI 展示：返回该 peer 的 {pathId: role}。 */
    fun getPathRoles(peerId: String): Map<String, String> {
        val sched = synchronized(this) { pathSchedulers[peerId] } ?: return emptyMap()
        return sched.roles()
    }

    /** P2 #6: TCP 映射就绪 → 向所有已有 UDP 通道广播，后续新建通道也会发。 */
    fun markMappingReady() {
        mappingReady = true
        broadcastTcpReady()
    }

    /** P2 #6: 向所有已有 UDP 通道的对端广播 TCP_READY。 */
    private fun broadcastTcpReady() {
        val items = synchronized(this) { udpConns.toMap() }
        for ((pid, rtp) in items) {
            try {
                rtp.sendTcpReady()
                log("[打洞] 广播 TCP_READY -> peer=" + pid)
            } catch (_: Exception) {}
        }
    }

    // 统一使用 synchronized(this) 保护 members/connections（避免与协程 Mutex 双锁竞争）
    private val members = HashMap<String, RoomMember>()
    private val connections = HashMap<String, RoomConn>()
    private val punchSem = Semaphore(RoomConfig.PUNCH_CONCURRENCY)
    private val puncher = HolePuncher(localTcpPort, log).also {
        // 逐候选诊断日志默认关闭（排查时可置 true）
        it.verbose = false
    }

    private var keepaliveJob: Job? = null
    @Volatile private var running = false

    /** 信令客户端引用（由 Service 注入，供发起打洞请求）。 */
    @Volatile var signaling: SignalingClient? = null

    fun start() {
        running = true
        keepaliveJob = scope.launch(Dispatchers.IO) { keepaliveLoop() }
    }

    fun stop() {
        running = false
        keepaliveJob?.cancel()
        synchronized(this) {
            for (c in connections.values) closeConn(c)
            connections.clear()
            for (r in udpConns.values) {
                try { r.close() } catch (_: Exception) {}
            }
            udpConns.clear()
        }
    }

    /** SignalingClient 的 UDP 打洞成功回调转发。 */
    fun onUdpHoleReady(peerId: String, rtp: UdpReliableSocket) {
        synchronized(this) {
            if (udpConns.containsKey(peerId)) return
            udpConns[peerId] = rtp
        }
        log("[UDP-RTP] 可靠通道就绪 peer=" + peerId + " addr=" + rtp.peer)
        // P2: 挂载轮次对齐回调
        rtp.onPunchRound = { roundNo, tGoR -> onPunchRoundRecv(peerId, roundNo, tGoR) }
        // P2 #6/#8: 挂载 TCP 就绪 / 打洞失败回调
        rtp.onTcpReady = { onPeerTcpReady(peerId) }
        rtp.onPunchFail = { onPeerPunchFail(peerId) }
        // P2 #4: 记录对端 UDP 公网地址，供 TCP 打洞预测端口
        try {
            val pa = rtp.peer
            puncher.setUdpHint(peerId, pa.hostString, pa.port)
        } catch (_: Exception) {}
        // 梯度冗余：注册 UDP 路径
        try { getPathSched(peerId).register("udp:$peerId", "udp") } catch (_: Exception) {}
        try { onUdpReady?.invoke(peerId, rtp) } catch (e: Exception) {
            log("[UDP-RTP] 回调异常: " + e.message)
        }
        // P2 #6: 若本机映射已就绪，立即告知对端
        if (mappingReady) {
            try { rtp.sendTcpReady(); log("[打洞] 新通道通知 TCP_READY -> peer=" + peerId) } catch (_: Exception) {}
        }
        // P1：UDP-RTP 通道就绪后，若是 initiator 则启动 SYNC 以精准 TCP 打洞
        maybeStartSync(peerId, rtp)
    }

    /** P1：initiator 判断（myId < peerId 时才发起 SYNC）。 */
    private fun maybeStartSync(peerId: String, rtp: UdpReliableSocket) {
        val myId = signaling?.myId ?: return
        if (myId >= peerId) {
            // 非 initiator，等对方发 SYNC_REQ
            return
        }
        // 已有 TCP 连接则不需要 SYNC
        synchronized(this) {
            val c = connections[peerId]
            if (c != null && c.state == RoomConnState.CONNECTED) return
        }
        scope.launch(Dispatchers.IO) {
            delay(300)  // 避免与 punch_go 触发的 TCP 打洞并发
            synchronized(this@RoomManager) {
                val c = connections[peerId]
                if (c != null && c.state == RoomConnState.CONNECTED) return@launch
            }
            log("[SYNC] 作为 initiator 向 peer=" + peerId + " 发起 SYNC")
            try { rtp.startSync() } catch (e: Exception) {
                log("[SYNC] startSync 异常: " + e.message)
            }
        }
    }

    /** P1：SYNC 完成 → 到 tGo 时刻精准执行 TCP 同时打开。 */
    fun onUdpSyncReady(peerId: String, tGo: Long) {
        synchronized(this) {
            val c = connections[peerId]
            if (c != null && c.state == RoomConnState.CONNECTED) return
        }
        // P2 #9: 用 SYNC RTT 自适应 connect 超时
        try {
            val rtt = getUdpConn(peerId)?.getSyncRtt()
            if (rtt != null) puncher.setAdaptiveTimeout(rtt)
        } catch (_: Exception) {}
        val peer = synchronized(this) { members[peerId] } ?: return
        scope.launch(Dispatchers.IO) {
            // 等到 tGo 时刻
            val now = System.currentTimeMillis()
            val waitMs = tGo - now
            if (waitMs > 0) delay(minOf(waitMs, 3000L))
            log("[SYNC] 到点执行 TCP 打洞 peer=" + peerId)
            val result = try { puncher.punch(peer, 0) {
                synchronized(this@RoomManager) {
                    val c = connections[peerId]
                    c != null && c.state == RoomConnState.CONNECTED
                }
            } } catch (e: Exception) {
                log("[SYNC] punch 异常: " + e.message); null
            }
            if (result != null) {
                installSocket(peerId, result)
                log("[SYNC] 精准 TCP 打洞成功 peer=" + peerId)
            } else {
                log("[SYNC] 精准 TCP 打洞失败 peer=" + peerId + "，转入多轮重试")
                // P2: 单次精准打洞失败后，回落到轮次对齐的多轮重试
                punchTask(peer, 0)
            }
        }
    }

    /** 取某成员的 UDP-RTP 连接（可能为 null）。 */
    fun getUdpConn(peerId: String): UdpReliableSocket? =
        synchronized(this) { udpConns[peerId] }

    /** 释放某成员的 UDP-RTP 连接。 */
    fun removeUdpConn(peerId: String) {
        val r = synchronized(this) { udpConns.remove(peerId) }
        try { r?.close() } catch (_: Exception) {}
    }

    /** P2 #6: 对端 TCP 就绪 → 立即发起打洞。 */
    private fun onPeerTcpReady(peerId: String) {
        val peer: RoomMember
        synchronized(this) {
            val c = connections[peerId]
            if (c != null && (c.state == RoomConnState.CONNECTING || c.state == RoomConnState.CONNECTED)) return
            peer = members[peerId] ?: return
            connections[peerId] = RoomConn(peer)
        }
        log("[打洞] 对端 TCP 就绪，立即发起打洞 peer=" + peerId)
        scope.launch(Dispatchers.IO) { punchTask(peer, 0) }
    }

    /** P2 #8: 对端放弃 TCP → 本端停止空等，标记失败并回退 UDP。
     *
     *  关键：对端放弃 TCP 说明它那条方向没打通。本端即便 connect 成功，
     *  也可能是【半开连接】——本端 SYN 到了对端，但对端 SYN 没到本端，
     *  对端应用层没有对应 socket 接收。这种连接本端 send() 能进内核缓冲，
     *  对端却收不到，数据会超时/RST。
     *  因此必须【无论本端是否 CONNECTED 都关闭 TCP】，否则会把文件发进
     *  一条单向死连接（现象：协商续传 timeout、通道失败 10053/10054）。
     */
    private fun onPeerPunchFail(peerId: String) {
        // 若本端已 CONNECTED（且经握手确认），对端的 PUNCH_FAIL 只是
        // "它那条腿失败"，不代表本端连接不可用 → 不关闭，避免误杀有效通道。
        synchronized(this) {
            val c = connections[peerId]
            if (c != null && c.state == RoomConnState.CONNECTED) {
                log("[打洞] 收到对端 PUNCH_FAIL，但本端已握手确认连接，忽略")
                return
            }
        }
        log("[打洞] 对端放弃 TCP，peer=" + peerId + " 关闭半开连接并回退 UDP")
        var dead = false
        synchronized(this) {
            val c = connections[peerId]
            if (c != null) {
                closeConn(c)
                c.state = RoomConnState.FAILED
                dead = true
            }
        }
        if (dead) {
            // 梯度冗余：通知调度器 TCP 热备失效，触发角色轮转
            try { pathSchedulers[peerId]?.promoteOnHotFailure() } catch (_: Exception) {}
        }
    }

    /** P2: 收到主导方下发的打洞轮次时刻。 */
    private fun onPunchRoundRecv(peerId: String, roundNo: Long, tGoR: Long) {
        synchronized(punchRoundLock) {
            punchRoundEvt[peerId] = Pair(roundNo, tGoR)
            punchRoundLock.notifyAll()
        }
    }

    /** P2: 等待主导方下发的 >= roundNo 的时刻（responder 钟）。返回 tGoR 或 null。 */
    private fun waitPunchRound(peerId: String, roundNo: Long, timeoutMs: Long): Long? {
        val deadline = System.currentTimeMillis() + timeoutMs
        synchronized(punchRoundLock) {
            while (true) {
                val v = punchRoundEvt[peerId]
                if (v != null && v.first >= roundNo) return v.second
                val remain = deadline - System.currentTimeMillis()
                if (remain <= 0) return null
                try { punchRoundLock.wait(remain) } catch (_: Exception) {}
            }
        }
    }

    /** UDP-RTP 对端失联 → 清理连接 + 自动重建（带冷却）。 */
    /**
     * 请求服务器重新协调打洞（带冷却）。【不销毁任何现存连接】。
     * 供 TCP 死亡等场景使用，避免误杀另一条健康路径（如刚上位的 UDP 热备）。
     */
    fun requestRebuild(peerId: String) {
        val now = System.currentTimeMillis()
        synchronized(this) {
            val last = rebuildCooldown[peerId] ?: 0L
            if (now - last < REBUILD_COOLDOWN_MS) {
                log("[房间] peer=" + peerId + " 重建冷却中（" +
                        ((now - last) / 1000) + " 秒前）")
                return
            }
            rebuildCooldown[peerId] = now
        }
        try {
            log("[房间] peer=" + peerId + " 请求重新打洞")
            signaling?.requestPunch(peerId)
        } catch (e: Exception) {
            log("[房间] peer=" + peerId + " 重新打洞请求失败: " + e.message)
        }
    }

    fun handleUdpPeerDead(peerId: String) {
        // 1) 清理 UDP 连接
        log("[UDP-RTP] peer=" + peerId + " 通道失联，清理并触发重建")
        val r = synchronized(this) { udpConns.remove(peerId) }
        try { r?.close() } catch (_: Exception) {}
        try { pathSchedulers[peerId]?.remove("udp:" + peerId) } catch (_: Exception) {}
        // 2) 触发重建
        requestRebuild(peerId)
    }

    // ---------- 信令回调 ----------
    fun onJoined(list: List<RoomMember>) { for (m in list) addMember(m) }
    fun onMemberJoin(member: RoomMember) { addMember(member) }
    fun onMemberLeave(peerId: String) {
        val rtp: UdpReliableSocket?
        synchronized(this) {
            members.remove(peerId)
            connections.remove(peerId)?.let { closeConn(it) }
            pathSchedulers.remove(peerId)   // 梯度冗余：清理路径调度器
            peerLastActive.remove(peerId)   // 连接池：清理空闲记录
            rebuildCooldown.remove(peerId)  // 清理重建冷却
            recvEpoch.remove(peerId)        // 清理接收代际
            tcpAliveFail.remove(peerId)     // B：清理保活失败计数
            rtp = udpConns.remove(peerId)   // UDP 连接也要清理
        }
        try { rtp?.close() } catch (_: Exception) {}
        log("[房间] 成员离开 " + peerId)
    }

    /** 服务器协调：与对端同时打洞。 */
    fun onPunchGo(peer: RoomMember, atMs: Long) {
        val pid = peer.id
        val need = synchronized(this) {
            members[pid] = peer
            val c = connections[pid]
            if (c != null && (c.state == RoomConnState.CONNECTING || c.state == RoomConnState.CONNECTED)) {
                false
            } else {
                connections[pid] = RoomConn(peer)
                true
            }
        }
        if (need) scope.launch(Dispatchers.IO) { punchTask(peer, atMs) }
    }

    // ---------- 入站连接识别 ----------
    /**
     * 处理 listener accept 到的连接：若匹配某成员的 TCP 公网映射，则接管为房间连接。
     * 返回 true 表示已接管（调用方不应再按普通文件接收处理）。
     */
    fun onInbound(socket: Socket, ip: String, port: Int): Boolean {
        val key = "$ip:$port"
        var readyMember: RoomMember? = null
        var readyPid: String? = null
        synchronized(this) {
            for ((pid, m) in members) {
                if (m.pubTcp.isNotEmpty() && m.pubTcp == key) {
                    readyPid = pid
                    readyMember = m
                    break
                }
            }
        }
        if (readyPid == null || readyMember == null) return false
        // 软握手确认（锁外做，避免阻塞）：成功→验证；失败→仍接管。
        // 与出站一致：不因握手失败而拒绝连接，避免误杀本可用的连接。
        if (punchHandshake(socket, RoomConfig.PUNCH_HANDSHAKE_TIMEOUT_MS)) {
            log("[打洞] 入站连接 " + key + " 握手确认")
        } else {
            log("[打洞] 入站连接 " + key + "（握手无回应，降级信任）")
        }
        synchronized(this) {
            val c = connections[readyPid!!]
            if (c != null && c.state == RoomConnState.CONNECTED && c.socket != null) {
                return true  // 已有连接，忽略重复
            }
            // 覆盖前先关掉旧 socket，避免并发入站/出站竞争导致 fd 泄漏
            if (c != null) closeConn(c)
            val nc = RoomConn(readyMember!!, RoomConnState.CONNECTED, socket, key)
            applyKeepalive(socket)
            connections[readyPid!!] = nc
        }
        // 回调移到锁外，避免在持锁时触发下游逻辑
        log("[房间] 入站连接 " + readyMember!!.name + " <- " + key)
        onSocketReady?.invoke(readyPid!!, socket, readyMember!!)
        return true
    }

    // ---------- 对外查询 ----------
    fun getSocket(peerId: String): Socket? = synchronized(this) {
        val c = connections[peerId]
        if (c != null && c.state == RoomConnState.CONNECTED) c.socket else null
    }

    fun getConn(peerId: String): RoomConn? = synchronized(this) { connections[peerId] }

    /** 新接收循环启动时调用：递增该 peer 的代际号，返回新值。 */
    fun newRecvEpoch(peerKey: String): Int = synchronized(this) {
        val e = (recvEpoch[peerKey] ?: 0) + 1
        recvEpoch[peerKey] = e
        e
    }

    /** 检查某接收循环是否仍是当前代际（旧的应退出）。 */
    fun isCurrentEpoch(peerKey: String, epoch: Int): Boolean = synchronized(this) {
        recvEpoch[peerKey] == epoch
    }

    /** 连接池：标记某 peer 刚被使用（重置空闲计时）。 */
    fun markActive(peerId: String) {
        synchronized(this) { peerLastActive[peerId] = System.currentTimeMillis() }
    }

    /** 连接池：按空闲时长返回保活间隔放大系数（空闲越久越稀疏）。 */
    private fun idleFactor(peerId: String, now: Long): Long {
        val last = synchronized(this) { peerLastActive[peerId] } ?: now
        val idle = now - last
        return when {
            idle < 60_000L -> 1L
            idle < 600_000L -> 4L
            idle < 3_600_000L -> 16L
            else -> 64L
        }
    }

    /**
     * 按路径角色选路发送：优先热备，其次保守暖备，最后宽松暖备。
     * 无调度器时退化为旧行为（TCP 优先，UDP 回退）。
     */
    fun getSendChannel(peerId: String, exclude: Set<Int> = emptySet()): SendChannel? {
        val sched = synchronized(this) { pathSchedulers[peerId] }
        if (sched != null) {
            for (role in listOf("hot", "warm_safe", "warm_loose")) {
                val path = sched.snapshot().firstOrNull { it.role == role } ?: continue
                if (path.proto == "tcp") {
                    val c = synchronized(this) { connections[peerId] }
                    if (c != null && c.state == RoomConnState.CONNECTED && c.socket != null
                        && idOf(c.socket!!) !in exclude) {
                        return SendChannel(
                            com.p2p.filetransfer.protocol.TcpStreamSocket(c.socket!!),
                            c.ioLock, false, role)
                    }
                } else {
                    val rtp = synchronized(this) { udpConns[peerId] }
                    if (rtp != null && idOf(rtp) !in exclude) {
                        return SendChannel(rtp, rtp.ioLock, true, role)
                    }
                }
            }
        }
        // 回退：任意可用通道
        synchronized(this) {
            val c = connections[peerId]
            if (c != null && c.state == RoomConnState.CONNECTED && c.socket != null
                && idOf(c.socket!!) !in exclude) {
                return SendChannel(
                    com.p2p.filetransfer.protocol.TcpStreamSocket(c.socket!!),
                    c.ioLock, false, "?")
            }
            val rtp = udpConns[peerId]
            if (rtp != null && idOf(rtp) !in exclude) {
                return SendChannel(rtp, rtp.ioLock, true, "?")
            }
        }
        return null
    }

    /** 对象身份标识（用于 exclude 比较）。 */
    private fun idOf(o: Any): Int = System.identityHashCode(o)

    fun hasPeer(peerId: String): Boolean = synchronized(this) { connections.containsKey(peerId) }

    fun getPeerDid(peerId: String): String = synchronized(this) {
        members[peerId]?.did?.takeIf { it.isNotEmpty() } ?: peerId
    }

    fun getMembers(): List<RoomMemberStatus> = synchronized(this) {
        members.values.map { m ->
            val c = connections[m.id]
            var st = c?.state ?: RoomConnState.IDLE
            var addr = c?.addr
            // UDP 通道已通即视为在线可用，无需等待 TCP（TCP 可能仍在打洞中/失败）
            if (st != RoomConnState.CONNECTED && udpConns.containsKey(m.id)) {
                st = RoomConnState.CONNECTED
                addr = "udp://" + udpConns[m.id]?.peer
            }
            val roles = pathSchedulers[m.id]?.roles() ?: emptyMap()
            RoomMemberStatus(m, st, addr, roles)
        }
    }

    // ---------- 内部 ----------
    private fun addMember(member: RoomMember) {
        val pid = member.id
        synchronized(this) { members[pid] = member }
        // 请求打洞（服务器会给双方下发 punch_go）
        signaling?.requestPunch(pid)
    }

    /**
     * 多轮重试打洞。
     *
     * 关键：每轮开始前【重读】members 里的最新 peer —— 首次 punch_go 到达时
     * 对端的 pub_tcp 可能还没登记（对端此时还在 syncTime / openMapping），
     * 之后对端完成登记再次 request_punch 会下发携带有效 pub_tcp 的第二次
     * punch_go。若不重读，就会一直用旧的空 pub_tcp 打洞，必然失败。
     */
    private suspend fun punchTask(peer: RoomMember, atMs: Long) {
        val pid = peer.id
        // 打洞并发限制：同一 peer 最多 2 个 punchTask 并发
        synchronized(this) {
            val cnt = punchActive[pid] ?: 0
            if (cnt >= 2) return
            punchActive[pid] = cnt + 1
        }
        try {
        punchSem.withPermit {
            var attempt = 1
            var at = atMs
            while (attempt <= RoomConfig.PUNCH_RETRY && running) {
                // 已连接则退出：其他并发任务已成功，避免无谓尝试
                // （消除 10048 端口冲突 / 无路由等噪音日志，省资源省电）
                val already = synchronized(this) {
                    val c = connections[pid]
                    c != null && c.state == RoomConnState.CONNECTED
                }
                if (already) {
                    log("[打洞] peer=" + pid + " 已由其他任务连接，本任务退出")
                    return
                }
                // 重读最新成员信息
                var latest = synchronized(this) { members[pid] } ?: peer
                // D 修复：pub_tcp 为空说明对端 TCP 公网映射尚未登记，
                // 此时再重试也无候选可用。主动再发一次 punch_req，
                // 让服务器重新下发带 pub_tcp 的 punch_go。
                if (latest.pubTcp.isEmpty()) {
                    try {
                        signaling?.let {
                            log("[打洞] peer=" + pid + " pub_tcp 为空，重新请求映射")
                            it.requestPunch(pid)
                            delay(300)
                            latest = synchronized(this) { members[pid] } ?: latest
                        }
                    } catch (_: Exception) {}
                }
                if (attempt == 1) {
                    log("[打洞] 任务启动 peer=" + pid + " 本地端口=" + localTcpPort)
                } else {
                    log("[打洞] 第 " + attempt + " 轮重试 peer=" + pid +
                            "（pub_tcp=" + latest.pubTcp + "）")
                }
                // 传入 shouldStop：风暴期间若其他任务已连上，立即停止
                val result = puncher.punch(latest, at) {
                    synchronized(this) {
                        val c = connections[pid]
                        c != null && c.state == RoomConnState.CONNECTED
                    }
                }
                if (result != null) {
                    installSocket(pid, result)
                    return
                }
                if (attempt < RoomConfig.PUNCH_RETRY) {
                    val nextRound = (attempt + 1).toLong()
                    val backoffMs = attempt * RoomConfig.PUNCH_RETRY_BACKOFF_S * 1000
                    val rtp = getUdpConn(pid)
                    val myId = signaling?.myId ?: ""
                    if (rtp != null && myId.isNotEmpty() && myId < pid) {
                        // 主导方：约定下一轮本机时刻 → 换算 responder 钟下发
                        val tGoNextI = System.currentTimeMillis() + backoffMs
                        val off = rtp.getSyncOffset() ?: 0L
                        rtp.sendPunchRound(nextRound, tGoNextI + off)
                        log("[打洞] 主导轮次 " + nextRound + "，约定 T_go(本地)=" + tGoNextI)
                        val remain = tGoNextI - System.currentTimeMillis()
                        if (remain > 0) delay(minOf(remain, 3000L))
                        at = 0
                    } else if (rtp != null && myId.isNotEmpty() && myId > pid) {
                        // 响应方：等待主导方下发
                        val tGoR = waitPunchRound(pid, nextRound, backoffMs + 2000)
                        if (tGoR != null) {
                            log("[打洞] 跟随主导轮次 " + nextRound + "，T_go(本地)=" + tGoR)
                            val remain = tGoR - System.currentTimeMillis()
                            if (remain > 0) delay(minOf(remain, 3000L))
                        } else {
                            delay(backoffMs)
                        }
                        at = 0
                    } else {
                        delay(backoffMs)
                        at = 0
                    }
                }
                attempt++
            }
            // 仅在仍未连接时才标记失败：避免把并发任务已建立的 CONNECTED 覆盖
            var markFailed = false
            synchronized(this) {
                val c = connections[pid]
                if (c != null && c.state != RoomConnState.CONNECTED) {
                    c.state = RoomConnState.FAILED
                    markFailed = true
                }
            }
            if (!markFailed) {
                log("[房间] 本任务失败，但 peer=" + pid + " 已由其他任务连接，忽略")
                return
            }
            log("[房间] 连接失败 " + peer.name)
            // P2 #8: 通知对端本端已放弃 TCP
            try { getUdpConn(pid)?.sendPunchFail() } catch (_: Exception) {}
        }
        } finally {
            synchronized(this) {
                val c = (punchActive[pid] ?: 1) - 1
                if (c <= 0) punchActive.remove(pid) else punchActive[pid] = c
            }
        }
    }

    private fun installSocket(peerId: String, result: PunchResult) {
        val member: RoomMember
        synchronized(this) {
            val old = connections[peerId]
            if (old != null && old.state == RoomConnState.CONNECTED && old.socket != null) {
                try { result.socket.close() } catch (_: Exception) {}
                return
            }
            if (old != null) closeConn(old)
            val nc = RoomConn(members[peerId] ?: RoomMember(peerId, "", peerId, "", "", "", emptyList(), 0),
                RoomConnState.CONNECTED, result.socket, result.ip + ":" + result.port)
            connections[peerId] = nc
            member = nc.member
        }
        applyKeepalive(result.socket)
        log("[房间] 已连接 " + member.name + " -> " + result.ip + ":" + result.port)
        // 梯度冗余：注册 TCP 路径
        try { getPathSched(peerId).register("tcp:$peerId", "tcp") } catch (_: Exception) {}
        onSocketReady?.invoke(peerId, result.socket, member)
    }

    private fun applyKeepalive(socket: Socket) {
        try { socket.keepAlive = true } catch (_: Exception) {}
    }

    private fun closeConn(c: RoomConn) {
        try { c.socket?.close() } catch (_: Exception) {}
        c.socket = null
    }

    private suspend fun keepaliveLoop() {
        // 梯度冗余：每条路径按各自软性探测间隔决定何时保活
        val nextDue = HashMap<String, Long>()
        while (running && scope.isActive) {
            delay(1000L)
            if (!running) break
            val nowMs = System.currentTimeMillis()
            // 0) 按路径调度器的软性间隔逐条检查
            val schedSnapshot = synchronized(this) { pathSchedulers.toMap() }
            for ((spid, sched) in schedSnapshot) {
                val factor = idleFactor(spid, nowMs)
                for (p in sched.snapshot()) {
                    // A) 跳过 dead 路径
                    if (p.role == "dead") continue
                    val due = nextDue[p.pathId] ?: 0L
                    if (nowMs < due) continue
                    val ok = probePath(spid, p)
                    if (ok) { p.scheduler.onSuccess(); p.lastSeen = nowMs }
                    else { p.scheduler.onFailure() }
                    // 连接池：空闲降频（间隔 ×factor）
                    nextDue[p.pathId] = nowMs + p.scheduler.currentInterval * 1000L * factor
                }
            }
            // B) 清理已移除路径的 nextDue 条目。
            //    必须在【所有 peer】处理完后统一按【全局存活集】清理——
            //    pathId 形如 "tcp:<peerId>"，并不以 peerId 开头，原来的
            //    startsWith(spid) 判据恒为 false，导致条目永不清理、无限增长。
            val allLive = HashSet<String>()
            synchronized(this) { pathSchedulers.values.toList() }.forEach { s ->
                for (p in s.snapshot()) if (p.role != "dead") allLive.add(p.pathId)
            }
            nextDue.keys.removeAll { it !in allLive }
            // 1) 检查 TCP 长连接
            val snapshot = synchronized(this) {
                connections.filterValues { it.state == RoomConnState.CONNECTED }.toMap()
            }
            for ((pid, c) in snapshot) {
                // B① 宽限期：连接太新，跳过判活（避免刚连上被误杀）
                if (nowMs - c.createdAt < TCP_KEEPALIVE_GRACE_MS) continue
                val s = c.socket
                val bad = s == null || s.isClosed || !s.isConnected
                if (!bad) {
                    tcpAliveFail.remove(pid)
                    continue
                }
                // B② 连续失败阈值：未达阈值先记账，不判死（抗瞬时抖动）
                val failN = (tcpAliveFail[pid] ?: 0) + 1
                if (failN < TCP_KEEPALIVE_FAIL_THRESHOLD) {
                    tcpAliveFail[pid] = failN
                    log("[保活] " + pid + " TCP 探测失败（" + failN + "/" +
                            TCP_KEEPALIVE_FAIL_THRESHOLD + "），暂不判死")
                    continue
                }
                log("[保活] " + pid + " TCP 通道失效（连续 " + failN + " 次），重新打洞")
                tcpAliveFail.remove(pid)
                // 关键：仅当快照里的 c 仍是【当前】连接时才处理。
                // 若期间已被其他任务替换成新连接，说明旧 socket 是被主动
                // 替换掉的，不能误判新连接失效、也不应多余触发重建。
                var dead = false
                synchronized(this) {
                    if (connections[pid] === c) {
                        closeConn(c)
                        c.state = RoomConnState.FAILED
                        dead = true
                    }
                }
                if (!dead) continue
                // 梯度冗余：通知调度器 TCP 热备失效，触发角色轮转
                try { pathSchedulers[pid]?.promoteOnHotFailure() } catch (_: Exception) {}
                // 关键修复：TCP 死亡【不能】走 handleUdpPeerDead ——
                // 那会销毁健康的 UDP 通道（可能是刚上位的热备）。
                // 只请求重建 TCP，绝不触碰其他路径。
                requestRebuild(pid)
            }
            // 2) 检查 UDP-RTP 连接
            val udpSnapshot = synchronized(this) { udpConns.toMap() }
            for ((pid, rtp) in udpSnapshot) {
                try {
                    if (rtp.isDead()) {
                        log("[保活] " + pid + " UDP-RTP 通道失联")
                        try { pathSchedulers[pid]?.remove("udp:$pid") } catch (_: Exception) {}
                        handleUdpPeerDead(pid)
                    }
                } catch (_: Exception) {}
            }
        }
    }

    /** 探测某条路径是否存活（按协议）。 */
    private fun probePath(pid: String, p: PathRecord): Boolean {
        return if (p.proto == "tcp") {
            val s = synchronized(this) { connections[pid]?.socket }
            s != null && !s.isClosed && s.isConnected
        } else {
            val rtp = synchronized(this) { udpConns[pid] }
            rtp != null && !rtp.isDead()
        }
    }
}


