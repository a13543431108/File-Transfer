package com.p2p.filetransfer

import android.app.Notification
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.SharedPreferences
import android.net.wifi.WifiManager
import android.os.Build
import android.os.IBinder
import android.os.PowerManager
import androidx.core.app.NotificationCompat
import com.p2p.filetransfer.model.DeviceNode
import com.p2p.filetransfer.model.IncomingRequest
import com.p2p.filetransfer.model.ReceiveRequest
import com.p2p.filetransfer.model.ResumeReminder
import com.p2p.filetransfer.model.ResumeReminderItem
import com.p2p.filetransfer.model.TransferItem
import com.p2p.filetransfer.model.TransferProgress
import com.p2p.filetransfer.protocol.TcpFileTransfer
import com.p2p.filetransfer.protocol.UdpDiscovery
import com.p2p.filetransfer.repository.DeviceRepository
import com.p2p.filetransfer.repository.ResumeRepository
import com.p2p.filetransfer.util.NetworkUtil
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.withTimeout
import java.io.File
import java.util.UUID
import java.util.concurrent.ConcurrentHashMap

/**
 * 前台服务：承载全部网络功能（UDP 发现 + TCP 传输），并向 UI 暴露状态。
 * 通过单例 [instance] 访问其 StateFlow / SharedFlow。
 */
class P2PFileTransferService : Service() {

    private val serviceScope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    lateinit var deviceRepo: DeviceRepository
        private set
    private lateinit var resumeRepo: ResumeRepository
    private lateinit var udp: UdpDiscovery
    private lateinit var tcp: TcpFileTransfer

    private lateinit var wifiLock: WifiManager.WifiLock
    private var wakeLock: PowerManager.WakeLock? = null
    private val pendingConfirms = ConcurrentHashMap<String, CompletableDeferred<Boolean>>()

    private val prefs: SharedPreferences by lazy {
        getSharedPreferences("p2p_config", Context.MODE_PRIVATE)
    }

    override fun onCreate() {
        super.onCreate()
        instance = this
        deviceRepo = DeviceRepository()
        resumeRepo = ResumeRepository(this)

        val saveDirProvider = { currentSaveDir() }

        udp = UdpDiscovery(
            deviceRepo = deviceRepo,
            log = ::emitLog,
            onNewDevice = { ip, host ->
                emitLog("[发现] 新设备 " + host + " (" + ip + ")")
                checkResumeOnOnline(ip, host)
            }
        )
        // 应用已保存的设备名
        val savedName = prefs.getString(KEY_DEVICE_NAME, null)
        if (!savedName.isNullOrEmpty()) {
            udp.setHostname(savedName)
        } else {
            // 首次运行：默认使用主机名并持久化
            prefs.edit().putString(KEY_DEVICE_NAME, udp.currentHostname()).apply()
        }
        _deviceName.value = udp.currentHostname()

        tcp = TcpFileTransfer(
            publisher = { file, isFolder -> publishReceived(file, isFolder) },
            resumeRepo = resumeRepo,
            saveDirProvider = saveDirProvider,
            log = ::emitLog,
            onSendProgress = { _sendProgress.value = it },
            onRecvProgress = { _recvProgress.value = it },
            onReceiveRequest = { req -> emitLog("[接收] 收到 " + req.fileName); _receiveRequest.tryEmit(req) },
            onAcceptIncoming = { req -> requestAccept(req) },
            onConfirmOverwrite = { file -> autoConfirmOverwrite(file) }
        )

        acquireLocks()

        startForeground(NOTIFICATION_ID, buildOngoingNotification())
        emitLog("[服务] 启动，本机 IPv4: " + NetworkUtil.getLocalIPv4List().joinToString(", "))
        emitLog("[服务] 设备名: " + udp.currentHostname())

        udp.start(serviceScope)
        tcp.startServer(serviceScope)
        udp.startAutoScan(serviceScope)

        _saveDirPath.value = publicSaveDescription()
        // 同步后台扫描状态到 UI（udp.startAutoScan 已把 autoScanEnabled 置 true）
        _autoScan.value = udp.autoScanEnabled
        _isRunning.value = true
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        return START_STICKY
    }

