package com.p2p.filetransfer

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.PowerManager
import android.provider.Settings
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.activity.viewModels
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.ArrowDownward
import androidx.compose.material.icons.filled.ArrowUpward
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Folder
import androidx.compose.material.icons.filled.InsertDriveFile
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.Checkbox
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilledTonalButton
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.p2p.filetransfer.model.TransferItem
import com.p2p.filetransfer.ui.LogView
import com.p2p.filetransfer.ui.ProgressView
import com.p2p.filetransfer.ui.theme.P2PFileTransferTheme
import com.p2p.filetransfer.util.SizeFormatter
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch
import java.io.File
import java.io.FileOutputStream
import java.util.UUID

class MainActivity : ComponentActivity() {

    private val vm: MainViewModel by viewModels()

    private val pickFiles = registerForActivityResult(
        ActivityResultContracts.OpenMultipleDocuments()
    ) { uris -> uris.forEach { vm.addUriAsFile(it) } }

    private val pickFolder = registerForActivityResult(
        ActivityResultContracts.OpenDocumentTree()
    ) { uri -> uri?.let { vm.addUriAsFolder(it) } }

    private val pickSaveDir = registerForActivityResult(
        ActivityResultContracts.OpenDocumentTree()
    ) { uri -> uri?.let { vm.setSaveTreeUri(it) } }

    private val requestPerms = registerForActivityResult(
        ActivityResultContracts.RequestMultiplePermissions()
    ) { }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        requestPermissionsIfNeeded()
        requestIgnoreBatteryOptimizations()
        startTransferService()

        setContent {
            P2PFileTransferTheme {
                Surface(modifier = Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.background) {
                    MainScreen(
                        vm = vm,
                        onAddFiles = { pickFiles.launch(arrayOf("*/*")) },
                        onAddFolder = { pickFolder.launch(null) },
                        onPickSaveDir = { pickSaveDir.launch(null) },
                        onResetSaveDir = { vm.resetSaveDir() }
                    )
                }
            }
        }
    }

    private fun startTransferService() {
        val intent = Intent(this, P2PFileTransferService::class.java)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            startForegroundService(intent)
        } else {
            startService(intent)
        }
    }

    private fun requestPermissionsIfNeeded() {
        val perms = mutableListOf<String>()
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            perms.add(Manifest.permission.POST_NOTIFICATIONS)
            perms.add(Manifest.permission.NEARBY_WIFI_DEVICES)
        }
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU) {
            perms.add(Manifest.permission.ACCESS_FINE_LOCATION)
        }
        if (Build.VERSION.SDK_INT <= Build.VERSION_CODES.P) {
            perms.add(Manifest.permission.WRITE_EXTERNAL_STORAGE)
            perms.add(Manifest.permission.READ_EXTERNAL_STORAGE)
        }
        perms.add(Manifest.permission.ACCESS_WIFI_STATE)
        perms.add(Manifest.permission.CHANGE_WIFI_STATE)

        val missing = perms.filter {
            ContextCompat.checkSelfPermission(this, it) != PackageManager.PERMISSION_GRANTED
        }
        if (missing.isNotEmpty()) {
            requestPerms.launch(missing.toTypedArray())
        }
    }

    private fun requestIgnoreBatteryOptimizations() {
        try {
            val pm = getSystemService(POWER_SERVICE) as PowerManager
            if (!pm.isIgnoringBatteryOptimizations(packageName)) {
                val intent = Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS)
                intent.data = Uri.parse("package:" + packageName)
                startActivity(intent)
            }
        } catch (_: Exception) {
        }
    }
}

// ================= ViewModel =================

class MainViewModel(app: android.app.Application) : AndroidViewModel(app) {

    private val _items = MutableStateFlow<List<TransferItem>>(emptyList())
    val items: StateFlow<List<TransferItem>> = _items

    private val _selected = MutableStateFlow<Set<String>>(emptySet())
    val selected: StateFlow<Set<String>> = _selected

    private val _logs = MutableStateFlow<List<String>>(emptyList())
    val logs: StateFlow<List<String>> = _logs

