package com.p2p.filetransfer.protocol

import java.io.InputStream
import java.io.OutputStream
import java.net.Socket

/**
 * 通用字节流 socket 接口 —— 让文件传输逻辑不区分 TCP Socket 和 UDP-RTP。
 *
 * 实现：
 *   · TcpStreamSocket：包装 java.net.Socket（打洞直连）
 *   · UdpReliableSocket：可靠 UDP 字节流
 */
interface StreamSocket {
    val inputStream: InputStream
    val outputStream: OutputStream
    var soTimeout: Int
    fun close()
}

/** TCP Socket 适配器。 */
class TcpStreamSocket(private val s: Socket) : StreamSocket {
    override val inputStream: InputStream get() = s.getInputStream()
    override val outputStream: OutputStream get() = s.getOutputStream()
    override var soTimeout: Int
        get() = s.soTimeout
        set(v) { try { s.soTimeout = v } catch (_: Exception) {} }
    override fun close() { try { s.close() } catch (_: Exception) {} }

    /** 底层 Socket（供需要具体 Socket 的场景，如优化调优）。 */
    val socket: Socket get() = s
}
