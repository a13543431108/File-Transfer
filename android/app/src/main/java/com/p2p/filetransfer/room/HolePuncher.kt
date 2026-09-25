package com.p2p.filetransfer.room

import com.p2p.filetransfer.util.ReusePort
import com.p2p.filetransfer.util.SocketOptimizer
import java.net.InetSocketAddress
import java.net.Socket

/** 打洞成功结果：一条已连接且保持打开的长连接。 */
class PunchResult(val ip: String, val port: Int, val socket: Socket)

/**
 * TCP 打洞握手确认：双方各发魔数、各收魔数。
 *
 * 返回 true 表示双向可达（收到对端魔数）；false 表示半开或失败。
 * 原理：connect 成功只能证明本端三次握手完成。半开场景下（本端 SYN
 * 到了对端、对端 SYN 未到本端），本端 connect 也会成功，但本端发出的
 * 数据对端收不到。必须做一次应用层往返确认。
 */
fun punchHandshake(socket: Socket, timeoutMs: Int): Boolean {
    return try {
        socket.soTimeout = timeoutMs
        val magic = RoomConfig.PUNCH_HANDSHAKE_MAGIC
        socket.getOutputStream().write(magic)
        socket.getOutputStream().flush()
        val buf = ByteArray(magic.size)
        var got = 0
        while (got < magic.size) {
            val n = socket.getInputStream().read(buf, got, magic.size - got)
            if (n < 0) return false
            got += n
        }
        socket.soTimeout = 0
        buf.contentEquals(magic)
    } catch (e: Exception) {
        false
    }
}

/**
 * 打洞器 —— 与电脑端 net/holepunch.py 对应。
 *
 * 单一职责：给定对端的候选地址，尝试建立一条可用的 TCP 直连通道。
 *   - 优先内网地址（同网段直连成功率最高）
 *   - 其次 TCP 公网映射（pub_tcp，跨 NAT 打洞）
 *
 * 关键：用 ReusePort.enable(socket, log) 在 bind 前设置 SO_REUSEPORT，
 * 让本 socket 能与映射观测 socket 共用本地端口 9998。
 */
class HolePuncher(private val localTcpPort: Int, private val log: (String) -> Unit) {

    // P2 #4: UDP 打洞成功后记录的对方 UDP 公网地址，用于预测 TCP 端口
    private val udpHint = HashMap<String, Pair<String, Int>>()

    // P2 #9: 自适应 connect 超时（由 SYNC RTT 调整）
    @Volatile private var connectTimeoutMs: Int = RoomConfig.PUNCH_CONNECT_TIMEOUT_MS

    // 诊断开关：逐候选失败/超时/尝试日志默认关闭（并行候选本就多数失败）。
    // 成功与最终汇总日志始终输出。
    @Volatile var verbose: Boolean = false

    /** P2 #9: 按 SYNC RTT 调整 connect 超时（3~8 秒）。 */
    fun setAdaptiveTimeout(rttMs: Long) {
        if (rttMs <= 0) return
        // 下限 2000ms（原 3000ms）：配合"重连风暴"模式，单次超时更短让重连更密
        connectTimeoutMs = maxOf(2000, minOf(8000, (rttMs * 4).toInt()))
    }

    /** P2 #4: 记录对端 UDP 公网地址。 */
    @Synchronized
    fun setUdpHint(peerId: String, ip: String, port: Int) {
        udpHint[peerId] = Pair(ip, port)
    }