    private val _devices = MutableStateFlow<List<com.p2p.filetransfer.model.DeviceNode>>(emptyList())
    val devices: StateFlow<List<com.p2p.filetransfer.model.DeviceNode>> = _devices

    private val _incoming = MutableStateFlow<com.p2p.filetransfer.model.IncomingRequest?>(null)
    val incoming: StateFlow<com.p2p.filetransfer.model.IncomingRequest?> = _incoming

    private val _resumeReminder = MutableStateFlow<com.p2p.filetransfer.model.ResumeReminder?>(null)
    val resumeReminder: StateFlow<com.p2p.filetransfer.model.ResumeReminder?> = _resumeReminder

    private val _saveDirDesc = MutableStateFlow("下载/P2PFileTransfer/")
    val saveDirDesc: StateFlow<String> = _saveDirDesc

    init {
        // 桥接 Service 的 DeviceRepository（Service 可能晚于 UI 启动，轮询等待）
        viewModelScope.launch {
            while (true) {
                val repo = P2PFileTransferService.instance?.deviceRepo
                if (repo != null) {
                    repo.nodesFlow.collect { _devices.value = it }
                    break
                }
                kotlinx.coroutines.delay(200)
            }
        }
        viewModelScope.launch {
            P2PFileTransferService.logs.collect { msg ->
                val stamp = java.text.SimpleDateFormat("HH:mm:ss", java.util.Locale.US).format(java.util.Date())
                val newList = (_logs.value + ("[" + stamp + "] " + msg)).takeLast(500)
                _logs.value = newList
            }
        }
        viewModelScope.launch {
            P2PFileTransferService.incomingRequest.collect { req ->
                _incoming.value = req
            }
        }
        viewModelScope.launch {
            P2PFileTransferService.resumeReminder.collect { r ->
                _resumeReminder.value = r
            }
        }
        viewModelScope.launch {
            P2PFileTransferService.saveDirPath.collect { d ->
                if (d.isNotEmpty()) _saveDirDesc.value = d
            }
        }
    }

    fun respondIncoming(id: String, accept: Boolean) {
        P2PFileTransferService.instance?.respondIncoming(id, accept)
        _incoming.value = null
    }

    fun respondResumeReminder(id: String, accept: Boolean) {
        P2PFileTransferService.instance?.respondResumeReminder(id, accept)
        _resumeReminder.value = null
    }

    fun setSaveTreeUri(uri: Uri) {
        val svc = P2PFileTransferService.instance
        if (svc != null) {
            svc.setSaveTreeUri(uri)
        } else {
            appendLog("[UI] 服务未就绪，无法设置保存目录")
        }
    }

    fun resetSaveDir() {
        P2PFileTransferService.instance?.clearSaveTreeUri()
    }

    fun toggleSelected(ip: String) {
        val cur = _selected.value
        _selected.value = if (cur.contains(ip)) cur - ip else cur + ip
    }

    fun addUriAsFile(uri: Uri) {
        val ctx = getApplication<android.app.Application>()
        viewModelScope.launch {
            try {
                val name = queryDisplayName(ctx, uri) ?: ("file_" + UUID.randomUUID())
                val cacheDir = File(ctx.cacheDir, "to_send")
                if (!cacheDir.exists()) cacheDir.mkdirs()
                val out = File(cacheDir, name)
                ctx.contentResolver.openInputStream(uri)?.use { input ->
                    FileOutputStream(out).use { fos -> input.copyTo(fos) }
                }
                _items.value = _items.value + TransferItem(path = out.absolutePath, isFolder = false, uriString = uri.toString())
            } catch (e: Exception) {
                appendLog("[UI] 添加文件失败: " + e.message)
            }
        }
    }

