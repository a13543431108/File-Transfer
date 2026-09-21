package com.p2p.filetransfer.util

import android.util.Log
import com.p2p.filetransfer.protocol.Constants
import java.net.Socket

/**
 * TCP Socket 优化（禁用 Nagle、启用 KeepAlive、设置大缓冲区）。
 *
 * 缓冲区大小统一取自 [Constants.SOCKET_BUFFER_SIZE]（16MB），
 * 避免多处硬编码 16MB 导致不一致。
 *
 * 注意：Android 内核对 SO_SNDBUF/SO_RCVBUF 有实际上限（通常是 4MB 或更小，
 * 部分 ROM 甚至限制到几百 KB）。设置后通过 getXxxBufferSize() 读取实际值，
 * 便于诊断真实瓶颈。
 */
object SocketOptimizer {

    private const val TAG = "SocketOptimizer"

    fun optimize(socket: Socket) {
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
        // 逐级尝试，直到设置成功；读取实际值便于诊断
        applyBuffer(socket, isSend = true)
        applyBuffer(socket, isSend = false)
        // 诊断：把实际生效值输出到 App 日志，便于观察内核限制
        try {
            Log.i(TAG, "连接 socket SNDBUF=" + socket.sendBufferSize +
                    " RCVBUF=" + socket.receiveBufferSize)
        } catch (_: Exception) {
        }
    }

    private fun applyBuffer(socket: Socket, isSend: Boolean) {
        val desired = Constants.SOCKET_BUFFER_SIZE
        // 逐级下调尝试：16MB → 8MB → 4MB → 1MB
        val candidates = intArrayOf(
            desired,
            desired / 2,
            desired / 4,
            desired / 16
        )
        for (size in candidates) {
            try {
                if (isSend) socket.sendBufferSize = size else socket.receiveBufferSize = size
                break
            } catch (_: Exception) {
            }
        }
        try {
            if (isSend) {
                Log.i(TAG, "SO_SNDBUF 实际 = " + socket.sendBufferSize)
            } else {
                Log.i(TAG, "SO_RCVBUF 实际 = " + socket.receiveBufferSize)
            }
        } catch (_: Exception) {
        }
    }
}