    /** 尝试与 peer 打洞。atMs 为统一打洞时刻；成功返回 PunchResult。
     *  shouldStop：可选回调，返回 true 表示外部已连接，风暴应立即停止。 */
    fun punch(peer: RoomMember, atMs: Long,
              shouldStop: (() -> Boolean)? = null): PunchResult? {
        val tcpPort = peer.tcp
        if (tcpPort <= 0) {
            log("[打洞] 放弃：对方 tcp 端口为 0 (peer=" + peer.id + ")")
            return null
        }
        val candidates = buildCandidates(peer)
        if (candidates.isEmpty()) {
            log("[打洞] 放弃：候选地址为空 (peer=" + peer.id + ")")
            return null
        }
        if (verbose) {
            log("[打洞] peer=" + peer.id + " 候选=" +
                    candidates.joinToString { it.first + ":" + it.second })
        }

        waitUntil(atMs)

        // 重连风暴：窗口内持续做并行 connect 轮次，每成功一个立即握手确认。
        // 两端持续交叉发 SYN，提升"端口受限锥形 NAT"下同时打开的命中率；
        // 握手确认保证返回的连接是真双向通（区分真通与半开）。
        val deadline = System.currentTimeMillis() + RoomConfig.PUNCH_STORM_DURATION_MS
        var round = 0
        while (System.currentTimeMillis() < deadline) {
            if (shouldStop != null && shouldStop()) {
                if (verbose) log("[打洞] 风暴停止：peer=" + peer.id + " 已由其他任务连接")
                return null
            }
            round++
            val r = connectCandidatesParallel(candidates, peer.id)
            if (r != null) {
                // 软握手确认：成功→已验证；失败→仍返回（降级信任）。
                // 不因握手失败而废弃连接：SO_REUSEPORT 干扰下握手往返常误失败，
                // 据此关闭会把【本可用的连接】误杀，反而降低成功率。
                // 是否正确由"实际发数据"验证（发送失败会自动换路/重建）。
                if (punchHandshake(r.socket, RoomConfig.PUNCH_HANDSHAKE_TIMEOUT_MS)) {
                    log("[打洞] 成功（握手确认）-> " + r.ip + ":" + r.port +
                            " (peer=" + peer.id + ", 第" + round + "轮)")
                } else {
                    log("[打洞] 连接成功（握手无回应，降级信任）-> " + r.ip + ":" + r.port +
                            " (peer=" + peer.id + ", 第" + round + "轮)")
                }
                return r
            }
            Thread.sleep(100)
        }
        if (verbose) log("[打洞] 风暴结束未成功 peer=" + peer.id + "（" + round + " 轮）")
        return null
    }

    /** P2 #3: 并行尝试所有候选，返回首个成功结果。 */
    private fun connectCandidatesParallel(
        candidates: List<Pair<String, Int>>, peerId: String): PunchResult? {
        if (candidates.size == 1) return tryConnect(candidates[0].first, candidates[0].second)
        val win = java.util.concurrent.atomic.AtomicReference<PunchResult?>(null)
        val winnerPicked = java.util.concurrent.atomic.AtomicBoolean(false)
        // 方向 A：限制同时 connect 的候选数（每个候选都绑 9998），
        // 降低 SO_REUSEPORT 入站匹配冲突。
        val connSem = java.util.concurrent.Semaphore(RoomConfig.PUNCH_CANDIDATE_CONCURRENCY)
        val threads = candidates.map { (ip, port) ->
            Thread({
                connSem.acquire()
                try {
                    if (winnerPicked.get()) return@Thread
                    val res = tryConnect(ip, port) ?: return@Thread
                    if (winnerPicked.compareAndSet(false, true)) {
                        win.set(res)
                    } else {
                        try { res.socket.close() } catch (_: Exception) {}
                    }
                } finally {
                    connSem.release()
                }
            }, "Punch-$peerId-$ip").also { it.isDaemon = true; it.start() }
        }
        // 等待赢家（上限 = connect 超时 + 余量）
        val deadline = System.currentTimeMillis() + connectTimeoutMs + 2000
        while (win.get() == null && System.currentTimeMillis() < deadline) {
            Thread.sleep(20)
        }
        threads.forEach { try { it.join(200) } catch (_: Exception) {} }
        return win.get()
    }

    private fun buildCandidates(peer: RoomMember): List<Pair<String, Int>> {
        val result = ArrayList<Pair<String, Int>>()
        val seen = HashSet<Pair<String, Int>>()
        for (ip in peer.lan) {
            if (ip.isNotEmpty()) {
                val key = Pair(ip, peer.tcp)
                if (seen.add(key)) result.add(key)
            }
        }
        val parsed = parseHostPort(peer.pubTcp)
        if (parsed != null) {
            val key = parsed
            if (seen.add(key)) result.add(key)
        }
        // P2 #4: pub_tcp 为空时，用 UDP 打洞端口预测 TCP 候选（仅 IPv4）。
        // 服务器观测的 tcpUdpOffset = 对端 TCP端口 - UDP端口（同一目标 IP），
        // 用于把预测基准从「UDP 端口本身」修正为「UDP 端口 + offset」。
        // offset 缺省为 0，行为与旧版完全一致（纯增益、零风险）。
        if (parsed == null) {
            val hint = udpHint[peer.id]
            if (hint != null && !hint.first.contains(':')) {
                val (hip, hport) = hint
                val off = peer.tcpUdpOffset ?: 0
                val base = hport + off
                for (dp in intArrayOf(0, -1, 1, -2, 2)) {
                    val pp = base + dp
                    if (pp <= 0 || pp > 65535) continue
                    val key = Pair(hip, pp)
                    if (seen.add(key)) result.add(key)
                }
                if (verbose) log("[打洞] 追加 UDP 预测候选 " + hip + ":" + base +
                        "±2 (base=" + base + ", offset=" + off + ", peer=" + peer.id + ")")
            }
        }
        return result
    }