    fun addUriAsFolder(treeUri: Uri) {
        val ctx = getApplication<android.app.Application>()
        viewModelScope.launch {
            try {
                appendLog("[UI] 开始复制文件夹到缓存...")
                val rootName = treeDisplayName(ctx, treeUri) ?: "folder"
                val safeName = sanitizeFileName(rootName)
                val cacheRoot = File(ctx.cacheDir, "to_send/" + safeName + "_" + System.currentTimeMillis())
                if (cacheRoot.exists()) cacheRoot.deleteRecursively()
                cacheRoot.mkdirs()
                val rootDocId = try {
                    android.provider.DocumentsContract.getTreeDocumentId(treeUri)
                } catch (e: Exception) {
                    throw IllegalStateException("无法解析选择的目录: " + e.message)
                }
                val count = copyTree(ctx, treeUri, rootDocId, cacheRoot)
                appendLog("[UI] 文件夹已复制到缓存: " + safeName + " (" + count + " 个文件)")
                _items.value = _items.value + TransferItem(
                    path = cacheRoot.absolutePath,
                    isFolder = true,
                    uriString = treeUri.toString()
                )
            } catch (e: Exception) {
                appendLog("[UI] 添加文件夹失败: " + e.message)
            }
        }
    }

    /** 用 DocumentFile 拿 tree URI 的显示名（比 OpenableColumns 更可靠） */
    private fun treeDisplayName(ctx: android.content.Context, treeUri: Uri): String? {
        return try {
            androidx.documentfile.provider.DocumentFile.fromTreeUri(ctx, treeUri)?.name
        } catch (_: Exception) {
            null
        }
    }

    /** 清掉文件名里的非法字符 */
    private fun sanitizeFileName(name: String): String {
        return name.replace(Regex("[\\\\/:*?\"<>|]"), "_").trim().ifEmpty { "folder" }
    }

    /**
     * 递归复制 SAF 树到本地缓存目录。
     * 返回复制的文件数。
     *
     * 关键设计：
     *   - 递归时**始终传同一个 treeUri 和子节点的 docId**，而不是子节点的 URI。
     *     因为 `DocumentsContract.getDocumentId(uri)` 对 `content://.../tree/downloads`
     *     这种特殊 tree URI（DownloadsProvider）会抛 Invalid URI；
     *     而 `buildDocumentUriUsingTree(treeUri, childId)` 得到的子 URI 再次进入
     *     本函数时也会踩到同一问题。
     *   - 只用 `buildChildDocumentsUriUsingTree(treeUri, docId)` 枚举子项，
     *     该 API 对各种 SAF provider（含 DownloadsProvider）都可靠。
     */
    private fun copyTree(ctx: android.content.Context, treeUri: Uri, docId: String, destRoot: File): Int {
        var count = 0
        val childrenUri = android.provider.DocumentsContract
            .buildChildDocumentsUriUsingTree(treeUri, docId)
        ctx.contentResolver.query(childrenUri, null, null, null, null)?.use { cursor ->
            val nameCol = cursor.getColumnIndex(android.provider.DocumentsContract.Document.COLUMN_DISPLAY_NAME)
            val mimeCol = cursor.getColumnIndex(android.provider.DocumentsContract.Document.COLUMN_MIME_TYPE)
            val idCol = cursor.getColumnIndex(android.provider.DocumentsContract.Document.COLUMN_DOCUMENT_ID)
            if (nameCol < 0 || mimeCol < 0 || idCol < 0) return 0
            while (cursor.moveToNext()) {
                val name = cursor.getString(nameCol) ?: continue
                val mime = cursor.getString(mimeCol) ?: ""
                val childId = cursor.getString(idCol) ?: continue
                val out = File(destRoot, sanitizeFileName(name))
                if (mime == android.provider.DocumentsContract.Document.MIME_TYPE_DIR) {
                    out.mkdirs()
                    count += copyTree(ctx, treeUri, childId, out)
                } else {
                    val childUri = android.provider.DocumentsContract
                        .buildDocumentUriUsingTree(treeUri, childId)
                    try {
                        ctx.contentResolver.openInputStream(childUri)?.use { input ->
                            FileOutputStream(out).use { fos -> input.copyTo(fos) }
                        }
                        count++
                    } catch (e: Exception) {
                        appendLog("[UI] 复制文件失败: " + name + " - " + e.message)
                    }
                }
            }
        }
        return count
    }

    private fun queryDisplayName(ctx: android.content.Context, uri: Uri): String? {
        return try {
            ctx.contentResolver.query(uri, null, null, null, null)?.use { c ->
                if (c.moveToFirst()) {
                    val idx = c.getColumnIndex(android.provider.OpenableColumns.DISPLAY_NAME)
                    if (idx >= 0) c.getString(idx) else null
                } else null
            }
        } catch (_: Exception) {
            null
        }
    }

