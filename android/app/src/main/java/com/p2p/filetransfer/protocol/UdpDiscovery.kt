package com.p2p.filetransfer.protocol

import android.util.Log
import com.google.gson.Gson
import com.p2p.filetransfer.repository.DeviceRepository
import com.p2p.filetransfer.util.NetworkUtil
import com.p2p.filetransfer.util.SubnetUtil
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.InetAddress
import java.net.InetSocketAddress

/**
 * UDP 设备发现模块。
 *
 * 端口:
 *   UDP_PORT (9998): 心跳 / 广播 / 互相回复
 *   SCAN_PORT (9997): 主动扫描探测
 *
 * 消息格式(JSON):
 *   {"hostname":"xxx"}                          普通广播
 *   {"hostname":"xxx","reply":true}             发现回复
 *   {"hostname":"xxx","scan_reply":true}        扫描回复
 *   {"heartbeat":true}                           心跳
 *   {"heartbeat":true,"ack":true}                心跳确认
 *   {"hostname":"xxx","discovery":true}          广播搜索请求
 */
class UdpDiscovery(
    private val deviceRepo: DeviceRepository,
    private val log: (String) -> Unit,
    /** 持久化设备 UUID（识别主键，跨重启不变） */
    private val deviceId: String,
    /** 参考 MAC（可为空） */
    private val mac: String,
    private val onNewDevice: (String, String) -> Unit
) {

    private val gson = Gson()

    /**
     * 本机设备名 —— **运行时唯一来源**。
     *
     * 所有出站报文（广播/回复/扫描探测/心跳）都从这里取值；
     * 任何修改（如 setDeviceName）只需改动这里，不再有多处镜像同步。
     * 持久化由上层 P2PFileTransferService 负责（prefs.KEY_DEVICE_NAME）。
     */
    private val _hostname = MutableStateFlow(NetworkUtil.hostname())
    val hostnameFlow: StateFlow<String> = _hostname

    private val hostname: String get() = _hostname.value

    /**
     * 本机 IP 集合（动态获取，避免启动时快照失准——Wi-Fi 后连/换 IP 后旧快照
     * 不再包含本机新 IP，会导致把自己加入设备列表）。设备指纹去重是主防线，
     * 这里作为辅助。
     */
    private val myIps: Set<String>
        get() = (NetworkUtil.getLocalIPv4List() + NetworkUtil.getLocalIPv6List()).toSet()

    /** 供外部（网络变化时）主动读取当前本机 IP，触发一次刷新。 */
    fun currentMyIps(): Set<String> = myIps

    /** 构造出站消息（自动带 hostname / device_id / mac） */
    private fun buildMsg(vararg extra: Pair<String, Any?>): String {
        val m = mutableMapOf<String, Any?>(
            "hostname" to hostname,
            "device_id" to deviceId,
            "mac" to mac,
        )
        for ((k, v) in extra) m[k] = v
        return gson.toJson(m)
    }

    /** 修改本机设备名（唯一入口，立即生效于下次广播/心跳/回复） */
    fun setHostname(newName: String) {
        if (newName.isNotEmpty()) _hostname.value = newName
    }

    /** 读取当前设备名 */
    fun currentHostname(): String = _hostname.value

    private var scope: CoroutineScope? = null
    private var udpListenerJob: Job? = null
    private var udpListenerV6Job: Job? = null
    private var scanListenerJob: Job? = null
    private var scanListenerV6Job: Job? = null
    private var broadcasterJob: Job? = null
    private var autoScanJob: Job? = null
    private var cleanJob: Job? = null

    @Volatile private var running = false
    @Volatile var pausingNetwork = false
    @Volatile var autoScanEnabled = false

    fun start(parentScope: CoroutineScope) {
        if (running) return
        running = true
        scope = parentScope
        udpListenerJob = parentScope.launch(Dispatchers.IO) { udpListenerLoop() }
        udpListenerV6Job = parentScope.launch(Dispatchers.IO) { udpListenerV6Loop() }
        scanListenerJob = parentScope.launch(Dispatchers.IO) { scanListenerLoop() }
        scanListenerV6Job = parentScope.launch(Dispatchers.IO) { scanListenerV6Loop() }
        broadcasterJob = parentScope.launch(Dispatchers.IO) { broadcasterLoop() }
        cleanJob = parentScope.launch(Dispatchers.IO) { cleanLoop() }
        parentScope.launch(Dispatchers.IO) { startupScan() }
        val ipv4 = NetworkUtil.getLocalIPv4List()
        val ipv6 = NetworkUtil.getLocalIPv6List()
        log("[发现] 本机 IPv4: " + ipv4.joinToString(", "))
        if (ipv6.isNotEmpty()) log("[发现] 本机 IPv6: " + ipv6.joinToString(", "))
        log("[发现] 主机名: " + hostname)
    }

    fun stop() {
        // 先广播"正常下线"通知，让其他设备立即从列表移除本机。
        // 注意：必须在关闭 socket / 停止 running 之前执行（发送是同步的、尽力而为）。
        try { sendByeBroadcast() } catch (_: Exception) {}

        running = false
        udpListenerJob?.cancel()
        udpListenerV6Job?.cancel()
        scanListenerJob?.cancel()
        scanListenerV6Job?.cancel()
        broadcasterJob?.cancel()
        autoScanJob?.cancel()
        cleanJob?.cancel()
    }

    /**
     * 发送"正常下线"通知：`{"hostname":"xxx","bye":true}`
     *
     * 双重投递：
     *   1. 单播 —— 向已知节点列表里的每个 IP:UDP_PORT 各发一份（最可靠）
     *   2. 广播 —— 向本机所有网卡的广播地址 + 255.255.255.255 各发一份（兜底）
     *
     * 同步执行（不挂起），因为调用方 UdpDiscovery.stop() 是普通函数。
     * UDP 发送不阻塞，总共几十毫秒；发送失败也不影响退出。
     */
    private fun sendByeBroadcast() {
        val msg = buildMsg("bye" to true)
        val bytes = msg.toByteArray(Charsets.UTF_8)

        // ---------- 1) 单播：向已知节点 ----------
        val peerIps = try {
            deviceRepo.snapshot().map { it.ip }
        } catch (_: Exception) {
            emptyList()
        }
        var unicastOk = 0
        var unicastFail = 0
        if (peerIps.isNotEmpty()) {
            val socket = try { DatagramSocket() } catch (_: Exception) { null }
            if (socket != null) {
                try {
                    socket.broadcast = true
                    for (ip in peerIps) {
                        try {
                            socket.send(DatagramPacket(bytes, bytes.size, InetAddress.getByName(ip), Constants.UDP_PORT))
                            unicastOk++
                        } catch (_: Exception) {
                            unicastFail++
                        }
                    }
                } finally {
                    try { socket.close() } catch (_: Exception) {}
                }
            }
        }

        // ---------- 2) 广播：兜底 ----------
        var bcastOk = 0
        val socket = try {
            DatagramSocket().apply { broadcast = true }
        } catch (_: Exception) {
            null
        }
        if (socket != null) {
            try {
                for (ip in NetworkUtil.getLocalIPv4List()) {
                    try {
                        socket.send(DatagramPacket(bytes, bytes.size, InetAddress.getByName(broadcastAddressFor(ip)), Constants.UDP_PORT))
                        bcastOk++
                    } catch (_: Exception) {}
                }
                try {
                    socket.send(DatagramPacket(bytes, bytes.size, InetAddress.getByName("255.255.255.255"), Constants.UDP_PORT))
                    bcastOk++
                } catch (_: Exception) {}
            } finally {
                try { socket.close() } catch (_: Exception) {}
            }
        }

        // ---------- 日志 ----------
        var logMsg = "[退出] 已发送下线通知：单播 " + unicastOk + " 台设备"
        if (unicastFail > 0) logMsg += "（" + unicastFail + " 台失败）"
        logMsg += "，广播 " + bcastOk + " 个地址"
        log(logMsg)
    }

    fun startAutoScan(parentScope: CoroutineScope) {
        if (autoScanEnabled) return
        autoScanEnabled = true
        autoScanJob = parentScope.launch(Dispatchers.IO) {
            log("[扫描] 后台自动扫描已开启（每 " + (Constants.AUTO_SCAN_INTERVAL_MS / 1000) + " 秒）")
            while (isActive && autoScanEnabled) {
                if (!pausingNetwork) {
                    autoScanAllSubnets()
                    heartbeatCheck()
                }
                var waited = 0L
                while (waited < Constants.AUTO_SCAN_INTERVAL_MS && autoScanEnabled && isActive) {
                    delay(1000)
                    waited += 1000
                }
            }
        }
    }

    fun stopAutoScan() {
        autoScanEnabled = false
        autoScanJob?.cancel()
        autoScanJob = null
        log("[扫描] 后台自动扫描已关闭")
    }

    // ================= UDP 监听 (9998) =================

    private fun udpListenerLoop() {
        val socket: DatagramSocket = try {
            DatagramSocket(null).apply {
                reuseAddress = true
                broadcast = true
                soTimeout = 0
                bind(InetSocketAddress(Constants.UDP_PORT))
            }
        } catch (e: Exception) {
            log("[发现] UDP 监听端口 " + Constants.UDP_PORT + " 绑定失败: " + e.message)
            return
        }
        val buf = ByteArray(2048)
        while (running) {
            try {
                val packet = DatagramPacket(buf, buf.size)
                socket.receive(packet)
                val remoteIp = packet.address.hostAddress?.substringBefore('%') ?: continue
                if (shouldIgnore(remoteIp)) continue
                val text = String(packet.data, 0, packet.length, Charsets.UTF_8)
                handleUdpMessage(socket, remoteIp, text)
            } catch (e: Exception) {
                if (running) Log.d("UdpDiscovery", "udp recv error: " + e.message)
            }
        }
        try { socket.close() } catch (_: Exception) {}
    }

    private fun handleUdpMessage(socket: DatagramSocket, remoteIp: String, text: String) {
        val msg = try {
            @Suppress("UNCHECKED_CAST")
            gson.fromJson(text, Map::class.java) as? Map<String, Any?> ?: return
        } catch (_: Exception) {
            return
        }

        // 按设备指纹去重：收到自己发的包（device_id 相同）直接忽略。
        // 比按 IP 过滤可靠——多网卡/IP 变化/快照失准都不会误把自己加入列表。
        val selfDid = (msg["device_id"] as? String) ?: ""
        if (selfDid.isNotEmpty() && selfDid == deviceId) {
            return
        }

        // 正常下线通知：立即移除该设备，不等待心跳/超时
        if (msg["bye"] == true) {
            val existed = deviceRepo.has(remoteIp)
            deviceRepo.remove(remoteIp)
            if (existed) {
                val hn = (msg["hostname"] as? String) ?: remoteIp
                log("[发现] 设备 " + hn + " (" + remoteIp + ") 已下线")
            }
            return
        }

        // 心跳包
        if (msg["heartbeat"] == true) {
            deviceRepo.touch(remoteIp)
            try {
                val reply = buildMsg("heartbeat" to true, "ack" to true)
                val bytes = reply.toByteArray(Charsets.UTF_8)
                socket.send(DatagramPacket(bytes, bytes.size, InetAddress.getByName(remoteIp), Constants.UDP_PORT))
            } catch (_: Exception) {}
            return
        }

        val remoteHost = (msg["hostname"] as? String) ?: remoteIp
        val remoteDeviceId = (msg["device_id"] as? String) ?: ""
        val remoteMac = (msg["mac"] as? String) ?: ""
        val isScanReply = msg["scan_reply"] == true
        val isReply = msg["reply"] == true

        // 按 device_id 去重：同一台设备的旧 IP 条目要迁移到新 IP
        if (remoteDeviceId.isNotEmpty()) {
            try {
                val stale = deviceRepo.snapshot().filter {
                    it.ip != remoteIp && it.deviceId == remoteDeviceId
                }
                for (old in stale) {
                    deviceRepo.remove(old.ip)
                    log("[发现] 设备 " + remoteHost + " IP 变化: " + old.ip + " → " + remoteIp)
                }
            } catch (_: Exception) {}
        }

        val isNew = deviceRepo.upsert(
            ip = remoteIp,
            hostname = remoteHost,
            source = if (isScanReply) "scan" else "udp",
            deviceId = remoteDeviceId,
            mac = remoteMac
        )
        if (isNew) {
            onNewDevice(remoteIp, remoteHost)
        }

        // 普通广播（非回复）需回一个 reply 让对端也发现本机
        if (!isReply && !isScanReply) {
            try {
                val reply = buildMsg("reply" to true)
                val bytes = reply.toByteArray(Charsets.UTF_8)
                socket.send(DatagramPacket(bytes, bytes.size, InetAddress.getByName(remoteIp), Constants.UDP_PORT))
            } catch (_: Exception) {}
        }
    }

    // ================= IPv6 UDP 监听 (9998) =================

    private fun udpListenerV6Loop() {
        val socket: DatagramSocket = try {
            DatagramSocket(null).apply {
                reuseAddress = true
                soTimeout = 0
                bind(InetSocketAddress(InetAddress.getByName("::"), Constants.UDP_PORT))
            }
        } catch (e: Exception) {
            // 部分设备/系统可能不支持 IPv6，静默跳过
            Log.d("UdpDiscovery", "IPv6 UDP 监听绑定失败: " + e.message)
            return
        }
        val buf = ByteArray(2048)
        while (running) {
            try {
                val packet = DatagramPacket(buf, buf.size)
                socket.receive(packet)
                if (packet.address is java.net.Inet6Address) {
                    val remoteIp = packet.address.hostAddress?.substringBefore('%') ?: continue
                    if (shouldIgnore(remoteIp)) continue
                    val text = String(packet.data, 0, packet.length, Charsets.UTF_8)
                    handleUdpMessage(socket, remoteIp, text)
                }
            } catch (e: Exception) {
                if (running) Log.d("UdpDiscovery", "ipv6 udp recv error: " + e.message)
            }
        }
        try { socket.close() } catch (_: Exception) {}
    }

    // ================= 扫描监听 (9997) =================

    private fun scanListenerLoop() {
        val socket: DatagramSocket = try {
            DatagramSocket(null).apply {
                reuseAddress = true
                soTimeout = 0
                bind(InetSocketAddress(Constants.SCAN_PORT))
            }
        } catch (e: Exception) {
            log("[发现] 扫描监听端口 " + Constants.SCAN_PORT + " 绑定失败: " + e.message)
            return
        }
        val buf = ByteArray(2048)
        while (running) {
            try {
                val packet = DatagramPacket(buf, buf.size)
                socket.receive(packet)
                val remoteIp = packet.address.hostAddress?.substringBefore('%') ?: continue
                if (shouldIgnore(remoteIp)) continue
                val reply = buildMsg("scan_reply" to true)
                val bytes = reply.toByteArray(Charsets.UTF_8)
                // 同时回复 SCAN_PORT 和 UDP_PORT，确保对方能收到
                for (port in intArrayOf(Constants.SCAN_PORT, Constants.UDP_PORT)) {
                    try {
                        socket.send(DatagramPacket(bytes, bytes.size, InetAddress.getByName(remoteIp), port))
                    } catch (_: Exception) {}
                }
            } catch (e: Exception) {
                if (running) Log.d("UdpDiscovery", "scan recv error: " + e.message)
            }
        }
        try { socket.close() } catch (_: Exception) {}
    }

    // ================= IPv6 扫描监听 (9997) =================

    private fun scanListenerV6Loop() {
        val socket: DatagramSocket = try {
            DatagramSocket(null).apply {
                reuseAddress = true
                soTimeout = 0
                bind(InetSocketAddress(InetAddress.getByName("::"), Constants.SCAN_PORT))
            }
        } catch (e: Exception) {
            Log.d("UdpDiscovery", "IPv6 扫描监听绑定失败: " + e.message)
            return
        }
        val buf = ByteArray(2048)
        while (running) {
            try {
                val packet = DatagramPacket(buf, buf.size)
                socket.receive(packet)
                if (packet.address is java.net.Inet6Address) {
                    val remoteIp = packet.address.hostAddress?.substringBefore('%') ?: continue
                    if (shouldIgnore(remoteIp)) continue
                    val reply = buildMsg("scan_reply" to true)
                    val bytes = reply.toByteArray(Charsets.UTF_8)
                    for (port in intArrayOf(Constants.SCAN_PORT, Constants.UDP_PORT)) {
                        try {
                            socket.send(DatagramPacket(bytes, bytes.size, InetAddress.getByName(remoteIp), port))
                        } catch (_: Exception) {}
                    }
                }
            } catch (e: Exception) {
                if (running) Log.d("UdpDiscovery", "ipv6 scan recv error: " + e.message)
            }
        }
        try { socket.close() } catch (_: Exception) {}
    }

    // ================= 广播 =================

    private suspend fun broadcasterLoop() {
        // 统一设备发现机制：周期广播搜索（替代原来"启动 5 秒后停止"）。
        //
        // 为什么改：
        //   · 原来是"启动 5 秒内爆发广播，之后完全停"，导致晚启动的对端错过窗口
        //   · 现在改为：
        //       - 启动后前 5 秒：每 0.2 秒发一次"广播搜索"（快速发现）
        //       - 5 秒后：每 20 秒发一次"广播搜索"（低成本维持）
        //   · 接收方收到 {discovery:true} 会立刻回 {reply:true}
        //   · 这样"发现"完全依赖"请求→响应"，不再依赖对方是否在广播窗口
        //
        // 副作用：因为发的是 discovery（不是普通 announce），桌面端的续传弹窗
        // 只在首次发现设备时才触发，不会每秒刷屏。
        val startTime = System.currentTimeMillis()
        while (running) {
            try {
                if (!pausingNetwork) {
                    sendBroadcastOnce()
                }
            } catch (_: Exception) {}
            val burst = System.currentTimeMillis() - startTime < Constants.BROADCAST_BURST_DURATION_MS
            delay(if (burst) Constants.BROADCAST_BURST_INTERVAL_MS else Constants.AUTO_SCAN_INTERVAL_MS)
        }
    }

    private fun sendBroadcastOnce() {
        // 发"广播搜索"包（discovery=true），接收方会回 reply=true。
        // 这比"普通广播"更明确——请求-响应模式，对端必须回应。
        val msg = buildMsg("discovery" to true)
        val bytes = msg.toByteArray(Charsets.UTF_8)
        val socket = DatagramSocket().apply {
            broadcast = true
        }
        try {
            for (ip in NetworkUtil.getLocalIPv4List()) {
                val bcast = broadcastAddressFor(ip)
                try {
                    socket.send(DatagramPacket(bytes, bytes.size, InetAddress.getByName(bcast), Constants.UDP_PORT))
                } catch (_: Exception) {}
            }
            // 全局广播
            try {
                socket.send(DatagramPacket(bytes, bytes.size, InetAddress.getByName("255.255.255.255"), Constants.UDP_PORT))
            } catch (_: Exception) {}
        } finally {
            try { socket.close() } catch (_: Exception) {}
        }
    }

    private fun broadcastAddressFor(ip: String): String {
        val parts = ip.split('.')
        if (parts.size != 4) return "255.255.255.255"
        return parts[0] + "." + parts[1] + "." + parts[2] + ".255"
    }

    /**
     * 广播搜索：向所有广播地址爆发发送 3 次 discovery 包，等待 5 秒。
     * 对端 udp_listener 会自动把本机加入设备列表。
     */
    suspend fun broadcastDiscovery() = withContext(Dispatchers.IO) {
        log("[广播搜索] 发送广播探测，等待设备回复...")
        val msg = buildMsg("discovery" to true)
        val bytes = msg.toByteArray(Charsets.UTF_8)
        repeat(3) {
            val socket = try {
                DatagramSocket().apply { broadcast = true }
            } catch (_: Exception) { null }
            if (socket != null) {
                try {
                    for (ip in NetworkUtil.getLocalIPv4List()) {
                        try {
                            socket.send(DatagramPacket(bytes, bytes.size, InetAddress.getByName(broadcastAddressFor(ip)), Constants.UDP_PORT))
                        } catch (_: Exception) {}
                    }
                    try {
                        socket.send(DatagramPacket(bytes, bytes.size, InetAddress.getByName("255.255.255.255"), Constants.UDP_PORT))
                    } catch (_: Exception) {}
                } finally {
                    try { socket.close() } catch (_: Exception) {}
                }
            }
            delay(200)
        }
        delay(5000)
        log("[广播搜索] 完成")
    }

    // ================= 主动扫描 =================

    private suspend fun startupScan() {
        delay(500)
        log("[扫描] 启动快速扫描...")
        autoScanAllSubnets()
    }

    /** 扫描本机所有子网 */
    suspend fun autoScanAllSubnets() = withContext(Dispatchers.IO) {
        val subnets = SubnetUtil.allSubnets()
        if (subnets.isEmpty()) {
            log("[扫描] 未找到任何有效子网")
            return@withContext
        }
        log("[扫描] 将扫描子网: " + subnets.joinToString(", "))
        for (cidr in subnets) {
            if (!running) return@withContext
            scanSubnet(cidr)
        }
        if (running) log("[扫描] 自动扫描完成")
    }

    /** 扫描单个 CIDR 网段 */
    suspend fun scanSubnet(cidr: String) = withContext(Dispatchers.IO) {
        val hosts = SubnetUtil.hostsForCidr(cidr, Constants.MAX_SCAN_IPS)
        if (hosts.isEmpty()) {
            log("[扫描] " + cidr + " 中没有可扫描的主机")
            return@withContext
        }
        log("[扫描] 开始扫描 " + cidr + "，共 " + hosts.size + " 个 IP...")
        var found = 0
        var scanned = 0
        val myIpSet = myIps
        coroutineScope {
            val chunkSize = Constants.SCAN_CONCURRENCY
            var index = 0
            while (index < hosts.size) {
                if (!running) break
                val slice = hosts.subList(index, minOf(index + chunkSize, hosts.size))
                val results = slice.map { ip ->
                    async(Dispatchers.IO) {
                        if (ip in myIpSet) return@async null
                        scanOne(ip)
                    }
                }.awaitAll()
                for (r in results) {
                    scanned++
                    if (r != null) {
                        val (ip, hname, remoteDid) = r
                        // 设备指纹去重：跳过自己（即使本机 IP 快照失准）
                        if (remoteDid.isNotEmpty() && remoteDid == deviceId) continue
                        if (!myIpSet.contains(ip)) {
                            val isNew = deviceRepo.upsert(ip, hname, "scan", deviceId = remoteDid)
                            found++
                            if (isNew) onNewDevice(ip, hname)
                        }
                    }
                }
                index += chunkSize
            }
        }
        if (running) {
            log("[扫描] " + cidr + " 扫描完成，扫描 " + scanned + " 个 IP，发现 " + found + " 个设备")
        }
    }

    /** 探测单个 IP：先试 SCAN_PORT，再试 UDP_PORT。返回 (ip, hostname, deviceId)。 */
    private fun scanOne(ip: String): Triple<String, String, String>? {
        val msg = buildMsg()
        val bytes = msg.toByteArray(Charsets.UTF_8)
        for (port in intArrayOf(Constants.SCAN_PORT, Constants.UDP_PORT)) {
            try {
                val socket = DatagramSocket().apply { soTimeout = Constants.SCAN_TIMEOUT_MS }
                try {
                    socket.send(DatagramPacket(bytes, bytes.size, InetAddress.getByName(ip), port))
                    val resp = ByteArray(2048)
                    val p = DatagramPacket(resp, resp.size)
                    socket.receive(p)
                    val text = String(p.data, 0, p.length, Charsets.UTF_8)
                    @Suppress("UNCHECKED_CAST")
                    val map = gson.fromJson(text, Map::class.java) as? Map<String, Any?>
                    val hname = (map?.get("hostname") as? String) ?: ip
                    val remoteDid = (map?.get("device_id") as? String) ?: ""
                    return Triple(ip, hname, remoteDid)
                } finally {
                    try { socket.close() } catch (_: Exception) {}
                }
            } catch (_: Exception) {
            }
        }
        return null
    }

    /** 向指定 IP（IPv4 或 IPv6）发送探测，让对方也发现我们 */
    fun sendProbeTo(ip: String) {
        try {
            val socket = DatagramSocket().apply { soTimeout = 2000 }
            val addr = InetAddress.getByName(ip)
            val msg = buildMsg()
            val bytes = msg.toByteArray(Charsets.UTF_8)
            try {
                socket.send(DatagramPacket(bytes, bytes.size, addr, Constants.UDP_PORT))
            } catch (_: Exception) {}
            try {
                val scanMsg = buildMsg("scan_reply" to true)
                val scanBytes = scanMsg.toByteArray(Charsets.UTF_8)
                socket.send(DatagramPacket(scanBytes, scanBytes.size, addr, Constants.SCAN_PORT))
            } catch (_: Exception) {}
            socket.close()
        } catch (e: Exception) {
            log("[手动添加] 向 " + ip + " 发送探测失败: " + e.message)
        }
    }


    // ================= 心跳 =================

    suspend fun heartbeatCheck() = withContext(Dispatchers.IO) {
        val nodes = deviceRepo.snapshot()
        if (nodes.isEmpty()) return@withContext
        val offline = mutableListOf<String>()
        coroutineScope {
            nodes.map { node ->
                async(Dispatchers.IO) {
                    val ok = heartbeatOne(node.ip)
                    Pair(node.ip, ok)
                }
            }.awaitAll().forEach { (ip, ok) ->
                if (ok) {
                    deviceRepo.succeedHeartbeat(ip)
                } else {
                    if (deviceRepo.failHeartbeat(ip, Constants.HEARTBEAT_MAX_FAIL)) {
                        offline.add(ip)
                    }
                }
            }
        }
        if (offline.isNotEmpty()) {
            log("[心跳] " + offline.size + " 个设备连续 " + Constants.HEARTBEAT_MAX_FAIL + " 次心跳无回复，已移除")
        }
    }

    private fun heartbeatOne(ip: String): Boolean {
        var socket: DatagramSocket? = null
        return try {
            val s = DatagramSocket().apply { soTimeout = 1000 }
            socket = s
            val msg = buildMsg("heartbeat" to true)
            val bytes = msg.toByteArray(Charsets.UTF_8)
            s.send(DatagramPacket(bytes, bytes.size, InetAddress.getByName(ip), Constants.UDP_PORT))
            val resp = ByteArray(2048)
            s.receive(DatagramPacket(resp, resp.size))
            true
        } catch (_: Exception) {
            false
        } finally {
            try { socket?.close() } catch (_: Exception) {}
        }
    }

    /** 主动检查已有设备在线状态（用户点击"刷新在线"） */
    suspend fun removeOfflineNodes() = withContext(Dispatchers.IO) {
        val nodes = deviceRepo.snapshot()
        if (nodes.isEmpty()) return@withContext
        log("[扫描] 正在检查已有设备在线状态...")
        coroutineScope {
            nodes.map { node ->
                async(Dispatchers.IO) { Pair(node.ip, heartbeatOne(node.ip)) }
            }.awaitAll().forEach { (ip, ok) ->
                if (ok) {
                    deviceRepo.succeedHeartbeat(ip)
                } else {
                    deviceRepo.remove(ip)
                }
            }
        }
    }

    // ================= 清理 =================

    private suspend fun cleanLoop() {
        while (running) {
            deviceRepo.cleanExpired(Constants.NODE_TIMEOUT_MS)
            delay(2000)
        }
    }

    private fun shouldIgnore(ip: String): Boolean {
        if (ip in myIps) return true
        if (ip == "127.0.0.1" || ip == "::1" || ip == "0.0.0.0" || ip == "::") return true
        return false
    }
}