    override fun onDestroy() {
        _isRunning.value = false
        // 未决的来件确认统一拒绝
        for ((_, d) in pendingConfirms) {
            try { d.complete(false) } catch (_: Exception) {}
        }
        pendingConfirms.clear()
        try { udp.stop() } catch (_: Exception) {}
        try { tcp.stopServer() } catch (_: Exception) {}
        serviceScope.cancel()
        releaseLocks()
        instance = null
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    // ================= 对外操作 API =================

    fun broadcastSearch() {
        serviceScope.launch { udp.broadcastDiscovery() }
    }

    fun scanAllSubnets() {
        serviceScope.launch { udp.autoScanAllSubnets() }
    }

    fun scanSubnet(cidr: String) {
        serviceScope.launch { udp.scanSubnet(cidr) }
    }

    fun addManualNode(ip: String) {
        udp.sendProbeTo(ip)
        val isNew = deviceRepo.upsert(ip, ip, "manual")
        if (isNew) emitLog("[手动] 已添加 " + ip)
    }

    fun refreshOnline() {
        serviceScope.launch { udp.removeOfflineNodes() }
    }

    fun toggleAutoScan() {
        if (udp.autoScanEnabled) udp.stopAutoScan() else udp.startAutoScan(serviceScope)
        _autoScan.value = udp.autoScanEnabled
    }

    /**
     * 向多个 IP 串行发送多个项目。
     * [onFinished] 在所有 IP 的所有项目处理完后调用，参数为"全部 IP 都成功"的项目路径集合，
     *             供 UI 从待发送列表移除。
     */
    fun sendItems(
        targetIps: List<String>,
        items: List<TransferItem>,
        onFinished: ((Set<String>) -> Unit)? = null
    ) {
        if (targetIps.isEmpty() || items.isEmpty()) return
        serviceScope.launch {
            // 传输期间暂停广播/扫描/心跳
            udp.pausingNetwork = true
            try {
                // path -> 是否全部 IP 都成功
                val allSuccess = HashMap<String, Boolean>()
                for (item in items) allSuccess[item.path] = true

                for (ip in targetIps) {
                    // 发送前离线检查
                    if (!deviceRepo.has(ip)) {
                        emitLog("[发送] 设备 " + ip + " 已离线，跳过")
                        for (item in items) allSuccess[item.path] = false
                        continue
                    }
                    tcp.sendFiles(ip, items) { path, success ->
                        emitLog("[发送] " + (if (success) "✓ " else "✗ ") + path)
                        if (!success) allSuccess[path] = false
                    }
                }

                val okPaths = allSuccess.filterValues { it }.keys
                onFinished?.invoke(okPaths)
            } finally {
                udp.pausingNetwork = false
            }
        }
    }

    fun currentSaveDir(): File {
        val path = prefs.getString(KEY_SAVE_DIR, null)
        val dir = if (path != null) File(path) else File(getExternalFilesDir(null) ?: filesDir, "Received")
        if (!dir.exists()) dir.mkdirs()
        return dir
    }

    /** 接收文件最终对外可见的位置说明（用于 UI 显示） */
    fun publicSaveDescription(): String {
        val treeUriStr = prefs.getString(KEY_SAVE_TREE_URI, null)
        if (treeUriStr == null) return PUBLIC_SAVE_DESC
        return try {
            val name = com.p2p.filetransfer.util.PublicStorage
                .treeDisplayName(applicationContext, android.net.Uri.parse(treeUriStr))
            if (name != null) "用户目录: " + name else "用户选择的目录"
        } catch (_: Exception) {
            "用户选择的目录"
        }
    }

    /**
     * 用户通过 SAF 选择的接收目录（持久化 Uri）。
     * 未设置时使用默认的 下载/P2PFileTransfer/。
     */
    fun setSaveTreeUri(uri: android.net.Uri) {
        try {
            contentResolver.takePersistableUriPermission(
                uri,
                android.content.Intent.FLAG_GRANT_READ_URI_PERMISSION or
                        android.content.Intent.FLAG_GRANT_WRITE_URI_PERMISSION
            )
        } catch (_: Exception) {}
        prefs.edit().putString(KEY_SAVE_TREE_URI, uri.toString()).apply()
        val desc = publicSaveDescription()
        _saveDirPath.value = desc
        emitLog("[设置] 接收文件将保存到: " + desc)
    }

    /**
     * 修改本机设备名。
     * 立即生效：下次广播/心跳/回复都使用新名字。
     */
    fun setDeviceName(newName: String) {
        val trimmed = newName.trim()
        if (trimmed.isEmpty()) {
            emitLog("[设置] 设备名不能为空")
            return
        }
        prefs.edit().putString(KEY_DEVICE_NAME, trimmed).apply()
        udp.setHostname(trimmed)
        _deviceName.value = trimmed
        emitLog("[设置] 设备名已改为: " + trimmed)
    }

    /** 读取当前设备名 */
    fun currentDeviceName(): String = udp.currentHostname()

    fun clearSaveTreeUri() {
        prefs.edit().remove(KEY_SAVE_TREE_URI).apply()
        _saveDirPath.value = publicSaveDescription()
        emitLog("[设置] 已恢复默认保存位置: " + PUBLIC_SAVE_DESC)
    }

    /**
     * 把接收完成的文件/文件夹发布到用户期望位置。
     * 用户通过 SAF 选择过树目录则发到那里；否则发到默认的 下载/P2PFileTransfer/。
     */
    private fun publishReceived(file: File, isFolder: Boolean): String? {
        val treeUriStr = prefs.getString(KEY_SAVE_TREE_URI, null)
        if (treeUriStr != null) {
            val treeUri = android.net.Uri.parse(treeUriStr)
            val r = if (isFolder)
                com.p2p.filetransfer.util.PublicStorage.publishFolderToTree(applicationContext, treeUri, file)
            else
                com.p2p.filetransfer.util.PublicStorage.publishFileToTree(applicationContext, treeUri, file)
            if (r != null) return r
            emitLog("[接收] 用户目录发布失败，回退到默认目录")
        }
        return if (isFolder)
            com.p2p.filetransfer.util.PublicStorage.publishFolder(applicationContext, file)
        else
            com.p2p.filetransfer.util.PublicStorage.publishFile(applicationContext, file)
    }

    // ================= 内部 =================

    private suspend fun autoConfirmOverwrite(file: File): Boolean {
        // 文件/文件夹已存在时默认覆盖；接收动作本身的确认走 requestAccept
        return true
    }

    /**
     * 挂起等待用户对来件请求的选择。
     * UI 通过 [incomingRequest] 收到 IncomingRequest 并调用 [respondIncoming] 回传结果。
     * 超过 60 秒未响应视为拒绝。
     */
    private suspend fun requestAccept(req: ReceiveRequest): Boolean {
        val id = UUID.randomUUID().toString()
        val deferred = CompletableDeferred<Boolean>()
        pendingConfirms[id] = deferred
        _incomingRequest.tryEmit(
            IncomingRequest(
                id = id,
                fromIp = req.fromIp,
                fileName = req.fileName,
                totalSize = req.totalSize,
                isFolder = req.isFolder
            )
        )
        return try {
            withTimeout(60_000L) { deferred.await() }
        } catch (e: Exception) {
            emitLog("[接收] 等待用户确认超时，默认拒绝 " + req.fileName)
            false
        } finally {
            pendingConfirms.remove(id)
        }
    }

    /** UI 回传用户选择 */
    fun respondIncoming(id: String, accept: Boolean) {
        pendingConfirms[id]?.complete(accept)
        pendingConfirms.remove(id)
    }

    // ================= 设备上线自动续传提醒 =================

    private val pendingResumeConfirms = ConcurrentHashMap<String, CompletableDeferred<Boolean>>()

    private fun checkResumeOnOnline(ip: String, hostname: String) {
        serviceScope.launch {
            try {
                val senderFiles = resumeRepo.listSenderStatesForIp(ip)
                val folderFiles = resumeRepo.listFolderStatesForIp(ip)
                if (senderFiles.isEmpty() && folderFiles.isEmpty()) return@launch

                val gson = com.google.gson.Gson()
                data class PendingItem(val filepath: String, val offset: Long, val total: Long, val isFolder: Boolean, val relPath: String?)
                val items = mutableListOf<PendingItem>()
                for (f in senderFiles) {
                    try {
                        val s = gson.fromJson(f.readText(), com.p2p.filetransfer.model.ResumeState::class.java) ?: continue
                        if (!File(s.filepath).exists()) continue
                        items.add(PendingItem(s.filepath, s.offset, s.totalSize, false, null))
                    } catch (_: Exception) {}
                }
                for (f in folderFiles) {
                    try {
                        val s = gson.fromJson(f.readText(), com.p2p.filetransfer.model.FolderResumeState::class.java) ?: continue
                        items.add(PendingItem("", s.offset, s.totalSize, true, s.folderName + "/" + s.relPath))
                    } catch (_: Exception) {}
                }
                if (items.isEmpty()) return@launch

                val id = java.util.UUID.randomUUID().toString()
                val deferred = CompletableDeferred<Boolean>()
                pendingResumeConfirms[id] = deferred
                _resumeReminder.tryEmit(
                    ResumeReminder(
                        id = id,
                        fromIp = ip,
                        hostname = hostname,
                        items = items.map {
                            ResumeReminderItem(
                                filepath = it.filepath,
                                relPath = it.relPath,
                                offset = it.offset,
                                totalSize = it.total,
                                isFolder = it.isFolder
                            )
                        }
                    )
                )
                val accept = try {
                    withTimeout(60_000L) { deferred.await() }
                } catch (_: Exception) {
                    false
                } finally {
                    pendingResumeConfirms.remove(id)
                }
                if (accept) {
                    emitLog("[续传] 用户确认向 " + ip + " 续传 " + items.size + " 个项目")
                    val transferItems = items.filter { !it.isFolder && it.filepath.isNotEmpty() }
                        .map { TransferItem(it.filepath, false) }
                        .toMutableList()
                    // 文件夹续传：从 filepath 中获取顶层文件夹路径（简单处理：
                    // FolderResumeState 里没有保存 folder 绝对路径，只能按当前 sender 记录推断）
                    // 为简化，暂不通过提醒自动重发文件夹续传，需用户手动添加文件夹。
                    if (transferItems.isNotEmpty()) {
                        udp.pausingNetwork = true
                        try {
                            tcp.sendFiles(ip, transferItems) { path, success ->
                                emitLog("[续传] " + (if (success) "✓ " else "✗ ") + path)
                            }
                        } finally {
                            udp.pausingNetwork = false
                        }
                    }
                } else {
                    emitLog("[续传] 用户放弃向 " + ip + " 续传，清理状态文件")
                    for (f in senderFiles) try { f.delete() } catch (_: Exception) {}
                    for (f in folderFiles) try { f.delete() } catch (_: Exception) {}
                }
            } catch (e: Exception) {
                emitLog("[续传] 检查续传失败: " + e.message)
            }
        }
    }

    /** UI 回传续传提醒选择 */
    fun respondResumeReminder(id: String, accept: Boolean) {
        pendingResumeConfirms[id]?.complete(accept)
        pendingResumeConfirms.remove(id)
    }

    private fun emitLog(msg: String) {
        _logs.tryEmit(msg)
    }

    private fun acquireLocks() {
        try {
            val wifi = applicationContext.getSystemService(Context.WIFI_SERVICE) as WifiManager
            wifiLock = wifi.createWifiLock(WifiManager.WIFI_MODE_FULL_HIGH_PERF, "p2p:wifi")
            wifiLock.setReferenceCounted(false)
            wifiLock.acquire()
        } catch (_: Exception) {}
        try {
            val pm = getSystemService(Context.POWER_SERVICE) as PowerManager
            wakeLock = pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "p2p:transfer")
            wakeLock?.setReferenceCounted(false)
            wakeLock?.acquire()
        } catch (_: Exception) {}
    }