    fun removeItem(index: Int) {
        val list = _items.value.toMutableList()
        if (index in list.indices) {
            list.removeAt(index)
            _items.value = list
        }
    }

    fun moveUp(index: Int) {
        if (index <= 0) return
        val list = _items.value.toMutableList()
        val tmp = list[index - 1]
        list[index - 1] = list[index]
        list[index] = tmp
        _items.value = list
    }

    fun moveDown(index: Int) {
        val list = _items.value.toMutableList()
        if (index >= list.size - 1) return
        val tmp = list[index + 1]
        list[index + 1] = list[index]
        list[index] = tmp
        _items.value = list
    }

    fun clearItems() {
        _items.value = emptyList()
    }

    fun appendLog(msg: String) {
        _logs.value = (_logs.value + msg).takeLast(500)
    }

    fun send() {
        val svc = P2PFileTransferService.instance ?: return
        val targets = _selected.value.toList()
        if (targets.isEmpty()) {
            appendLog("[UI] 请先选择至少一台设备")
            return
        }
        if (_items.value.isEmpty()) {
            appendLog("[UI] 请先添加文件或文件夹")
            return
        }
        val itemsToSend = _items.value
        svc.sendItems(targets, itemsToSend) { successPaths ->
            // 从待发送列表中移除所有 IP 都发送成功的项目
            if (successPaths.isEmpty()) {
                appendLog("[UI] 所有项目发送失败，待发送列表保持不变")
            } else {
                val remain = _items.value.filter { it.path !in successPaths }
                val removedCount = _items.value.size - remain.size
                _items.value = remain
                appendLog("[UI] 已从待发送列表移除 " + removedCount + " 个成功项目" +
                        if (remain.isNotEmpty()) "，剩余 " + remain.size + " 个未成功" else "")
            }
        }
    }
}

