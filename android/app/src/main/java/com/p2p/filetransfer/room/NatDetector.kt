package com.p2p.filetransfer.room

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.InetAddress
import java.net.InetSocketAddress
import java.net.SocketTimeoutException

/**
 * NAT 类型探测 —— 与电脑端 rm_detect_nat_type 对应。
 *
 * 原理：向服务器两个不同的 UDP 端口（DEFAULT_SERVER_PORT 与 NAT_PROBE_PORT）
 * 各发一个 nat_probe 探测包，比较服务器观测到的公网映射：
 *   · 相同  -> 锥形 NAT（Cone）
 *   · 不同  -> 对称 NAT（Symmetric）
 *   · 无响应 -> UDP 被封堵
 *
 * 只做诊断，不影响既有信令/打洞流程。
 */
object NatDetector {

    data class Result(
        val type: String,      // cone / symmetric / unknown / no_udp
        val primary: String?,
        val alt: String?
    )

    private const val TIMEOUT_MS = 1500

    suspend fun detect(
        serverHost: String,
        serverPort: Int = RoomConfig.DEFAULT_SERVER_PORT,
        probePort: Int = RoomConfig.NAT_PROBE_PORT,
        log: (String) -> Unit
    ): Result = withContext(Dispatchers.IO) {
        val sock: DatagramSocket = try {
            // 用 DatagramSocket(null) 创建未绑定的 socket，先绑网卡再绑端口。
            DatagramSocket(null).apply { reuseAddress = true }
        } catch (e: Exception) {
            log("[NAT探测] 创建 UDP socket 失败: " + e.message)
            return@withContext Result("unknown", null, null)
        }
        // 与打洞 socket 走同一张网卡（activeNetwork），否则测出的映射
        // 与实际打洞时使用的出口不一致。
        try {
            com.p2p.filetransfer.util.NetworkUtil.bindSocketToActiveNetwork(sock)
        } catch (_: Throwable) {}
        try {
            if (serverHost.contains(":")) {
                sock.bind(InetSocketAddress("::", 0))
            } else {
                sock.bind(InetSocketAddress(0))
            }
        } catch (t: Throwable) {
            log("[NAT探测] socket bind 端口失败: " + t.message)
        }

        try {
            sock.soTimeout = TIMEOUT_MS
            val serverAddr = InetAddress.getByName(serverHost)
            val replies = HashMap<String, String>()

            fun probe(targetPort: Int, tag: String): Boolean {
                return try {
                    val payload = JSONObject()
                        .put("type", RoomConfig.T_NAT_PROBE)
                        .put("ver", RoomConfig.VER)
                        .put("probe", tag)
                        .toString().toByteArray(Charsets.UTF_8)
                    sock.send(DatagramPacket(payload, payload.size, serverAddr, targetPort))
                    true
                } catch (e: Exception) {
                    log("[NAT探测] 向 " + serverHost + ":" + targetPort + " 发包失败: " + e.message)
                    false
                }
            }

            fun collect(tag: String) {
                val deadline = System.currentTimeMillis() + TIMEOUT_MS
                while (System.currentTimeMillis() < deadline && !replies.containsKey(tag)) {
                    try {
                        val buf = ByteArray(2048)
                        val pkt = DatagramPacket(buf, buf.size)
                        sock.receive(pkt)
                        val obj = JSONObject(String(pkt.data, 0, pkt.length, Charsets.UTF_8))
                        if (obj.optString("type") == RoomConfig.T_NAT_PROBE_REPLY) {
                            replies[obj.optString("probe")] = obj.optString("pub")
                        }
                    } catch (_: SocketTimeoutException) {
                        break
                    } catch (_: Exception) {
                        break
                    }
                }
            }

            if (!probe(serverPort, "primary")) {
                return@withContext Result("no_udp", null, null)
            }
            collect("primary")
            if (!probe(probePort, "alt")) {
                return@withContext Result("unknown", replies["primary"], null)
            }
            collect("alt")

            val primary = replies["primary"]?.takeIf { it.isNotEmpty() }
            val alt = replies["alt"]?.takeIf { it.isNotEmpty() }
            val type = when {
                primary == null -> "no_udp"
                alt == null -> "unknown"
                primary == alt -> "cone"
                else -> "symmetric"
            }
            Result(type, primary, alt)
        } finally {
            try { sock.close() } catch (_: Exception) {}
        }
    }

    fun typeName(type: String): String = when (type) {
        "cone" -> "锥形 NAT（Cone）"
        "symmetric" -> "对称 NAT（Symmetric）"
        "no_udp" -> "UDP 被封堵（无法探测）"
        else -> "未知（仅收到一路回应）"
    }
}
