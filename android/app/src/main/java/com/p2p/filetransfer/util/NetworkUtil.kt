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
import java.net.InetSocketAddress
import java.net.NetworkInterface
import java.net.Socket
import java.net.SocketException

/** 本机网络信息获取工具 */
object NetworkUtil {

    private const val TAG = "NetworkUtil"

    /** 应用 Context（由 Service.onCreate 注入），用于获取 activeNetwork。 */
    @Volatile private var appContext: Context? = null

    /** 由 Service.onCreate 调用一次，保存 applicationContext。 */
    fun init(context: Context) {
        appContext = context.applicationContext
    }

    /** 缓存的蜂窝 [Network]（由 registerCellularTracker 维护）。 */
    @Volatile private var cachedCellularNetwork: Network? = null
    @Volatile private var cellularTracker: ConnectivityManager.NetworkCallback? = null

    /** 注册蜂窝网络追踪：蜂窝上线时缓存其 Network，下线时清空。 */
    fun registerCellularTracker(context: Context) {
        if (cellularTracker != null) return
        try {
            val cm = context.getSystemService(Context.CONNECTIVITY_SERVICE) as? ConnectivityManager
                ?: return
            val request = android.net.NetworkRequest.Builder()
                .addTransportType(NetworkCapabilities.TRANSPORT_CELLULAR)
                .addCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)
                .build()
            val cb = object : ConnectivityManager.NetworkCallback() {
                override fun onAvailable(network: Network) {
                    cachedCellularNetwork = network
                    Log.i(TAG, "蜂窝上线: " + network)
                }
                override fun onLost(network: Network) {
                    if (cachedCellularNetwork == network) cachedCellularNetwork = null
                    Log.i(TAG, "蜂窝下线: " + network)
                }
            }
            cm.registerNetworkCallback(request, cb)
            cellularTracker = cb
        } catch (e: Exception) {
            Log.w(TAG, "registerCellularTracker 失败: " + e.message)
        }
    }

    /**
     * 获取"能真正到达公网的网络"。
     *
     * 关键：Android 的 activeNetwork 可能报告 WiFi（因为 WiFi 声明 CAPABILITY_INTERNET），
     * 但校园网 WiFi 到公网实际无路由。此时若把 TCP socket 绑到该网络，会 EADDRNOTAVAIL。
     *
     * 策略：
     *   ① 若有蜂窝 → 用蜂窝（能到公网；校园网 WiFi 不能）
     *   ② 否则用 activeNetwork（如纯 WiFi 环境）
     */
    fun getPublicNetwork(): Network? {
        cachedCellularNetwork?.let { return it }
        return getActiveNetwork()
    }

    /**
     * 获取"默认数据网络"（能访问互联网的那张网卡）。
     * 手机同时有 WiFi + 蜂窝时，Android 会把能上网的那张作为 activeNetwork。
     * 若 WiFi 是无互联网（如校园网需认证），activeNetwork 会是蜂窝。
     */
    fun getActiveNetwork(): Network? {
        return try {
            val cm = appContext?.getSystemService(Context.CONNECTIVITY_SERVICE)
                as? ConnectivityManager ?: return null
            cm.activeNetwork
        } catch (_: Exception) {
            null
        }
    }

    /**
     * 把单个 socket 绑定到"默认数据网络"（蜂窝/可联网那张网卡）。
     *
     * 用途：进程级 bindProcessToNetwork(WiFi) 会强制所有 socket 走 WiFi，
     * 但 WiFi 若是校园网（到公网无路由），会导致 connect 报 EADDRNOTAVAIL。
     * socket 级绑定优先于进程级绑定，因此对房间模式的公网 socket 单独绑到
     * activeNetwork（蜂窝），既能走公网，又不影响局域网 UDP 广播走 WiFi。
     */
    fun bindSocketToActiveNetwork(socket: Socket): Boolean {
        return try {
            // 优先蜂窝（能到公网），回退 activeNetwork
            val n = getPublicNetwork() ?: return false
            n.bindSocket(socket)
            true
        } catch (_: Exception) {
            false
        }
    }

    /** UDP 版本 */
    fun bindSocketToActiveNetwork(socket: DatagramSocket): Boolean {
        return try {
            val n = getPublicNetwork() ?: return false
            n.bindSocket(socket)
            true
        } catch (_: Exception) {
            false
        }
    }

    /**
     * 探测"经 activeNetwork 到目标地址的实际源 IP"。
     *
     * 用 UDP connect 技巧（不发数据，仅让内核选路）：
     *   DatagramSocket(null) → bindSocket(activeNetwork) → bind(0)
     *   → connect(dest) → localAddress 就是该网络到目标的源 IP。
     *
     * 为什么需要它：手机有 WiFi + 蜂窝两张网卡时，TCP socket 若 bind(0.0.0.0)
     * 后 connect，内核可能选不出源 IP（EADDRNOTAVAIL）。必须先得到具体源 IP，
     * 再 bind 到它，connect 才能成功。
     */
    fun getRouteSourceIpForActiveNetwork(destHost: String, destPort: Int,
                                         log: (String) -> Unit): String? {
        var s: DatagramSocket? = null
        return try {
            s = DatagramSocket(null)
            val n = getPublicNetwork()
            log("[路由] 探测源 IP：publicNetwork=" + n)
            if (n != null) {
                n.bindSocket(s)
                log("[路由] socket 已绑 publicNetwork")
            } else {
                log("[路由] 无 publicNetwork，退回默认路由")
            }
            s.bind(InetSocketAddress(0))
            log("[路由] 源端口=" + s.localPort + "，准备 connect 目标 " + destHost + ":" + destPort)
            s.connect(InetAddress.getByName(destHost), destPort)
            val ip = s.localAddress?.hostAddress
            log("[路由] 经 activeNetwork 到 " + destHost + " 的源 IP = " + ip +
                    "（localSocket=" + s.localSocketAddress + "）")
            ip
        } catch (t: Throwable) {
            log("[路由] 探测源 IP 失败: " + t.javaClass.simpleName + ": " + t.message)
            null
        } finally {
            try { s?.close() } catch (_: Throwable) {}
        }
    }

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

    /**
     * 获取 Wi-Fi 网卡的【当前】IPv4 地址。
     *
     * 直接从 NetworkInterface 遍历读取，不用 ConnectivityManager.getLinkProperties
     * ——后者会缓存旧的 DHCP 地址（手机重连换 IP 后仍返回旧值，导致 bind 到
     * 不存在的地址、connect 报 EADDRNOTAVAIL）。
     */
    fun getWifiIpv4Address(log: ((String) -> Unit)? = null): String? {
        return try {
            val ifaces = NetworkInterface.getNetworkInterfaces() ?: return null
            var found: String? = null
            for (iface in ifaces) {
                val nm = iface.name ?: continue
                if (!nm.startsWith("wlan")) continue
                val ups = iface.isUp
                val all = iface.inetAddresses.toList()
                    .joinToString(", ") { it.hostAddress ?: "?" }
                log?.invoke("[WiFiIP] 网卡 " + nm + " up=" + ups + " addrs=[" + all + "]")
                if (!ups) continue
                for (addr in iface.inetAddresses) {
                    if (addr is Inet4Address && !addr.isLoopbackAddress) {
                        val ip = addr.hostAddress
                        log?.invoke("[WiFiIP] 选中 " + nm + " -> " + ip)
                        if (found == null) found = ip
                    }
                }
            }
            if (found == null) {
                log?.invoke("[WiFiIP] 未找到任何 wlan IPv4")
            }
            found
        } catch (t: Throwable) {
            log?.invoke("[WiFiIP] 异常: " + t.javaClass.simpleName + ": " + t.message)
            null
        }
    }

    /**
     * 创建【纯 IPv4】家族的 TCP Socket。
     *
     * 为什么需要：Android 的 java.net.Socket() 默认创建 AF_INET6 双栈 socket，
     * 即使 bind(InetSocketAddress("0.0.0.0", port)) 内部仍是 IPv6（localSocket=::）。
     * connect IPv4 服务器会报 EADDRNOTAVAIL（IPv6 地址空间找不到源地址）。
     *
     * 用反射调用 SocketChannel.open(ProtocolFamily)（API 24+）：
     * Kotlin 编译器会把该调用误匹配到 open(SocketAddress) 重载，编译不过，
     * 故用反射绕过。这是 Android 公开 API，不属于隐藏 API。
     */
    fun createIpv4Socket(): Socket {
        // 关键：不能依赖 java.net.preferIPv4Stack —— 它在 Application.onCreate 设置时
        // 往往晚于 InetAddress 初始化，属性只读一次，导致 Socket() 仍是 AF_INET6
        // 双栈（localSocket=::），connect IPv4 目标时报 EADDRNOTAVAIL / NoRouteToHost。
        // 用反射打开真正的 AF_INET SocketChannel（与 createIpv6Socket 对称）。
        return try {
            val m = java.nio.channels.SocketChannel::class.java.getMethod(
                "open", java.net.ProtocolFamily::class.java)
            val ch = m.invoke(null, java.net.StandardProtocolFamily.INET)
                as java.nio.channels.SocketChannel
            ch.socket()
        } catch (t: Throwable) {
            Log.w(TAG, "createIpv4Socket 反射失败，回退普通 Socket: " + t.message)
            Socket()
        }
    }

    /** 创建【纯 IPv6】家族的 TCP Socket（反射同上）。 */
    fun createIpv6Socket(): Socket {
        return try {
            val m = java.nio.channels.SocketChannel::class.java.getMethod(
                "open", java.net.ProtocolFamily::class.java)
            val ch = m.invoke(null, java.net.StandardProtocolFamily.INET6)
                as java.nio.channels.SocketChannel
            ch.socket()
        } catch (t: Throwable) {
            Log.w(TAG, "createIpv6Socket 反射失败，回退普通 Socket: " + t.message)
            Socket()
        }
    }

    /** 本机主机名 */
    fun hostname(): String {
        return try {
            InetAddress.getLocalHost().hostName ?: "Android"
        } catch (_: Exception) {
            "Android"
        }
    }

    /**
     * 【诊断】打印当前网络全景，用于定位"绑错网卡 / 源 IP 选不对"问题。
     *
     * 输出：
     *   ① 所有网络接口（名字、状态、IPv4/IPv6 地址）
     *   ② activeNetwork 的 trans 类型（WiFi/蜂窝/以太网）+ 具体 handle
     *   ③ 进程级绑定的 network
     *   ④ Wi-Fi 网络 handle
     */
    fun dumpNetworkDiagnostics(log: (String) -> Unit) {
        try {
            log("[诊断] ===== 网络全景 =====")
            // ① 所有网络接口
            val ifaces = NetworkInterface.getNetworkInterfaces()
            if (ifaces != null) {
                for (iface in ifaces) {
                    val up = iface.isUp
                    val loop = iface.isLoopback
                    val name = iface.name
                    val addrs = mutableListOf<String>()
                    for (a in iface.inetAddresses) {
                        val h = a.hostAddress ?: continue
                        addrs.add(h + (if (a is Inet4Address) " (v4)" else " (v6)"))
                    }
                    log("[诊断] 网卡 $name up=$up loopback=$loop addrs=" + addrs.joinToString(", "))
                }
            }
            // ② activeNetwork
            val cm = appContext?.getSystemService(Context.CONNECTIVITY_SERVICE)
                as? ConnectivityManager
            if (cm == null) {
                log("[诊断] ConnectivityManager 不可用")
                return
            }
            val active = cm.activeNetwork
            if (active != null) {
                val caps = cm.getNetworkCapabilities(active)
                val transports = mutableListOf<String>()
                if (caps != null) {
                    if (caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI)) transports.add("WiFi")
                    if (caps.hasTransport(NetworkCapabilities.TRANSPORT_CELLULAR)) transports.add("蜂窝")
                    if (caps.hasTransport(NetworkCapabilities.TRANSPORT_ETHERNET)) transports.add("以太网")
                    if (caps.hasTransport(NetworkCapabilities.TRANSPORT_VPN)) transports.add("VPN")
                }
                log("[诊断] activeNetwork=" + active +
                        " 类型=" + transports.joinToString("+") +
                        " 可上网=" + (caps?.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET) ?: false))
            } else {
                log("[诊断] activeNetwork=null")
            }
            // ③ 进程级绑定
            val bound = try { cm.boundNetworkForProcess } catch (_: Throwable) { null }
            log("[诊断] 进程绑定 network=" + bound)
            // ④ Wi-Fi network
            val wifi = getWifiNetwork(appContext!!)
            log("[诊断] Wi-Fi network=" + wifi)
            // ⑤ 网络变化回调是否注册
            log("[诊断] Wi-Fi tracker 已注册=" + (wifiTracker != null))
            log("[诊断] 蜂窝 network=" + cachedCellularNetwork +
                    " tracker=" + (cellularTracker != null))
            log("[诊断] 选中的公网 network=" + getPublicNetwork())
            log("[诊断] =====================")
        } catch (t: Throwable) {
            log("[诊断] 异常: " + t.javaClass.simpleName + ": " + t.message)
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