// ================= Compose UI =================

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun MainScreen(
    vm: MainViewModel,
    onAddFiles: () -> Unit,
    onAddFolder: () -> Unit,
    onPickSaveDir: () -> Unit,
    onResetSaveDir: () -> Unit
) {
    val running by P2PFileTransferService.isRunning.collectAsState()
    val service = if (running) P2PFileTransferService.instance else null
    val devices by vm.devices.collectAsState()
    val selected by vm.selected.collectAsState()
    val items by vm.items.collectAsState()
    val logs by vm.logs.collectAsState()
    val sendProgress by P2PFileTransferService.sendProgress.collectAsState()
    val recvProgress by P2PFileTransferService.recvProgress.collectAsState()
    val autoScan by P2PFileTransferService.autoScan.collectAsState()
    val incoming by vm.incoming.collectAsState()
    val resumeReminder by vm.resumeReminder.collectAsState()
    val saveDirDesc by vm.saveDirDesc.collectAsState()

    var scanCidrDialog by remember { mutableStateOf(false) }
    var scanCidrText by remember { mutableStateOf("") }
    var saveDirDialog by remember { mutableStateOf(false) }

    // 保存目录操作弹窗
    if (saveDirDialog) {
        AlertDialog(
            onDismissRequest = { saveDirDialog = false },
            title = { Text("保存位置") },
            text = {
                Text(
                    "当前保存位置:\n" + saveDirDesc + "\n\n" +
                            "默认位置: 下载/P2PFileTransfer/\n\n" +
                            "点击“选择目录”用系统文件选择器选一个文件夹（如 Download、Documents 或外置存储上的任意目录）。"
                )
            },
            confirmButton = {
                TextButton(onClick = {
                    saveDirDialog = false
                    onPickSaveDir()
                }) { Text("选择目录") }
            },
            dismissButton = {
                TextButton(onClick = {
                    saveDirDialog = false
                    onResetSaveDir()
                }) { Text("恢复默认") }
            }
        )
    }

    // 网段扫描弹窗
    if (scanCidrDialog) {
        AlertDialog(
            onDismissRequest = { scanCidrDialog = false },
            title = { Text("网段扫描") },
            text = {
                Column {
                    Text("输入网段（如 192.168.1.0/24，留空则扫描所有子网）")
                    OutlinedTextField(
                        value = scanCidrText,
                        onValueChange = { scanCidrText = it },
                        singleLine = true,
                        modifier = Modifier.fillMaxWidth().padding(top = 8.dp)
                    )
                }
            },
            confirmButton = {
                TextButton(onClick = {
                    val cidr = scanCidrText.trim()
                    if (cidr.isEmpty()) service?.scanAllSubnets() else service?.scanSubnet(cidr)
                    scanCidrDialog = false
                }) { Text("扫描") }
            },
            dismissButton = {
                TextButton(onClick = { scanCidrDialog = false }) { Text("取消") }
            }
        )
    }

    // 续传提醒
    if (resumeReminder != null) {
        val rr = resumeReminder!!
        val preview = rr.items.take(10).joinToString("\n") { item ->
            val name = if (item.isFolder) ("[目录] " + (item.relPath ?: "?")) else ("[文件] " + File(item.filepath).name)
            val pct = if (item.totalSize > 0) (item.offset * 100 / item.totalSize) else 0
            "  " + name + " (" + pct + "%)"
        }
        AlertDialog(
            onDismissRequest = { vm.respondResumeReminder(rr.id, false) },
            title = { Text("续传提醒") },
            text = {
                Text(
                    "检测到设备 " + rr.hostname + " (" + rr.fromIp + ") 上线！\n" +
                            "以下文件未完成传输，是否继续上传？\n\n" + preview +
                            if (rr.items.size > 10) "\n  ... 还有 " + (rr.items.size - 10) + " 个文件" else ""
                )
            },
            confirmButton = {
                TextButton(onClick = { vm.respondResumeReminder(rr.id, true) }) { Text("续传") }
            },
            dismissButton = {
                TextButton(onClick = { vm.respondResumeReminder(rr.id, false) }) { Text("放弃") }
            }
        )
    }

    // 来件确认
    if (incoming != null) {
        val req = incoming!!
        AlertDialog(
            onDismissRequest = { vm.respondIncoming(req.id, false) },
            title = { Text(if (req.isFolder) "收到文件夹" else "收到文件") },
            text = {
                Text(
                    "来自: " + req.fromIp + "\n" +
                            "名称: " + req.fileName + "\n" +
                            "大小: " + SizeFormatter.humanSize(req.totalSize) +
                            "\n\n是否接收？"
                )
            },
            confirmButton = {
                TextButton(onClick = { vm.respondIncoming(req.id, true) }) { Text("接收") }
            },
            dismissButton = {
                TextButton(onClick = { vm.respondIncoming(req.id, false) }) { Text("拒绝") }
            }
        )
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = {
                    Column {
                        Text("P2P 文件互传", style = MaterialTheme.typography.titleMedium)
                        Text(
                            text = "保存到: " + saveDirDesc,
                            style = MaterialTheme.typography.labelSmall,
                            maxLines = 1,
                            overflow = TextOverflow.Ellipsis
                        )
                    }
                },
                actions = {
                    IconButton(onClick = { saveDirDialog = true }) {
                        Icon(Icons.Filled.Settings, contentDescription = "设置保存目录")
                    }
                },
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = MaterialTheme.colorScheme.primaryContainer,
                    titleContentColor = MaterialTheme.colorScheme.onPrimaryContainer
                )
            )
        },
        bottomBar = {
            Surface(color = MaterialTheme.colorScheme.surface, shadowElevation = 8.dp) {
                val canSend = selected.isNotEmpty() && items.isNotEmpty()
                Button(
                    onClick = { vm.send() },
                    enabled = canSend,
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(horizontal = 16.dp, vertical = 12.dp)
                        .height(52.dp)
                ) {
                    Text(
                        text = if (canSend)
                            "极速发送  ·  " + selected.size + " 台设备 / " + items.size + " 个文件"
                        else
                            "请选择设备和文件后发送",
                        style = MaterialTheme.typography.titleMedium
                    )
                }
            }
        }
    ) { padding ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding)
                .verticalScroll(rememberScrollState())
                .padding(horizontal = 12.dp)
        ) {
            Spacer(Modifier.height(8.dp))

            // ===== 设备区 =====
            Card(
                modifier = Modifier.fillMaxWidth(),
                colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceVariant)
            ) {
                Column(modifier = Modifier.padding(12.dp)) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Text(
                            "在线设备 (" + devices.size + ")",
                            style = MaterialTheme.typography.titleMedium,
                            modifier = Modifier.weight(1f)
                        )
                        FilledTonalButton(
                            onClick = { service?.broadcastSearch() },
                            contentPadding = PaddingValues(horizontal = 12.dp, vertical = 6.dp)
                        ) {
                            Text("广播搜索", style = MaterialTheme.typography.labelMedium)
                        }
                    }
                    Spacer(Modifier.height(8.dp))
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        OutlinedButton(
                            onClick = { service?.scanAllSubnets() },
                            modifier = Modifier.weight(1f),
                            contentPadding = PaddingValues(horizontal = 4.dp, vertical = 6.dp)
                        ) {
                            Text("扫描子网", style = MaterialTheme.typography.labelMedium)
                        }
                        OutlinedButton(
                            onClick = { scanCidrDialog = true },
                            modifier = Modifier.weight(1f),
                            contentPadding = PaddingValues(horizontal = 4.dp, vertical = 6.dp)
                        ) {
                            Text("网段", style = MaterialTheme.typography.labelMedium)
                        }
                        OutlinedButton(
                            onClick = { service?.toggleAutoScan() },
                            modifier = Modifier.weight(1f),
                            contentPadding = PaddingValues(horizontal = 4.dp, vertical = 6.dp)
                        ) {
                            Text(
                                if (autoScan) "后台:开" else "后台:关",
                                style = MaterialTheme.typography.labelMedium
                            )
                        }
                    }
                    Spacer(Modifier.height(8.dp))

                    if (devices.isEmpty()) {
                        Text(
                            "暂无设备，点击上方【广播搜索】或【扫描子网】发现设备",
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                            modifier = Modifier.padding(vertical = 12.dp)
                        )
                    } else {
                        devices.forEach { node ->
                            val checked = selected.contains(node.ip)
                            Row(
                                modifier = Modifier
                                    .fillMaxWidth()
                                    .clickable { vm.toggleSelected(node.ip) }
                                    .padding(vertical = 2.dp),
                                verticalAlignment = Alignment.CenterVertically
                            ) {
                                Checkbox(
                                    checked = checked,
                                    onCheckedChange = { vm.toggleSelected(node.ip) }
                                )
                                Column(modifier = Modifier.weight(1f)) {
                                    Text(
                                        node.hostname,
                                        style = MaterialTheme.typography.bodyLarge,
                                        maxLines = 1,
                                        overflow = TextOverflow.Ellipsis
                                    )
                                    Text(
                                        node.ip + "   [" + node.source + "]",
                                        style = MaterialTheme.typography.labelSmall,
                                        color = MaterialTheme.colorScheme.onSurfaceVariant
                                    )
                                }
                            }
                        }
                        Spacer(Modifier.height(8.dp))
                        OutlinedButton(
                            onClick = { service?.refreshOnline() },
                            modifier = Modifier.fillMaxWidth(),
                            contentPadding = PaddingValues(vertical = 6.dp)
                        ) {
                            Text("刷新在线状态", style = MaterialTheme.typography.labelMedium)
                        }
                    }
                }
            }

            Spacer(Modifier.height(10.dp))

            // ===== 待发送区 =====
            Card(
                modifier = Modifier.fillMaxWidth(),
                colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceVariant)
            ) {
                Column(modifier = Modifier.padding(12.dp)) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Text(
                            "待发送 (" + items.size + ")",
                            style = MaterialTheme.typography.titleMedium,
                            modifier = Modifier.weight(1f)
                        )
                        if (items.isNotEmpty()) {
                            TextButton(onClick = { vm.clearItems() }) { Text("清空") }
                        }
                    }
                    Spacer(Modifier.height(4.dp))
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        Button(
                            onClick = onAddFiles,
                            modifier = Modifier.weight(1f),
                            contentPadding = PaddingValues(horizontal = 4.dp, vertical = 8.dp)
                        ) {
                            Icon(Icons.Filled.Add, contentDescription = null, modifier = Modifier.size(16.dp))
                            Spacer(Modifier.width(4.dp))
                            Text("文件", style = MaterialTheme.typography.labelMedium)
                        }
                        Button(
                            onClick = onAddFolder,
                            modifier = Modifier.weight(1f),
                            contentPadding = PaddingValues(horizontal = 4.dp, vertical = 8.dp)
                        ) {
                            Icon(Icons.Filled.Folder, contentDescription = null, modifier = Modifier.size(16.dp))
                            Spacer(Modifier.width(4.dp))
                            Text("文件夹", style = MaterialTheme.typography.labelMedium)
                        }
                    }
                    Spacer(Modifier.height(8.dp))

                    if (items.isEmpty()) {
                        Text(
                            "暂无待发送项目，点击上方按钮添加",
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                            modifier = Modifier.padding(vertical = 12.dp)
                        )
                    } else {
                        items.forEachIndexed { idx, it ->
                            Row(
                                modifier = Modifier.fillMaxWidth().padding(vertical = 4.dp),
                                verticalAlignment = Alignment.CenterVertically
                            ) {
                                Icon(
                                    imageVector = if (it.isFolder) Icons.Filled.Folder else Icons.Filled.InsertDriveFile,
                                    contentDescription = null,
                                    modifier = Modifier.size(20.dp),
                                    tint = MaterialTheme.colorScheme.primary
                                )
                                Spacer(Modifier.width(8.dp))
                                Column(modifier = Modifier.weight(1f)) {
                                    Text(
                                        text = File(it.path).name,
                                        style = MaterialTheme.typography.bodyMedium,
                                        maxLines = 1,
                                        overflow = TextOverflow.Ellipsis
                                    )
                                    Text(
                                        text = if (it.isFolder) "文件夹" else SizeFormatter.humanSize(File(it.path).length()),
                                        style = MaterialTheme.typography.labelSmall,
                                        color = MaterialTheme.colorScheme.onSurfaceVariant
                                    )
                                }
                                IconButton(
                                    onClick = { vm.moveUp(idx) },
                                    enabled = idx > 0,
                                    modifier = Modifier.size(32.dp)
                                ) {
                                    Icon(Icons.Filled.ArrowUpward, contentDescription = "上移", modifier = Modifier.size(18.dp))
                                }
                                IconButton(
                                    onClick = { vm.moveDown(idx) },
                                    enabled = idx < items.size - 1,
                                    modifier = Modifier.size(32.dp)
                                ) {
                                    Icon(Icons.Filled.ArrowDownward, contentDescription = "下移", modifier = Modifier.size(18.dp))
                                }
                                IconButton(
                                    onClick = { vm.removeItem(idx) },
                                    modifier = Modifier.size(32.dp)
                                ) {
                                    Icon(Icons.Filled.Close, contentDescription = "移除", modifier = Modifier.size(18.dp))
                                }
                            }
                        }
                    }
                }
            }

            Spacer(Modifier.height(10.dp))

            // ===== 进度区 =====
            Card(
                modifier = Modifier.fillMaxWidth(),
                colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceVariant)
            ) {
                Column(modifier = Modifier.padding(12.dp)) {
                    ProgressView(title = "发送进度", progress = sendProgress)
                    Spacer(Modifier.height(4.dp))
                    ProgressView(title = "接收进度", progress = recvProgress)
                }
            }

            Spacer(Modifier.height(10.dp))

            // ===== 日志区 =====
            Card(
                modifier = Modifier.fillMaxWidth(),
                colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceVariant)
            ) {
                Column(modifier = Modifier.padding(12.dp)) {
                    Text("日志", style = MaterialTheme.typography.titleMedium)
                    Spacer(Modifier.height(4.dp))
                    Box(modifier = Modifier.fillMaxWidth().height(140.dp)) {
                        LogView(logs = logs)
                    }
                }
            }

            Spacer(Modifier.height(16.dp))
        }
    }
}
