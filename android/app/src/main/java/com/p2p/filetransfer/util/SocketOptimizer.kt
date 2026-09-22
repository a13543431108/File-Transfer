package com.p2p.filetransfer.util

import android.util.Log
import com.p2p.filetransfer.protocol.Constants
import java.net.InetSocketAddress
import java.net.Socket

/**
 * TCP Socket 优化。
 *
 * 【核心原则】缓冲区设置必须【在 connect 之前】。TCP 窗口缩放在三次握手时按
 * 当时的缓冲区协商，connect 之后再设 SO_RCVBUF 对已建立连接无效，接收窗口会
 * 停留在内核默认值（几百 KB），导致吃不满带宽。
 *
 * 因此本对象把所有"出站连接"的缓冲区设置收敛到唯一入口 [applyBufferBeforeConnect]，
 * 调用方只需：applyBufferBeforeConnect → connect → tuneConnected。
 *
 * 缓冲区大小统一取自 [Constants.SOCKET_BUFFER_SIZE]（16MB）。
 * 注意：Android 内核对 SO_SNDBUF/SO_RCVBUF 有实际上限（通常 4MB 或更小，
 * 部分 ROM 甚至几百 KB）。设置后读取实际值输出日志，便于诊断真实瓶颈。
 */
object SocketOptimizer {

    private const val TAG = "SocketOptimizer"

    /**
     * 创建并连接一个出站 TCP socket —— 所有主动连接的【唯一】入口。
     *
     * 统一完成：connect 前设缓冲区 → connect → connect 后调优。
     * 这样缓冲区设置时机不会再被遗漏（这是之前接收速度上不去的根因）。
     *
     * @param ip                目标地址（IPv4 或 IPv6）
     * @param port              目标端口
     * @param connectTimeoutMs  连接超时（毫秒）
     * @param readTimeoutAfter  连接成功后的读超时（毫秒，0 表示不超时）
     */
    fun connectOutbound(
        ip: String,
        port: Int,
        connectTimeoutMs: Int,
        readTimeoutAfter: Int = 0
    ): Socket {
        val s = Socket()
        s.soTimeout = connectTimeoutMs
        applyBufferBeforeConnect(s)
        s.connect(InetSocketAddress(ip, port), connectTimeoutMs)
        s.soTimeout = readTimeoutAfter
        tuneConnected(s)
        return s
    }

    /**
     * connect 之前调用：设置收发缓冲区 —— 所有主动连接的缓冲区设置入口。
     * 仅供需要自定义 bind 的场景（如打洞 TCP 同时打开）直接调用。
     */
    fun applyBufferBeforeConnect(socket: Socket) {
        applyBuffer(socket, isSend = true)
        applyBuffer(socket, isSend = false)
        logActualBuffers(socket)
    }

    /** connect 之后调用：常规调优（Nagle / KeepAlive）。缓冲区已由上一个方法设好。 */
    fun tuneConnected(socket: Socket) {
        try {
            socket.tcpNoDelay = true
        } catch (e: Exception) {
            Log.w(TAG, "setTcpNoDelay failed", e)
        }
        try {
            socket.keepAlive = true
        } catch (e: Exception) {
            Log.w(TAG, "setKeepAlive failed", e)
        }
    }

    /**
     * 完整优化：用于 accept 得到的连接。
     * 监听 socket 已在 bind 前设过缓冲区，accept 的连接继承其窗口缩放；
     * 此处再设一次收发缓冲 + Nagle/KeepAlive。
     */
    fun optimize(socket: Socket) {
        tuneConnected(socket)
        applyBuffer(socket, isSend = true)
        applyBuffer(socket, isSend = false)
        logActualBuffers(socket)
    }

    private fun applyBuffer(socket: Socket, isSend: Boolean) {
        val desired = Constants.SOCKET_BUFFER_SIZE
        // 逐级下调尝试：16MB → 8MB → 4MB → 1MB
        val candidates = intArrayOf(desired, desired / 2, desired / 4, desired / 16)
        for (size in candidates) {
            try {
                if (isSend) socket.sendBufferSize = size else socket.receiveBufferSize = size
                break
            } catch (_: Exception) {
            }
        }
    }

    /** 把实际生效的缓冲区值输出到日志，便于诊断内核限制。 */
    private fun logActualBuffers(socket: Socket) {
        try {
            Log.i(TAG, "连接 socket SNDBUF=" + socket.sendBufferSize +
                    " RCVBUF=" + socket.receiveBufferSize)
        } catch (_: Exception) {
        }
    }
}
