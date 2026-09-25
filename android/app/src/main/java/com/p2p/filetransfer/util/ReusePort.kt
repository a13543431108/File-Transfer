package com.p2p.filetransfer.util

import android.os.ParcelFileDescriptor
import android.system.Os
import android.system.OsConstants
import java.net.DatagramSocket
import java.net.InetAddress
import java.net.ServerSocket
import java.net.Socket
import java.util.WeakHashMap

/**
 * 房间模式端口复用工具（v8 最终方案）。
 *
 * 目的：让同一个本地端口 9998 上共存两个出站 socket：
 *   1. 映射观测连接（→ 服务器 3337）
 *   2. 打洞出站连接（→ 对方 9998）
 * Linux/Android 必须 SO_REUSEPORT（Linux 值 = 15）。
 *
 * 关键点（之前版本踩过的坑）：
 *   ① java.net.Socket() 的 fd 是【lazy】的 —— 首次 setReuseAddress / bind /
 *      connect 才创建。因此必须【先调 setReuseAddress】触发 fd 创建。
 *   ② ParcelFileDescriptor.fromSocket(socket) 返回的 PFD 是【临时对象】，
 *      GC 会关闭其 fd。必须用 WeakHashMap 保活到 socket 关闭。
 *   ③ SO_REUSEPORT 通过 android.system.Os.setsockoptInt 设置（公开 API）。
 *   ④ 必须在 bind() 之前设置好 SO_REUSEPORT。
 */
object ReusePort {

    private const val SO_REUSEPORT_LINUX = 15

    /** socket → PFD 保活表（WeakHashMap：socket 被回收时 PFD 一并释放）。 */
    private val pfdRefs = WeakHashMap<Socket, ParcelFileDescriptor>()

    /**
     * 为 socket 开启 SO_REUSEPORT（必须在 bind 前调用）。返回是否成功。
     */
    @Synchronized
    fun enable(socket: Socket, log: (String) -> Unit): Boolean {
        // ① 触发 fd 创建 —— 关键！
        try {
            socket.reuseAddress = true
        } catch (t: Throwable) {
            log("[端口复用] setReuseAddress 失败: " + t.javaClass.simpleName + ": " + t.message)
        }
        // ② 拿 PFD 并【保活】
        val pfd = try {
            ParcelFileDescriptor.fromSocket(socket)
        } catch (t: Throwable) {
            log("[端口复用] PFD.fromSocket 失败: " + t.javaClass.simpleName + ": " + t.message)
            null
        }
        if (pfd == null) {
            log("[端口复用] PFD.fromSocket 返回 null")
            return false
        }
        pfdRefs[socket] = pfd
        // ③ 设置 SO_REUSEPORT
        return try {
            Os.setsockoptInt(pfd.fileDescriptor, OsConstants.SOL_SOCKET, SO_REUSEPORT_LINUX, 1)
            log("[端口复用] SO_REUSEPORT 设置成功")
            true
        } catch (t: Throwable) {
            log("[端口复用] SO_REUSEPORT 设置失败: " +
                    t.javaClass.simpleName + ": " + t.message)
            false
        }
    }

    /**
     * 为 ServerSocket 开启 SO_REUSEPORT（必须在 bind 前调用）。
     *
     * 场景：安卓端要在 9998 上监听，与打洞/映射出站 socket 共存。
     * Android(Linux) 的 SO_REUSEADDR 不允许同端口多活 socket，
     * 必须 SO_REUSEPORT；且 ServerSocket 的 fd 是懒创建的，
     * 需先 setReuseAddress 触发 fd，再反射拿 fd 设 SO_REUSEPORT。
     */
    @Synchronized
    fun enableServerSocket(ss: ServerSocket, log: (String) -> Unit): Boolean {
        try { ss.reuseAddress = true } catch (_: Throwable) {}
        val fd = try {
            serverSocketFd(ss)
        } catch (t: Throwable) {
            log("[端口复用] 取 ServerSocket fd 失败: " + t.javaClass.simpleName + ": " + t.message)
            null
        }
        if (fd == null) {
            log("[端口复用] ServerSocket fd 为 null，跳过 SO_REUSEPORT")
            return false
        }
        return try {
            Os.setsockoptInt(fd, OsConstants.SOL_SOCKET, SO_REUSEPORT_LINUX, 1)
            log("[端口复用] ServerSocket SO_REUSEPORT 设置成功")
            true
        } catch (t: Throwable) {
            log("[端口复用] ServerSocket SO_REUSEPORT 设置失败: " +
                    t.javaClass.simpleName + ": " + t.message)
            false
        }
    }

    /** 反射取 ServerSocket 内部 fd（遍历父类找 fd 字段）。 */
    private fun serverSocketFd(ss: ServerSocket): java.io.FileDescriptor? {
        val implField = ServerSocket::class.java.getDeclaredField("impl")
        implField.isAccessible = true
        val impl = implField.get(ss) ?: return null
        var clazz: Class<*>? = impl.javaClass
        while (clazz != null && clazz != Any::class.java) {
            try {
                val fdField = clazz.getDeclaredField("fd")
                fdField.isAccessible = true
                return fdField.get(impl) as? java.io.FileDescriptor
            } catch (_: NoSuchFieldException) {
                clazz = clazz.superclass
            }
        }
        return null
    }

    /**
     * 用 UDP connect 技巧获取"到目标地址的实际出接口 IP"。
     * UDP connect 不发送数据，仅让内核按路由表选择源 IP。
     *
     * 用途：手机有 WiFi + 蜂窝时，bind(0.0.0.0) 后 connect 可能报
     * EADDRNOTAVAIL（内核无法确定源 IP）。先探测出实际出口 IP，再用它 bind。
     */
    fun getRouteSourceIp(destHost: String, destPort: Int, log: (String) -> Unit): String? {
        return try {
            val s = DatagramSocket()
            try {
                s.connect(InetAddress.getByName(destHost), destPort)
                s.localAddress?.hostAddress
            } finally {
                try { s.close() } catch (_: Throwable) {}
            }
        } catch (t: Throwable) {
            log("[路由] 获取源 IP 失败: " + t.javaClass.simpleName + ": " + t.message)
            null
        }
    }
}
