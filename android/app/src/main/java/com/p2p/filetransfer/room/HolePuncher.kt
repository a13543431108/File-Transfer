package com.p2p.filetransfer.room

import com.p2p.filetransfer.util.SocketOptimizer
import java.net.InetSocketAddress
import java.net.Socket

/** 打洞成功结果：一条已连接且保持打开的长连接。 */
class PunchResult(val ip: String, val port: Int, val socket: Socket)

/**
 * 打洞器 —— 与电脑端 net/holepunch.py 对应。
 *
 * 单一职责：给定对端的候选地址，尝试建立一条可用的 TCP 直连通道，
 * 成功返回已连接且保持打开的 socket，失败返回 null。
 *   - 优先内网地址（同网段直连成功率最高），用对端 TCP 监听端口
 *   - 其次 TCP 公网映射（pub_tcp，跨 NAT 打洞）
 * 不做：信令交互、连接状态管理（由 RoomManager 负责）。
 */
class HolePuncher(private val localTcpPort: Int, private val log: (String) -> Unit) {

    /** 尝试与 peer 打洞。atMs 为统一打洞时刻；成功返回 PunchResult。 */
    fun punch(peer: RoomMember, atMs: Long): PunchResult? {
        val tcpPort = peer.tcp
        if (tcpPort <= 0) return null

        val candidates = buildCandidates(peer)
        if (candidates.isEmpty()) return null

        waitUntil(atMs)

        for ((ip, port) in candidates) {
            val r = tryConnect(ip, port)
            if (r != null) {
                log("[打洞] 成功 -> " + r.ip + ":" + r.port)
                return r
            }
        }
        log("[打洞] 失败 peer=" + peer.id)
        return null
    }

    private fun buildCandidates(peer: RoomMember): List<Pair<String, Int>> {
        val result = ArrayList<Pair<String, Int>>()
        val seen = HashSet<Pair<String, Int>>()

        // 1) 内网地址 + 对端 TCP 监听端口
        for (ip in peer.lan) {
            if (ip.isNotEmpty()) {
                val key = Pair(ip, peer.tcp)
                if (seen.add(key)) result.add(key)
            }
        }
        // 2) TCP 公网映射（支持 IPv4 "ip:port" 与 IPv6 "[ip]:port"）
        val parsed = parseHostPort(peer.pubTcp)
        if (parsed != null) {
            val key = parsed
            if (seen.add(key)) result.add(key)
        }
        return result
    }

    /**
     * 解析 "ip:port"（IPv4）或 "[ipv6]:port"（IPv6），返回 (ip, port)。
     * IPv6 地址本身含冒号，必须用方括号与端口区分。
     */
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
        // IPv4:port（无方括号）
        val idx = t.lastIndexOf(':')
        if (idx < 0) return null
        val ip = t.substring(0, idx)
        val port = t.substring(idx + 1).toIntOrNull() ?: return null
        // 若 ip 部分仍含冒号，说明是未加方括号的 IPv6（非法格式），拒绝
        if (ip.isEmpty() || ip.contains(':')) return null
        return Pair(ip, port)
    }

    private fun waitUntil(atMs: Long) {
        if (atMs <= 0) return
        val remain = atMs - System.currentTimeMillis()
        if (remain > 0) Thread.sleep(minOf(remain, 2000L))
    }

    private fun tryConnect(ip: String, port: Int): PunchResult? {
        var s: Socket? = null
        try {
            s = Socket()
            s.reuseAddress = true
            // 绑定本地端口，使双方可同时主动打开（TCP simultaneous open）
            try { s.bind(InetSocketAddress(localTcpPort)) } catch (_: Exception) {}
            // 与 TcpFileTransfer 的 createOutboundSocket 保持同一约定：
            // applyBufferBeforeConnect（connect 前设缓冲，窗口缩放在握手时协商）
            // → connect → tuneConnected（connect 后 Nagle/KeepAlive）
            SocketOptimizer.applyBufferBeforeConnect(s)
            s.connect(InetSocketAddress(ip, port), RoomConfig.PUNCH_CONNECT_TIMEOUT_MS)
            s.soTimeout = 0
            SocketOptimizer.tuneConnected(s)
            return PunchResult(ip, port, s)
        } catch (_: Exception) {
            try { s?.close() } catch (_: Exception) {}
            return null
        }
    }
}
