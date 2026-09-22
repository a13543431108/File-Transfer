package com.p2p.filetransfer.util

import android.content.Context
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities
import android.util.Log
import java.net.DatagramSocket
import java.net.Inet4Address
import java.net.Inet6Address
import java.net.InetAddress
import java.net.NetworkInterface
import java.net.Socket
import java.net.SocketException

/** 本机网络信息获取工具 */
object NetworkUtil {

    private const val TAG = "NetworkUtil"

    /**
     * 缓存的 Wi-Fi [Network] 对象（由 [registerWifiTracker] 维护）。
     * 使用回调方式获取，避免依赖已弃用的 ConnectivityManager.allNetworks（API 31+ 弃用）。
     */
    @Volatile
    private var cachedWifiNetwork: Network? = null

    @Volatile
    private var wifiTracker: ConnectivityManager.NetworkCallback? = null

    /**
     * 注册 Wi-Fi 网络追踪：Wi-Fi 上线时缓存其 [Network] 对象，下线时清空。
     * 幂等：重复调用只会注册一次。
     */
    fun registerWifiTracker(context: Context) {
        if (wifiTracker != null) return
        try {
            val cm = context.getSystemService(Context.CONNECTIVITY_SERVICE) as? ConnectivityManager
                ?: return
            val request = android.net.NetworkRequest.Builder()
                .addTransportType(NetworkCapabilities.TRANSPORT_WIFI)
                .build()
            val cb = object : ConnectivityManager.NetworkCallback() {
                override fun onAvailable(network: Network) {
                    cachedWifiNetwork = network
                    Log.i(TAG, "Wi-Fi 上线: " + network)
                }
                override fun onLost(network: Network) {
                    if (cachedWifiNetwork == network) cachedWifiNetwork = null
                    Log.i(TAG, "Wi-Fi 下线: " + network)
                }
            }
            cm.registerNetworkCallback(request, cb)
            wifiTracker = cb
        } catch (e: Exception) {
            Log.w(TAG, "registerWifiTracker 失败: " + e.message)
        }
    }

    /** 注销 Wi-Fi 追踪 */
    fun unregisterWifiTracker(context: Context) {
        try {
            val cm = context.getSystemService(Context.CONNECTIVITY_SERVICE) as? ConnectivityManager
            wifiTracker?.let { cm?.unregisterNetworkCallback(it) }
        } catch (_: Exception) {
        }
        wifiTracker = null
        cachedWifiNetwork = null
    }