    private fun releaseLocks() {
        try { wifiLock.release() } catch (_: Exception) {}
        try { wakeLock?.release() } catch (_: Exception) {}
    }

    private fun buildOngoingNotification(): Notification {
        val intent = Intent(this, MainActivity::class.java)
        val flags = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M)
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
        else PendingIntent.FLAG_UPDATE_CURRENT
        val pi = PendingIntent.getActivity(this, 0, intent, flags)

        return NotificationCompat.Builder(this, P2PApplication.CHANNEL_STATUS)
            .setSmallIcon(android.R.drawable.stat_sys_upload)
            .setContentTitle(getString(R.string.notification_title))
            .setContentText(getString(R.string.notification_text))
            .setOngoing(true)
            .setContentIntent(pi)
            .build()
    }

    companion object {
        private const val NOTIFICATION_ID = 1001
        private const val KEY_SAVE_DIR = "save_dir"

        @Volatile
        var instance: P2PFileTransferService? = null
            private set

        // ================= 对外状态流 =================

        private val _isRunning = MutableStateFlow(false)
        val isRunning: StateFlow<Boolean> = _isRunning

        private val _autoScan = MutableStateFlow(false)
        val autoScan: StateFlow<Boolean> = _autoScan

        private val _sendProgress = MutableStateFlow(TransferProgress())
        val sendProgress: StateFlow<TransferProgress> = _sendProgress

        private val _recvProgress = MutableStateFlow(TransferProgress())
        val recvProgress: StateFlow<TransferProgress> = _recvProgress

        private val _logs = MutableSharedFlow<String>(extraBufferCapacity = 256)
        val logs: SharedFlow<String> = _logs

        private val _receiveRequest = MutableSharedFlow<ReceiveRequest>(extraBufferCapacity = 16)
        val receiveRequest: SharedFlow<ReceiveRequest> = _receiveRequest

        private val _incomingRequest = MutableSharedFlow<IncomingRequest>(extraBufferCapacity = 16)
        val incomingRequest: SharedFlow<IncomingRequest> = _incomingRequest

        private val _resumeReminder = MutableSharedFlow<com.p2p.filetransfer.model.ResumeReminder>(extraBufferCapacity = 8)
        val resumeReminder: SharedFlow<com.p2p.filetransfer.model.ResumeReminder> = _resumeReminder

        private val _saveDirPath = MutableStateFlow("")
        val saveDirPath: StateFlow<String> = _saveDirPath

        private val _deviceName = MutableStateFlow("")
        val deviceName: StateFlow<String> = _deviceName

        const val PUBLIC_SAVE_DESC = "下载/P2PFileTransfer/"
        private const val KEY_SAVE_TREE_URI = "save_tree_uri"
        private const val KEY_DEVICE_NAME = "device_name"

        /** UI 侧读取设备列表 */
        fun deviceFlow(): StateFlow<List<DeviceNode>>? = instance?.deviceRepo?.nodesFlow
    }
}
