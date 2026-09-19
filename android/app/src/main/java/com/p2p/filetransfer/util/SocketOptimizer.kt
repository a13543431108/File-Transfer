package com.p2p.filetransfer.util

import android.util.Log
import java.net.Socket

/**
 * TCP Socket 优化（禁用 Nagle、启用 KeepAlive、设置大缓冲区）。
 *
 * 注意：Android 内核对 SO_SNDBUF/SO_RCVBUF 有实际上限（通常是 4MB 或更小，
 * 部分 ROM 甚至限制到几百 KB）。设置后通过 getXxxBufferSize() 读取实际值，
 * 便于诊断真实瓶颈。
 */
object SocketOptimizer {

    private const val TAG = "SocketOptimizer"

    /** 期望的 socket 缓冲区大小（16MB，内核可能截断） */
    private const val DESIRED_BUF = 16 * 1024 * 1024

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
    }

    private fun applyBuffer(socket: Socket, isSend: Boolean) {
        val candidates = intArrayOf(DESIRED_BUF, 8 * 1024 * 1024, 4 * 1024 * 1024, 1024 * 1024)
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
