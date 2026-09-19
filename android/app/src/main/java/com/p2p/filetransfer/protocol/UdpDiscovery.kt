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
    private val onNewDevice: (String, String) -> Unit
) {

    private val gson = Gson()
    private val hostname: String = NetworkUtil.hostname()
    private val myIps: Set<String> = (NetworkUtil.getLocalIPv4List() + NetworkUtil.getLocalIPv6List()).toSet()

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
        running = false
        udpListenerJob?.cancel()
        udpListenerV6Job?.cancel()
        scanListenerJob?.cancel()
        scanListenerV6Job?.cancel()
        broadcasterJob?.cancel()
        autoScanJob?.cancel()
        cleanJob?.cancel()
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

        // 心跳包
        if (msg["heartbeat"] == true) {
            deviceRepo.touch(remoteIp)
            try {
                val reply = gson.toJson(mapOf("heartbeat" to true, "ack" to true))
                val bytes = reply.toByteArray(Charsets.UTF_8)
                socket.send(DatagramPacket(bytes, bytes.size, InetAddress.getByName(remoteIp), Constants.UDP_PORT))
            } catch (_: Exception) {}
            return
        }

        val remoteHost = (msg["hostname"] as? String) ?: remoteIp
        val isScanReply = msg["scan_reply"] == true
        val isReply = msg["reply"] == true

        val isNew = deviceRepo.upsert(remoteIp, remoteHost, if (isScanReply || isReply) "udp" else "udp")
        if (isNew) {
            onNewDevice(remoteIp, remoteHost)
        }

        // 普通广播（非回复）需回一个 reply 让对端也发现本机
        if (!isReply && !isScanReply) {
            try {
                val reply = gson.toJson(mapOf("hostname" to hostname, "reply" to true))
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
                val reply = gson.toJson(mapOf("hostname" to hostname, "scan_reply" to true))
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
                    val reply = gson.toJson(mapOf("hostname" to hostname, "scan_reply" to true))
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
        // 只在启动后 BROADCAST_BURST_DURATION_MS 内做爆发式广播，之后停止。
        //
        // 原因：桌面端 Python 的 UDP 监听在收到“普通广播”（无 reply/scan_reply）时，
        // 会无条件触发一次续传检查，导致每隔一秒弹窗。Android 端不再周期性广播，
        // 通过“回复桌面端广播 + 心跳 + 手动广播搜索”维持被发现。
        val startTime = System.currentTimeMillis()
        while (running) {
            if (System.currentTimeMillis() - startTime >= Constants.BROADCAST_BURST_DURATION_MS) {
                return
            }
            try {
                if (!pausingNetwork) {
                    sendBroadcastOnce()
                }
            } catch (_: Exception) {}
            delay(Constants.BROADCAST_BURST_INTERVAL_MS)
        }
    }

    private fun sendBroadcastOnce() {
        val msg = gson.toJson(mapOf("hostname" to hostname))
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
        val msg = gson.toJson(mapOf("hostname" to hostname, "discovery" to true))
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
                        val (ip, hname) = r
                        if (!myIpSet.contains(ip)) {
                            val isNew = deviceRepo.upsert(ip, hname, "scan")
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

    /** 探测单个 IP：先试 SCAN_PORT，再试 UDP_PORT */
    private fun scanOne(ip: String): Pair<String, String>? {
        val msg = gson.toJson(mapOf("hostname" to hostname))
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
                    return Pair(ip, hname)
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
            val msg = gson.toJson(mapOf("hostname" to hostname))
            val bytes = msg.toByteArray(Charsets.UTF_8)
            try {
                socket.send(DatagramPacket(bytes, bytes.size, addr, Constants.UDP_PORT))
            } catch (_: Exception) {}
            try {
                val scanMsg = gson.toJson(mapOf("hostname" to hostname, "scan_reply" to true))
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
            val msg = gson.toJson(mapOf("heartbeat" to true))
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
