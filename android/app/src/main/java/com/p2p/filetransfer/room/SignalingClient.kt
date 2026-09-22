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
    private val serverTcpPort: Int = RoomConfig.DEFAULT_SERVER_TCP_PORT
) {
    @Volatile var myId: String? = null
        private set

    private var udp: DatagramSocket? = null
    private var mapSocket: Socket? = null
    private var recvJob: Job? = null
    private var hbJob: Job? = null
    @Volatile private var running = false

    private var joinedMembers: List<RoomMember> = emptyList()

    /** 启动：加入房间 → 建 TCP 映射连接 → 开收包/心跳。
     *  返回 true 表示成功加入；false 表示连接失败。 */
    suspend fun start(): Boolean {
        if (running) return false
        running = true
        // 按服务器地址族自适应（支持 IPv6 服务器）
        udp = if (serverHost.contains(":")) DatagramSocket(null).apply {
            reuseAddress = true
            bind(InetSocketAddress("::", 0))
        } else DatagramSocket()
        udp?.soTimeout = RoomConfig.RECV_SO_TIMEOUT_MS

        sendJoin()
        if (!waitJoined()) {
            // 未能加入房间：明确报错并停止，绝不打印"已加入"误导用户
            running = false
            log("[信令] 无法连接信令服务器 " + serverHost + ":" + serverPort +
                    "（10 秒内无响应）。请检查：服务器是否已启动、地址是否正确、" +
                    "防火墙是否放行 UDP " + serverPort + "。" +
                    "提示：若服务器与本机在同一台机器/同一局域网，请填局域网 IP" +
                    "（如服务器的局域网地址），不要填公网 IP" +
                    "（多数路由器不支持从内网访问自己的公网 IP）。")
            try { udp?.close() } catch (_: Exception) {}
            onError("CONNECT_FAILED")
            return false
        }

        openMapping()
        delay(200)  // 给服务器登记映射留出时间
        onJoined(joinedMembers)

        recvJob = scope.launch(Dispatchers.IO) { recvLoop() }
        hbJob = scope.launch(Dispatchers.IO) { hbLoop() }
        log("[信令] 已加入房间 " + room + "，我的ID=" + myId)
        return true
    }

    fun stop() {
        if (!running) return
        running = false
        try {
            myId?.let { send(JSONObject().put("type", RoomConfig.T_BYE)
                .put("ver", RoomConfig.VER).put("id", it)) }
        } catch (_: Exception) {}
        try { mapSocket?.close() } catch (_: Exception) {}
        try { udp?.close() } catch (_: Exception) {}
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
        send(JSONObject().put("type", RoomConfig.T_JOIN).put("ver", RoomConfig.VER)
            .put("room", room).put("name", name).put("tcp", tcpPort)
            .put("lan", lan).put("did", deviceId))
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
                    onError(msg.optString("code", "UNKNOWN"))
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

    private suspend fun openMapping() {
        for (attempt in 1..3) {
            var s: Socket? = null
            try {
                s = Socket()
                s.reuseAddress = true
                // 按服务器地址族自适应（IPv6 服务器需 bind 到 ::）
                if (serverHost.contains(":")) {
                    s.bind(InetSocketAddress("::", punchLocalPort))
                } else {
                    s.bind(InetSocketAddress(punchLocalPort))
                }
                // 与所有出站连接统一：connect 前设缓冲区
                SocketOptimizer.applyBufferBeforeConnect(s)
                s.connect(InetSocketAddress(serverHost, serverTcpPort), 5000)
                val mid = myId!!.toByteArray(Charsets.UTF_8)
                val hdr = ByteBuffer.allocate(2).putShort(mid.size.toShort()).array()
                s.getOutputStream().write(hdr)
                s.getOutputStream().write(mid)
                s.getOutputStream().flush()
                mapSocket = s
                log("[信令] TCP 映射观测连接已建立（本地端口=" + s.localPort + "）")
                return
            } catch (e: Exception) {
                try { s?.close() } catch (_: Exception) {}
                if (attempt < 3) { delay(500); continue }
                log("[信令] TCP 映射观测连接失败: " + e.message)
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
                    if (peer != null) onPunchGo(PunchGo(peer, msg.optLong("at", 0)))
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
        return RoomMember(
            id = o.optString("id"),
            did = o.optString("did"),
            name = o.optString("name"),
            pub = o.optString("pub"),
            pubTcp = o.optString("pub_tcp"),
            lan = lan,
            tcp = o.optInt("tcp", 0)
        )
    }
}