    private fun parseHostPort(s: String): Pair<String, Int>? {
        val t = s.trim()
        if (t.isEmpty()) return null
        if (t.startsWith("[")) {
            val close = t.indexOf(']')
            if (close < 0) return null
            val ip = t.substring(1, close)
            val rest = t.substring(close + 1)
            if (!rest.startsWith(":")) return null
            val port = rest.substring(1).toIntOrNull() ?: return null
            if (ip.isEmpty()) return null
            return Pair(ip, port)
        }
        val idx = t.lastIndexOf(':')
        if (idx < 0) return null
        val ip = t.substring(0, idx)
        val port = t.substring(idx + 1).toIntOrNull() ?: return null
        if (ip.isEmpty() || ip.contains(':')) return null
        return Pair(ip, port)
    }

    private fun waitUntil(atMs: Long) {
        if (atMs <= 0) return
        val localTarget = ClockSync.toLocal(atMs)
        val remain = localTarget - System.currentTimeMillis()
        if (remain > 0) Thread.sleep(minOf(remain, 2000L))
    }

    /**
     * 一次 TCP 打洞尝试。
     * 流程：Socket() → enable(SO_REUSEPORT) → bind(9998) → connect(对端)
     */
    private fun tryConnect(ip: String, port: Int): PunchResult? {
        val isIPv6 = ip.contains(":")
        var s: Socket? = null
        var bindOk = false
        try {
            // 按目标地址族创建对应家族的 socket（避免 IPv6 socket 连 IPv4 目标）
            s = if (isIPv6) {
                com.p2p.filetransfer.util.NetworkUtil.createIpv6Socket()
            } else {
                com.p2p.filetransfer.util.NetworkUtil.createIpv4Socket()
            }
            if (verbose) log("[打洞] ===== 尝试 " + ip + ":" + port + " =====")
            // 顺序：enable（创建 fd + 设 SO_REUSEPORT）→ 绑 activeNetwork。
            ReusePort.enable(s, log)
            val boundHole = com.p2p.filetransfer.util.NetworkUtil.bindSocketToActiveNetwork(s)
            if (verbose) log("[打洞] 绑 activeNetwork=" + boundHole)
            try {
                if (isIPv6) {
                    s.bind(InetSocketAddress("::", localTcpPort))
                } else {
                    // bind 0.0.0.0：让内核选源 IP（经 AP NAT）。
                    // 不能绑具体 WiFi IP——在校园网中不可路由到公网。
                    s.bind(InetSocketAddress("0.0.0.0", localTcpPort))
                }
                bindOk = true
                if (verbose) log("[打洞] bind " + localTcpPort + " 成功（" + (if (isIPv6) "IPv6" else "IPv4") + "）" +
                        " localSocket=" + s.localSocketAddress)
            } catch (be: Throwable) {
                if (verbose) log("[打洞] bind " + localTcpPort + " 失败: " + be.message +
                        "（尝试 " + ip + ":" + port + " 仍继续，走内核随机端口）")
            }
            try { SocketOptimizer.applyBufferBeforeConnect(s) } catch (_: Throwable) {}
            val t0 = System.currentTimeMillis()
            s.connect(InetSocketAddress(ip, port), connectTimeoutMs)
            val dt = System.currentTimeMillis() - t0
            s.soTimeout = 0
            try { SocketOptimizer.tuneConnected(s) } catch (_: Throwable) {}
            log("[打洞] connect 成功 " + ip + ":" + port + "（" + dt + "ms, bind=" + bindOk + "）")
            return PunchResult(ip, port, s)
        } catch (e: Exception) {
            val reason = when (e) {
                is java.net.SocketTimeoutException -> "超时"
                is java.net.ConnectException -> "拒绝/不可达"
                is java.net.NoRouteToHostException -> "无路由"
                else -> e.javaClass.simpleName + ": " + e.message
            }
            if (verbose) log("[打洞] connect 失败 " + ip + ":" + port + " -> " + reason +
                    "（bind=" + bindOk + "）")
            try { s?.close() } catch (_: Throwable) {}
            return null
        } catch (t: Throwable) {
            if (verbose) log("[打洞] connect 异常 " + ip + ":" + port + " -> " +
                    t.javaClass.simpleName + ": " + t.message)
            try { s?.close() } catch (_: Throwable) {}
            return null
        }
    }
}


