package com.p2p.filetransfer.room

import android.util.Log
import com.p2p.filetransfer.util.SocketOptimizer
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import org.json.JSONObject
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.InetAddress
import java.net.InetSocketAddress
import java.net.Socket
import java.nio.ByteBuffer

/**
 * 信令客户端 —— 与电脑端 net/signaling.py 对应。
 *
 * 单一职责：与信令服务器做 UDP JSON 通信 + 建立 TCP 映射观测连接。
 * 不做：打洞（HolePuncher）、房间状态管理（RoomManager）、文件传输。
 *
 * 回调在协程中触发，调用方需自行保证线程安全。
 */
class SignalingClient(
    private val serverHost: String,
    private val serverPort: Int,
    private val room: String,
    private val name: String,
    private val tcpPort: Int,
    private val lanIps: List<String>,
    private val deviceId: String,
    private val punchLocalPort: Int,
    private val scope: CoroutineScope,
    private val log: (String) -> Unit,
    private val onJoined: (List<RoomMember>) -> Unit,
    private val onMemberJoin: (RoomMember) -> Unit,
    private val onMemberLeave: (String) -> Unit,
    private val onPunchGo: (PunchGo) -> Unit,
    private val onError: (String) -> Unit,
    private val serverTcpPort: Int = RoomConfig.DEFAULT_SERVER_TCP_PORT,
    /** 身份复用：网络切换重建时传入上次 myId，服务器复用（不换 id）。 */
    private val reuseId: String? = null,
    /** 房间密码（可选）：空 = 开放房间。仅存内存，随 join 发送，重建时重发。 */
    private val password: String = ""
) {
    @Volatile var myId: String? = null
        private set

    /**
     * 房间级拒绝码（服务器明确回了 error）：NEED_PASSWORD / BAD_PASSWORD /
     * ROOM_OPEN / ROOM_FULL 等。非空表示"服务器可达但拒绝加入"，
     * 区别于"超时无响应"（服务器不可达）。供 start() 区分错误提示。
     */
    @Volatile var lastJoinError: String? = null
        private set

    private var udp: DatagramSocket? = null
    private var mapSocket: Socket? = null
    private var recvJob: Job? = null
    private var hbJob: Job? = null
    @Volatile private var running = false

    // UDP 打洞
    private var udpHoleSock: DatagramSocket? = null
    private val udpHoleJobs = HashMap<String, Job>()   // peerId -> 发送任务
    private val holeTargets = HashMap<String, String>() // "ip:port" -> peerId（正在打洞的目标）
    private val holeTargetsLock = Object()

    // UDP-RTP 数据分发：{(ip,port): onPacket 回调}
    private val udpRtpHandlers = HashMap<String, (ByteArray) -> Unit>()
    private val udpRtpLock = Object()
    @Volatile private var udpRecvRunning = false
    private var udpRecvJob: Job? = null

    /** UDP 打洞成功回调(peerId, UdpReliableSocket) —— 由 RoomManager 注入。 */
    @Volatile var onUdpHoleReady: ((String, UdpReliableSocket) -> Unit)? = null

    /** P0：UDP-RTP 对端失联回调(peerId)。 */
    @Volatile var onUdpPeerDead: ((String) -> Unit)? = null

    /** P1：SYNC 完成回调(peerId, tGo_local_ms)。 */
    @Volatile var onUdpSyncReady: ((String, Long) -> Unit)? = null

    /** P2 #6: TCP 映射观测连接建立成功回调。 */
    @Volatile var onMappingReady: (() -> Unit)? = null
    @Volatile private var lastUdpSendErrLog = 0L

    private var joinedMembers: List<RoomMember> = emptyList()

    /** 启动：加入房间 → 建 TCP 映射连接 → 开收包/心跳。
     *  返回 true 表示成功加入；false 表示连接失败。 */
    suspend fun start(): Boolean {
        if (running) return false
        running = true
        // 按服务器地址族自适应（支持 IPv6 服务器）
        // 关键：用 DatagramSocket(null) 创建【未绑定】的 socket，
        // 才能先 bindSocketToActiveNetwork 再 bind 端口。
        // DatagramSocket() 会自动 bind，导致后续 bindSocket 报 already bound。
        udp = DatagramSocket(null).apply { reuseAddress = true }
        // 公网信令 socket 必须走 activeNetwork（能上网的那张网卡）。
        // 进程级 bindProcessToNetwork(WiFi) 会把 socket 拖到校园网 WiFi，
        // 而校园网到公网无路由，会导致信令超时。
        try {
            val ok = com.p2p.filetransfer.util.NetworkUtil.bindSocketToActiveNetwork(udp!!)
            log("[信令] UDP 信令 socket 绑定 activeNetwork=" + ok)
        } catch (_: Throwable) {}
        // 再绑端口（通配地址；路由由 activeNetwork 决定）
        try {
            if (serverHost.contains(":")) {
                udp?.bind(InetSocketAddress("::", 0))
            } else {
                udp?.bind(InetSocketAddress(0))
            }
        } catch (t: Throwable) {
            log("[信令] UDP socket bind 端口失败: " + t.message)
        }
        udp?.soTimeout = RoomConfig.RECV_SO_TIMEOUT_MS

        lastJoinError = null
        sendJoin()
        if (!waitJoined()) {
            // 未能加入房间：明确报错并停止，绝不打印"已加入"误导用户
            running = false
            val rej = lastJoinError
            if (rej != null) {
                // 服务器可达但明确拒绝（密码错/房间满等）。错误详情已由
                // onError 上报，这里【不再】误报"无法连接"，也【不再】发
                // CONNECT_FAILED（那会让 UI 提示去查网络，误导用户）。
                val hint = when (rej) {
                    "NEED_PASSWORD" -> "该房间需要密码，请填写房间密码后重试"
                    "BAD_PASSWORD" -> "房间密码错误，请检查后重试"
                    "ROOM_OPEN" -> "该房间是开放房间（无密码），请清空密码栏后重试"
                    "ROOM_FULL" -> "房间人数已满"
                    else -> "服务器拒绝了加入请求"
                }
                log("[信令] 加入房间被拒绝（" + rej + "）：" + hint)
            } else {
                log("[信令] 无法连接信令服务器 " + serverHost + ":" + serverPort +
                        "（10 秒内无响应）。请检查：服务器是否已启动、地址是否正确、" +
                        "防火墙是否放行 UDP " + serverPort + "。" +
                        "提示：若服务器与本机在同一台机器/同一局域网，请填局域网 IP" +
                        "（如服务器的局域网地址），不要填公网 IP" +
                        "（多数路由器不支持从内网访问自己的公网 IP）。")
                onError("CONNECT_FAILED")
            }
            try { udp?.close() } catch (_: Exception) {}
            return false
        }

        // NTP 校时：必须在 recvLoop 启动之前（syncTime 会独占收 UDP）
        try {
            syncTime()
        } catch (t: Throwable) {
            log("[校时] 异常: " + t.javaClass.simpleName + ": " + t.message)
        }

        // 关键：立即启动 recvLoop/hbLoop。若先做 openMapping（网络差时
        // 每次 connect 5s 超时 × 3 次 ≈ 15s），这段期间收不到服务器的
        // punch_go / member_join，会导致两端打洞时刻错开十几秒、必然失败。
        recvJob = scope.launch(Dispatchers.IO) { recvLoop() }
        hbJob = scope.launch(Dispatchers.IO) { hbLoop() }
        onJoined(joinedMembers)
        log("[信令] 已加入房间 " + room + "，我的ID=" + myId)
        scope.launch(Dispatchers.IO) { runNatProbe() }
        // 打开 UDP 打洞专用 socket + 向服务器登记公网映射
        openUdpHoleSocket()

        // openMapping 放后台：即使失败也不阻塞信令；成功后主动对所有已加入
        // 成员重新 requestPunch，让服务器用刚登记的有效 pub_tcp 下发 punch_go。
        scope.launch(Dispatchers.IO) {
            try {
                openMapping()
            } catch (t: Throwable) {
                log("[信令] openMapping 异常: " + t.javaClass.simpleName + ": " + t.message)
                return@launch
            }
            delay(200)
            for (m in joinedMembers) {
                try { requestPunch(m.id) } catch (_: Exception) {}
            }
        }
        return true
    }

    /**
     * 打开 UDP 打洞专用 socket（本地 9996），向服务器登记公网映射。
     *
     * 服务器从 recvfrom 的 addr 记录本客户端的 UDP 公网映射，punch_go 时
     * 下发给对端作为打洞目标。
     */
    private fun openUdpHoleSocket() {
        try {
            log("[UDP打洞] ===== 打开 UDP 打洞 socket =====")
            val s = DatagramSocket(null).apply { reuseAddress = true }
            // 不绑特定网络：UDP 打洞的源 IP 由内核按当前路由自动选。
            // 若绑定 activeNetwork，网络变化后旧网络失效会导致 ENETUNREACH。
            s.bind(InetSocketAddress(RoomConfig.UDP_HOLE_PORT))
            log("[UDP打洞] bind 本地端口 " + RoomConfig.UDP_HOLE_PORT +
                    " 成功，localSocket=" + s.localSocketAddress)
            s.soTimeout = 1000
            udpHoleSock = s
            // 关键：必须【从 udpHoleSock 本身】发 hello，服务器 recvfrom 看到的
            // 源端口才是 9996 的公网映射。用 send()（信令 socket）发则记录错误。
            val hello = JSONObject().put("type", RoomConfig.T_UDP_HELLO)
                .put("ver", RoomConfig.VER).put("id", myId)
                .toString().toByteArray(Charsets.UTF_8)
            s.send(DatagramPacket(hello, hello.size,
                InetAddress.getByName(serverHost), serverPort))
            log("[UDP打洞] socket 绑定本地 " + RoomConfig.UDP_HOLE_PORT + "，已向服务器登记映射")
            // 关键：立即启动【全局接收循环】。所有 UDP 包（探测回复 + RTP 数据）
            // 都由它统一读取，避免多个打洞任务各自 recv 互相抢包。
            if (!udpRecvRunning) {
                udpRecvRunning = true
                udpRecvJob = scope.launch(Dispatchers.IO) { udpGlobalRecvLoop() }
            }
            // 立即重新 requestPunch：让服务器用刚登记的公网映射（含 pub_udp）
            // 重新下发 punch_go 给双方
            for (m in joinedMembers) {
                try { requestPunch(m.id) } catch (_: Exception) {}
            }
        } catch (t: Throwable) {
            log("[UDP打洞] socket 打开失败: " + t.javaClass.simpleName + ": " + t.message)
            udpHoleSock = null
        }
    }

    /** 向指定对端地址发送一个 UDP 包（UDP-RTP 发送入口）。 */
    fun sendUdpTo(addr: java.net.InetSocketAddress, data: ByteArray) {
        try {
            udpHoleSock?.send(DatagramPacket(data, data.size, addr))
        } catch (e: Exception) {
            // 不频繁刷日志（ENETUNREACH 会连续报几百次），只在偶尔打一条
            val now = System.currentTimeMillis()
            if (now - lastUdpSendErrLog > 3000) {
                lastUdpSendErrLog = now
                log("[UDP-RTP] sendUdpTo 失败: " + e.message)
            }
        }
    }

    /** 注册某对端地址的 RTP 处理回调。 */
    fun registerUdpRtp(addrKey: String, handler: (ByteArray) -> Unit) {
        synchronized(udpRtpLock) { udpRtpHandlers[addrKey] = handler }
    }

    /** 移除某对端地址的 RTP 处理回调。 */
    fun unregisterUdpRtp(addrKey: String) {
        synchronized(udpRtpLock) { udpRtpHandlers.remove(addrKey) }
    }

    /** 全局 UDP 接收循环：从 udpHoleSock 收包，按源地址分发。 */
    private fun udpGlobalRecvLoop() {
        log("[UDP-RTP] 全局接收循环启动")
        val buf = ByteArray(2048)
        while (udpRecvRunning) {
            val sock = udpHoleSock ?: break
            try {
                sock.soTimeout = 500
                val pkt = DatagramPacket(buf, buf.size)
                sock.receive(pkt)
                if (pkt.length <= 0) continue
                val data = pkt.data.copyOfRange(0, pkt.length)
                val srcKey = (pkt.address?.hostAddress ?: "") + ":" + pkt.port
                // 1) 打洞探测包：若来自正在打洞的目标，则判定打通
                if (data.size == 12 && String(data, Charsets.UTF_8) == "P2P_UDP_HOLE") {
                    val peerId = synchronized(holeTargetsLock) { holeTargets[srcKey] }
                    if (peerId != null) {
                        // 避免重复建立
                        val already = synchronized(udpRtpLock) { udpRtpHandlers.containsKey(srcKey) }
                        if (!already) {
                            log("[UDP打洞] ★ 成功！收到 " + srcKey + " 的探测包（peer=" + peerId + "）")
                            try {
                                val peerAddr = java.net.InetSocketAddress(pkt.address, pkt.port)
                                val rtp = UdpReliableSocket(peerAddr,
                                    { addr, d -> sendUdpTo(addr, d) }, log,
                                    onPeerDead = { onUdpPeerDead?.invoke(peerId) },
                                    onSyncReady = { tGo -> onUdpSyncReady?.invoke(peerId, tGo) })
                                registerUdpRtp(srcKey, rtp::onPacket)
                                onUdpHoleReady?.invoke(peerId, rtp)
                            } catch (e: Exception) {
                                log("[UDP-RTP] 创建可靠通道失败: " + e.message)
                            }
                        }
                    }
                    continue
                }
                // 2) RTP 数据包：按源地址分发
                val handler = synchronized(udpRtpLock) { udpRtpHandlers[srcKey] }
                if (handler != null) {
                    try { handler(data) } catch (e: Exception) {
                        log("[UDP-RTP] 处理包异常: " + e.message)
                    }
                }
            } catch (_: java.net.SocketTimeoutException) {
            } catch (e: Exception) {
                if (!udpRecvRunning) break
            }
        }
        log("[UDP-RTP] 全局接收循环退出")
    }

    /** 收到 punch_go：向对端 pub_udp 持续发包，尝试打通 UDP 通道。 */
    private fun startUdpHole(peer: RoomMember) {
        val sock = udpHoleSock ?: run {
            log("[UDP打洞] socket 未打开，跳过")
            return
        }
        val pubUdp = peer.pubUdp
        val parsed = parseHostPort(pubUdp)
        if (parsed == null) {
            log("[UDP打洞] 对端 pub_udp 无效（" + pubUdp + "），无法打洞")
            return
        }
        val (ip, port) = parsed
        val key = ip + ":" + port
        // 注册打洞目标：全局接收循环收到该地址的包时判定打通
        synchronized(holeTargetsLock) { holeTargets[key] = peer.id }
        udpHoleJobs[peer.id] = scope.launch(Dispatchers.IO) {
            val endAt = System.currentTimeMillis() + RoomConfig.UDP_PROBE_DURATION_MS
            var sent = 0
            log("[UDP打洞] 开始向 " + key + " 发探测包（" +
                    (RoomConfig.UDP_PROBE_DURATION_MS / 1000.0) + " 秒）")
            val payload = "P2P_UDP_HOLE".toByteArray(Charsets.UTF_8)
            while (System.currentTimeMillis() < endAt && running) {
                try {
                    sock.send(DatagramPacket(payload, payload.size,
                        InetAddress.getByName(ip), port))
                    sent++
                } catch (e: Exception) {
                    log("[UDP打洞] 发送失败: " + e.message)
                    break
                }
                delay(RoomConfig.UDP_PROBE_INTERVAL_MS)
            }
            // 超时清理：若仍未打通，从 targets 移除
            synchronized(holeTargetsLock) {
                if (holeTargets[key] == peer.id) {
                    // 若已注册过 RTP handler 说明打通了，保留；否则算失败
                    val ok = synchronized(udpRtpLock) { udpRtpHandlers.containsKey(key) }
                    if (!ok) {
                        holeTargets.remove(key)
                        log("[UDP打洞] 失败：发了 " + sent + " 个包，未收到 " + key + " 响应")
                    }
                }
            }
        }
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

    /**
     * NTP 式时间同步（单次往返，4 次采样取最小 RTT 的那次）。
     *
     * t1: 客户端发送时刻（本地）
     * t2: 服务器接收时刻（服务器）
     * t3: 客户端收到回包时刻（本地）
     * offset = t2 - (t1 + t3) / 2   （服务器时间 - 本地时间）
     */
    private fun syncTime() {
        var bestRtt: Long = -1
        var bestOffset: Long = 0
        for (i in 0 until 4) {
            try {
                val t1 = System.currentTimeMillis()
                send(JSONObject().put("type", RoomConfig.T_TIME_REQ)
                    .put("ver", RoomConfig.VER).put("t1", t1))
                val deadline = System.currentTimeMillis() + 1000
                var reply: JSONObject? = null
                while (System.currentTimeMillis() < deadline) {
                    val msg = recvOne() ?: continue
                    if (msg.optString("type") == RoomConfig.T_TIME_REPLY &&
                        msg.optLong("t1", -1) == t1) {
                        reply = msg
                        break
                    }
                }
                if (reply == null) continue
                val t3 = System.currentTimeMillis()
                val t2 = reply.optLong("t2", 0)
                val rtt = t3 - t1
                val offset = t2 - (t1 + t3) / 2
                if (bestRtt < 0 || rtt < bestRtt) {
                    bestRtt = rtt
                    bestOffset = offset
                }
                Thread.sleep(50)
            } catch (e: Exception) {
                log("[校时] 采样失败: " + e.message)
                break
            }
        }
        if (bestRtt >= 0) {
            ClockSync.update(bestOffset, bestRtt)
            log("[校时] 与服务器时钟偏差 = " + bestOffset + " ms（最小 RTT " + bestRtt + " ms）")
        } else {
            log("[校时] 未能同步，使用本地时钟（可能影响打洞时刻）")
        }
    }

    /** 加入房间后异步探测本机 NAT 类型并写入日志。 */
    private suspend fun runNatProbe() {
        try {
            val r = NatDetector.detect(serverHost, serverPort,
                RoomConfig.NAT_PROBE_PORT, log)
            log("[NAT探测] 本机 NAT 类型 = " + NatDetector.typeName(r.type))
            log("[NAT探测] 探测 1（UDP " + serverPort + "）观察到: " + r.primary)
            log("[NAT探测] 探测 2（UDP " + RoomConfig.NAT_PROBE_PORT + "）观察到: " + r.alt)
            when (r.type) {
                "symmetric" -> log("[NAT探测] 提示：本机为对称 NAT，TCP 打洞成功率极低。")
                "cone" -> log("[NAT探测] 提示：本机为锥形 NAT，打洞可行性较高。")
            }
        } catch (e: Exception) {
            log("[NAT探测] 异常: " + e.message)
        }
    }

    /**
     * 停止信令客户端。
     * quiet=true：不发 BYE（网络切换重建用）——服务器保留成员条目，
     * 对端不掉线，重建后以同一 id 回归。
     */
    fun stop(quiet: Boolean = false) {
        if (!running) return
        running = false
        if (!quiet) {
            try {
                myId?.let { send(JSONObject().put("type", RoomConfig.T_BYE)
                    .put("ver", RoomConfig.VER).put("id", it)) }
            } catch (_: Exception) {}
        }
        try { mapSocket?.close() } catch (_: Exception) {}
        try { udp?.close() } catch (_: Exception) {}
        udpRecvRunning = false
        try { udpHoleSock?.close() } catch (_: Exception) {}
        for (j in udpHoleJobs.values) { try { j.cancel() } catch (_: Exception) {} }
        udpHoleJobs.clear()
        udpRecvJob?.cancel()
        recvJob?.cancel()
        hbJob?.cancel()
    }

    /** 请求与某成员打洞（服务器会给双方下发 punch_go）。 */
    fun requestPunch(targetId: String) {
        try {
            send(JSONObject().put("type", RoomConfig.T_PUNCH_REQ)
                .put("ver", RoomConfig.VER).put("id", myId).put("target", targetId))
        } catch (_: Exception) {}
    }

    // ---------- 内部 ----------
    private fun send(obj: JSONObject) {
        val data = obj.toString().toByteArray(Charsets.UTF_8)
        val addr = InetAddress.getByName(serverHost)
        udp?.send(DatagramPacket(data, data.size, addr, serverPort))
    }

    private fun sendJoin() {
        val lan = org.json.JSONArray()
        lanIps.forEach { lan.put(it) }
        val msg = JSONObject().put("type", RoomConfig.T_JOIN).put("ver", RoomConfig.VER)
            .put("room", room).put("name", name).put("tcp", tcpPort)
            .put("lan", lan).put("did", deviceId)
        // 网络重建时携带旧 id，服务器复用之（协议向后兼容：旧服务器忽略该字段）
        if (!reuseId.isNullOrEmpty()) {
            msg.put("reuse_id", reuseId)
        }
        // 房间密码：仅在【有密码时】才带该字段；无密码时不发 pwd，
        // 报文与旧版完全一致 → 开放房间零兼容风险。
        if (password.isNotEmpty()) {
            msg.put("pwd", password)
        }
        send(msg)
    }

    /** 等待 joined（或 error），超时重试 join。返回 true 表示成功加入。 */
    private suspend fun waitJoined(): Boolean {
        val deadline = System.currentTimeMillis() + 10_000
        while (System.currentTimeMillis() < deadline && running) {
            val msg = recvOne() ?: run { sendJoin(); null } ?: continue
            when (msg.optString("type")) {
                RoomConfig.T_JOINED -> {
                    myId = msg.optString("id")
                    joinedMembers = parseMembers(msg.optJSONArray("members"))
                    return true
                }
                RoomConfig.T_ERROR -> {
                    val code = msg.optString("code", "UNKNOWN")
                    lastJoinError = code
                    onError(code)
                    running = false
                    return false
                }
            }
        }
        return false
    }

    private fun recvOne(): JSONObject? {
        return try {
            val buf = ByteArray(65535)
            val pkt = DatagramPacket(buf, buf.size)
            udp?.receive(pkt)
            JSONObject(String(pkt.data, 0, pkt.length, Charsets.UTF_8))
        } catch (_: java.net.SocketTimeoutException) {
            null
        } catch (_: Exception) {
            null
        }
    }

    /**
     * 建立 TCP 映射观测连接：从本机 9998 连到服务器 TCP 3337，
     * 让服务器从连接源头看到 (本机公网IP, 9998) —— 这就是对端打洞用的靶子。
     *
     * 用 SocketChannel + SO_REUSEPORT（公开 API），与打洞 socket 共用 9998。
     */
    private suspend fun openMapping() {
        for (attempt in 1..3) {
            var s: Socket? = null
            try {
                // 关键：手机可能同时有 WiFi + 蜂窝。进程被 bindProcessToNetwork(WiFi)，
                // 但 WiFi 若是校园网（到公网无路由），connect 会报 EADDRNOTAVAIL。
                // 因此把这个公网 socket 单独绑到"能上网的那张网卡"(activeNetwork)，
                // 既能走公网，又不影响局域网 UDP 广播走 WiFi。
                // 关键：必须用纯 IPv4 socket。Android 的 Socket() 默认 AF_INET6，
                // bind IPv4 地址后 localSocket 仍是 ::，connect IPv4 服务器会
                // EADDRNOTAVAIL。
                s = com.p2p.filetransfer.util.NetworkUtil.createIpv4Socket()
                log("[映射] ===== 开始建立映射观测连接（第 " + attempt + " 次）=====")
                // 顺序：enable（创建 fd + 设 SO_REUSEPORT）→ 绑 activeNetwork。
                log("[映射] 步骤1: ReusePort.enable（创建 fd + 设 SO_REUSEPORT）")
                com.p2p.filetransfer.util.ReusePort.enable(s, log)
                if (attempt == 1) {
                    // 第 1 次：绑定 activeNetwork（走能上网的网卡）。
                    log("[映射] 步骤2: bindSocketToActiveNetwork")
                    val boundTo = com.p2p.filetransfer.util.NetworkUtil.bindSocketToActiveNetwork(s)
                    log("[映射] 步骤2 结果: 绑 activeNetwork=" + boundTo)
                } else {
                    // 重试：【不绑 activeNetwork】，改用内核默认路由。
                    // 原因：首次失败常因 activeNetwork 在网络切换/抖动时短暂
                    // 无路由（EADDRNOTAVAIL "Address not available"）；此时若
                    // 重试仍绑同一个 stale Network，会反复失败。改用默认路由
                    // 可绕过该问题（只要系统默认路由可用即可连通服务器）。
                    log("[映射] 步骤2: 重试不绑 activeNetwork（改用默认路由）")
                }
                // bind 到通配地址 0.0.0.0：让内核按路由自动选源 IP（经 AP NAT）。
                // 不能绑具体 WiFi IP（如 .225）——它在校园网中不可路由到公网，
                // connect 会 EADDRNOTAVAIL。socket 已是纯 IPv4（createIpv4Socket），
                // 所以 bind(0.0.0.0) 不会再变成 IPv6。
                log("[映射] 步骤3: bind 0.0.0.0:" + punchLocalPort)
                try {
                    s.bind(InetSocketAddress("0.0.0.0", punchLocalPort))
                    log("[映射] 步骤3 结果: bind 成功，localPort=" + s.localPort +
                            " localSocket=" + s.localSocketAddress)
                } catch (be: Throwable) {
                    log("[映射] 步骤3 失败: " + be.javaClass.simpleName + ": " + be.message)
                }
                // connect 前设缓冲区（TCP window scale 需在握手前协商）
                try { SocketOptimizer.applyBufferBeforeConnect(s) } catch (_: Throwable) {}
                log("[映射] 步骤4: connect " + serverHost + ":" + serverTcpPort +
                        "（本地=" + s.localSocketAddress + "）")
                val t0 = System.currentTimeMillis()
                s.connect(InetSocketAddress(serverHost, serverTcpPort), 5000)
                log("[映射] 步骤4 结果: connect 成功，" +
                        (System.currentTimeMillis() - t0) + "ms，对端=" + s.remoteSocketAddress)
                val mid = myId!!.toByteArray(Charsets.UTF_8)
                val hdr = ByteBuffer.allocate(2).putShort(mid.size.toShort()).array()
                s.getOutputStream().write(hdr)
                s.getOutputStream().write(mid)
                s.getOutputStream().flush()
                mapSocket = s
                log("[信令] TCP 映射观测连接已建立（本地端口=" + s.localPort + "）")
                // P2 #6: 通知上层 TCP 映射已就绪，可广播 TCP_READY
                try { onMappingReady?.invoke() } catch (_: Exception) {}
                return
            } catch (e: Exception) {
                try { s?.close() } catch (_: Throwable) {}
                log("[映射] 第 " + attempt + " 次失败: " +
                        e.javaClass.simpleName + ": " + e.message)
                if (attempt < 3) { delay(1000); continue }
                log("[信令] TCP 映射观测连接失败: " + e.message)
            } catch (t: Throwable) {
                try { s?.close() } catch (_: Throwable) {}
                log("[映射] 第 " + attempt + " 次异常: " +
                        t.javaClass.simpleName + ": " + t.message)
                if (attempt < 3) { delay(1000); continue }
                log("[信令] TCP 映射观测连接异常: " + t.javaClass.simpleName + ": " + t.message)
            }
        }
    }

    private suspend fun recvLoop() {
        while (running) {
            val msg = recvOne() ?: continue
            when (msg.optString("type")) {
                RoomConfig.T_MEMBER_JOIN ->
                    parseMember(msg.optJSONObject("member"))?.let { onMemberJoin(it) }
                RoomConfig.T_MEMBER_LEAVE -> onMemberLeave(msg.optString("id"))
                RoomConfig.T_PUNCH_GO -> {
                    val peer = parseMember(msg.optJSONObject("peer"))
                    if (peer != null) {
                        // UDP 打洞探测（与 TCP 打洞并行，独立通道）
                        try { startUdpHole(peer) } catch (_: Throwable) {}
                        onPunchGo(PunchGo(peer, msg.optLong("at", 0)))
                    }
                }
                RoomConfig.T_ERROR -> onError(msg.optString("code", "UNKNOWN"))
            }
        }
    }

    private suspend fun hbLoop() {
        while (running) {
            delay(RoomConfig.HEARTBEAT_INTERVAL_MS)
            if (!running) break
            try {
                send(JSONObject().put("type", RoomConfig.T_HB)
                    .put("ver", RoomConfig.VER).put("id", myId))
            } catch (_: Exception) {}
        }
    }

    private fun parseMembers(arr: org.json.JSONArray?): List<RoomMember> {
        if (arr == null) return emptyList()
        val out = ArrayList<RoomMember>()
        for (i in 0 until arr.length()) {
            parseMember(arr.optJSONObject(i))?.let { out.add(it) }
        }
        return out
    }

    private fun parseMember(o: JSONObject?): RoomMember? {
        if (o == null) return null
        val lanArr = o.optJSONArray("lan")
        val lan = ArrayList<String>()
        if (lanArr != null) for (i in 0 until lanArr.length()) lan.add(lanArr.optString(i))
        // tcp_udp_offset：服务器观测的 TCP-UDP 端口偏移，缺失/null 时为 null
        val offset: Int? = if (o.has("tcp_udp_offset") && !o.isNull("tcp_udp_offset")) {
            o.optInt("tcp_udp_offset")
        } else null
        return RoomMember(
            id = o.optString("id"),
            did = o.optString("did"),
            name = o.optString("name"),
            pub = o.optString("pub"),
            pubTcp = o.optString("pub_tcp"),
            pubUdp = o.optString("pub_udp"),
            lan = lan,
            tcp = o.optInt("tcp", 0),
            tcpUdpOffset = offset
        )
    }
}