    /**
     * 获取当前 Wi-Fi 的 [Network] 对象；没有 Wi-Fi 时返回 null。
     *
     * 依赖 [registerWifiTracker] 注册的回调缓存值。
     * 若尚未注册回调，回退到"主动查询 activeNetwork"的方式（仅在默认网络为 Wi-Fi 时有效）。
     */
    fun getWifiNetwork(context: Context): Network? {
        cachedWifiNetwork?.let { return it }
        // 回退：如果当前默认网络就是 Wi-Fi，也能拿到
        return try {
            val cm = context.getSystemService(Context.CONNECTIVITY_SERVICE) as? ConnectivityManager
                ?: return null
            val active = cm.activeNetwork ?: return null
            val caps = cm.getNetworkCapabilities(active) ?: return null
            if (caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI)) active else null
        } catch (_: Exception) {
            null
        }
    }

    /**
     * 把当前进程的所有网络流量绑定到 Wi-Fi。
     * 若无 Wi-Fi，则调 bindProcessToNetwork(null) 恢复默认网络。
     *
     * 返回 true 表示成功绑定到 Wi-Fi。
     */
    fun bindProcessToWifi(context: Context): Boolean {
        return try {
            val cm = context.getSystemService(Context.CONNECTIVITY_SERVICE) as? ConnectivityManager
                ?: return false
            val wifi = getWifiNetwork(context)
            val ok = cm.bindProcessToNetwork(wifi)
            Log.i(TAG, "bindProcessToNetwork: " + (if (wifi != null) "Wi-Fi" else "default") + ", ok=" + ok)
            ok && wifi != null
        } catch (e: Exception) {
            Log.w(TAG, "bindProcessToWifi 失败: " + e.message)
            false
        }
    }

    /**
     * 对单个 socket 绑定到 Wi-Fi 网络（后备方案，比 [bindProcessToWifi] 更精细）。
     * 已经 bindProcessToNetwork 的情况下一般不需要再调，但作为双保险。
     */
    fun bindSocketToWifi(context: Context, socket: Socket): Boolean {
        return try {
            val wifi = getWifiNetwork(context) ?: return false
            wifi.bindSocket(socket)
            true
        } catch (e: Exception) {
            Log.d(TAG, "bindSocketToWifi 失败: " + e.message)
            false
        }
    }

    /** UDP 版本 */
    fun bindSocketToWifi(context: Context, socket: DatagramSocket): Boolean {
        return try {
            val wifi = getWifiNetwork(context) ?: return false
            wifi.bindSocket(socket)
            true
        } catch (e: Exception) {
            Log.d(TAG, "bindSocketToWifi(UDP) 失败: " + e.message)
            false
        }
    }

    /** 获取本机所有非回环 IPv4 地址 */
    fun getLocalIPv4List(): List<String> {
        val result = mutableListOf<String>()
        try {
            val ifaces = NetworkInterface.getNetworkInterfaces() ?: return result
            for (iface in ifaces) {
                if (!iface.isUp || iface.isLoopback) continue
                for (addr in iface.inetAddresses) {
                    if (addr is Inet4Address && !addr.isLoopbackAddress) {
                        val host = addr.hostAddress ?: continue
                        if (!host.startsWith("169.254.") || result.isEmpty()) {
                            result.add(host)
                        }
                    }
                }
            }
        } catch (_: SocketException) {
        }
        return result
    }

    /** 获取本机所有非回环 IPv6 地址 */
    fun getLocalIPv6List(): List<String> {
        val result = mutableListOf<String>()
        try {
            val ifaces = NetworkInterface.getNetworkInterfaces() ?: return result
            for (iface in ifaces) {
                if (!iface.isUp || iface.isLoopback) continue
                for (addr in iface.inetAddresses) {
                    if (addr is Inet6Address && !addr.isLoopbackAddress) {
                        val host = addr.hostAddress ?: continue
                        val pure = host.substringBefore('%')
                        if (!pure.equals("::1", ignoreCase = true)) {
                            result.add(pure)
                        }
                    }
                }
            }
        } catch (_: SocketException) {
        }
        return result
    }

    /** 主 IPv4（第一个非回环地址），无则返回 null */
    fun primaryIPv4(): String? = getLocalIPv4List().firstOrNull()

    /** 本机主机名 */
    fun hostname(): String {
        return try {
            InetAddress.getLocalHost().hostName ?: "Android"
        } catch (_: Exception) {
            "Android"
        }
    }

    /**
     * 尽力获取 Wi-Fi MAC 地址。
     *
     * 注意（Android 6.0+）：
     *   · Wi-Fi 默认开启 MAC 随机化，返回的多是随机地址
     *   · Android 10+ 出于隐私，无法再拿到真实 MAC（返回 02:00:00:00:00:00）
     * 因此本方法返回的 MAC **仅作展示参考**，不能用作设备识别主键。
     * 识别主键用 prefs 里的持久化 UUID（device_id）。
     *
     * 需要 ACCESS_WIFI_STATE 权限（已在 Manifest 声明）。
     */
    fun getWifiMacAddress(): String? {
        return try {
            val ifaces = NetworkInterface.getNetworkInterfaces() ?: return null
            for (iface in ifaces) {
                if (!iface.isUp) continue
                // 只看 wlan* 或 eth* 之类的物理网卡
                val name = iface.name ?: continue
                if (!name.startsWith("wlan") && !name.startsWith("eth")) continue
                val macBytes = iface.hardwareAddress ?: continue
                if (macBytes.isEmpty()) continue
                val sb = StringBuilder()
                for ((i, b) in macBytes.withIndex()) {
                    if (i > 0) sb.append(':')
                    sb.append(String.format("%02X", b))
                }
                val mac = sb.toString()
                // Android 10+ 的占位 MAC 视为无效
                if (mac == "02:00:00:00:00:00") continue
                return mac
            }
            null
        } catch (_: Exception) {
            null
        }
    }
}
