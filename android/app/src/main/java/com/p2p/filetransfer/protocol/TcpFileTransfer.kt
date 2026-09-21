package com.p2p.filetransfer.protocol

import android.util.Log
import com.p2p.filetransfer.model.ReceiveRequest
import com.p2p.filetransfer.model.TransferItem
import com.p2p.filetransfer.model.TransferProgress
import com.p2p.filetransfer.repository.ResumeRepository
import com.p2p.filetransfer.util.HashUtil
import com.p2p.filetransfer.util.SizeFormatter
import com.p2p.filetransfer.util.SocketOptimizer
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.File
import java.io.IOException
import java.io.RandomAccessFile
import java.net.InetSocketAddress
import java.net.ServerSocket
import java.net.Socket
import java.util.zip.Deflater
import java.util.zip.DeflaterOutputStream
import java.util.zip.Inflater

/**
 * TCP 文件传输（发送与接收）。
 *
 * 与桌面端 Python 实现完全兼容：
 *   - 大端序二进制协议
 *   - 单文件 / 文件夹 / 续传偏移量查询
 *   - SHA-256 完整性校验
 *   - 文本文件 zlib 压缩
 *   - 断点续传
 */
class TcpFileTransfer(
    /**
     * 接收完成后，把文件/文件夹发布到用户期望的位置。
     * 参数: (源文件或文件夹, 是否为文件夹)
     * 返回: 目标显示描述；失败返回 null。
     */
    private val publisher: (File, Boolean) -> String?,
    private val resumeRepo: ResumeRepository,
    private val saveDirProvider: () -> File,
    private val log: (String) -> Unit,
    private val onSendProgress: (TransferProgress) -> Unit,
    private val onRecvProgress: (TransferProgress) -> Unit,
    private val onReceiveRequest: (ReceiveRequest) -> Unit,
    /**
     * 收到文件/文件夹传输请求时调用，返回 true 表示接受。
     * Service 侧会通过 UI 弹窗获取用户选择；若超时或用户拒绝返回 false。
     */
    private val onAcceptIncoming: suspend (ReceiveRequest) -> Boolean,
    private val onConfirmOverwrite: suspend (File) -> Boolean
) {

    private var serverSocket: ServerSocket? = null
    private var serverJob: Job? = null
    private var running = false

    // ================= TCP 服务器 =================

    fun startServer(parentScope: CoroutineScope) {
        if (running) return
        running = true
        serverJob = parentScope.launch(Dispatchers.IO) {
            try {
                val ss = ServerSocket()
                // 关键：SO_RCVBUF 必须在 bind 之前设置。
                // TCP window scale 在三次握手时按当时的缓冲区大小协商，
                // accept 之后再设置对已建立连接无效 —— 这就是之前接收速度
                // 只有 ~0.9MB/s 而电脑发送端显示 10MB/s 的根本原因。
                try {
                    ss.receiveBufferSize = Constants.SOCKET_BUFFER_SIZE
                    log("[TCP] 监听 socket RCVBUF 设为 " +
                            com.p2p.filetransfer.util.SizeFormatter.humanSize(
                                ss.receiveBufferSize.toLong()) +
                            "（期望 " + com.p2p.filetransfer.util.SizeFormatter.humanSize(
                                Constants.SOCKET_BUFFER_SIZE.toLong()) + "）")
                } catch (e: Exception) {
                    log("[TCP] 设置监听 socket RCVBUF 失败: " + e.message)
                }
                ss.reuseAddress = true
                ss.bind(InetSocketAddress(Constants.TCP_PORT), 50)
                serverSocket = ss
                log("[TCP] 监听端口 " + Constants.TCP_PORT + " 成功")
            } catch (e: Exception) {
                log("[TCP] 端口绑定失败: " + e.message)
                return@launch
            }
            val server = serverSocket ?: return@launch
            while (running) {
                try {
                    val conn = server.accept()
                    SocketOptimizer.optimize(conn)
                    conn.soTimeout = 15000
                    launch(Dispatchers.IO) { handleReceive(conn) }
                } catch (e: Exception) {
                    if (running) Log.d("TcpFileTransfer", "accept error: " + e.message)
                }
            }
        }
    }

    fun stopServer() {
        running = false
        try { serverSocket?.close() } catch (_: Exception) {}
        serverJob?.cancel()
    }

    // ================= 接收 =================

    private suspend fun handleReceive(socket: Socket) {
        try {
            val reader = ProtoReader(socket.getInputStream())
            socket.soTimeout = 0
            val peerIp = socket.inetAddress?.hostAddress?.substringBefore('%') ?: "unknown"
            // 循环处理同一 TCP 连接上的多条消息。
            //
            // 关键：桌面端 Python 在发送非压缩单文件时，会在同一个连接上：
            //   1) 先发 FLAG_QUERY_OFFSET 查询接收方已收字节数
            //   2) 收到回复后，紧接着在同一个 socket 上发 FLAG_FILE + 文件头 + 数据
            // 因此接收端必须在一次 accept 的连接里循环处理多条消息，
            // 处理完 QUERY_OFFSET 后不能关闭 socket，而要等客户端主动断开。
            while (true) {
                val flag = try {
                    reader.readByte()
                } catch (e: Exception) {
                    // 客户端主动关闭或读超时/中断，结束本连接
                    break
                }
                when (flag) {
                    Constants.FLAG_FILE -> receiveSingleFile(socket, reader, peerIp)
                    Constants.FLAG_FOLDER -> receiveFolder(socket, reader, peerIp)
                    Constants.FLAG_QUERY_OFFSET -> handleQueryOffset(socket, reader)
                    else -> {
                        log("[接收] 未知消息类型: " + flag)
                        break
                    }
                }
            }
        } catch (_: IOException) {
        } catch (e: Exception) {
            log("[接收] 连接异常: " + e.message)
        } finally {
            try { socket.close() } catch (_: Exception) {}
        }
    }

    /** 处理续传偏移量查询 */
    private fun handleQueryOffset(socket: Socket, reader: ProtoReader) {
        try {
            val totalSize = reader.readInt64()
            val filename = reader.readLengthPrefixedString()
            val localOffset = lookupLocalOffset(filename, totalSize)
            val out = socket.getOutputStream()
            out.write(longToBytes(localOffset))
            out.flush()
            log("[接收] 续传查询: " + filename + "，本地已收 " + SizeFormatter.humanSize(localOffset))
        } catch (e: Exception) {
            log("[接收] 续传查询异常: " + e.message)
            try {
                socket.getOutputStream().write(longToBytes(0))
                socket.getOutputStream().flush()
            } catch (_: Exception) {}
        }
    }

    private fun lookupLocalOffset(filename: String, totalSize: Long): Long {
        val saveDir = saveDirProvider()
        val f = File(saveDir, filename)
        // 路径穿越检查
        if (!isInside(saveDir, f)) {
            log("[安全] 拒绝非法文件名: " + filename)
            return 0
        }
        if (!f.exists()) return 0
        val len = f.length()
        // 本地大小 >= 总大小（说明要么已完整、要么是残留），
        // 都返回 0 让发送方从头重传；发完 SHA-256 会校验出完整性
        return if (len >= totalSize) 0 else len
    }

    private suspend fun receiveSingleFile(socket: Socket, reader: ProtoReader, peerIp: String) {
        val filename = reader.readLengthPrefixedString()
        val flags = reader.readByte()
        val compressed = (flags and Constants.FLAG_COMPRESS) != 0
        val resume = (flags and Constants.FLAG_RESUME) != 0
        val fileSize = reader.readInt64()
        var offset = if (resume) reader.readInt64() else 0

        val saveDir = saveDirProvider()
        saveDir.mkdirs()
        val savePath = File(saveDir, filename)
        if (!isInside(saveDir, savePath)) {
            log("[安全] 拒绝非法文件名: " + filename)
            socket.getOutputStream().write("NO".toByteArray())
            socket.getOutputStream().flush()
            return
        }

        val out = socket.getOutputStream()

        // 请求用户接受
        val accept = onAcceptIncoming(ReceiveRequest(peerIp, filename, fileSize, isFolder = false))
        if (!accept) {
            out.write("NO".toByteArray()); out.flush()
            log("[接收] 用户拒绝接收 " + filename + " (来自 " + peerIp + ")")
            return
        }

        val actualLocalSize = if (savePath.exists()) savePath.length() else -1L

        if (resume && offset > 0) {
            // 续传请求：本地文件必须存在且大小 == 发送方给出的 offset，
            // 否则返回 NO 并清理残留文件。
            // 原因：如果本地大小与 offset 不符，从 offset 位置写会破坏数据完整性，
            // 导致最终 SHA-256 校验必然失败。
            if (actualLocalSize != offset) {
                log("[接收] 续传偏移不符（本地=" + actualLocalSize + "，请求=" + offset + "），拒绝并清理残留")
                if (savePath.exists()) {
                    try { savePath.delete() } catch (_: Exception) {}
                }
                out.write("NO".toByteArray()); out.flush()
                return
            }
            // 直接续传
        } else {
            // 非续传：处理本地已有文件
            if (savePath.exists()) {
                val confirmed = onConfirmOverwrite(savePath)
                if (!confirmed) {
                    out.write("NO".toByteArray()); out.flush()
                    return
                }
                savePath.delete()
            }
            offset = 0
        }
        out.write("OK".toByteArray()); out.flush()

        onReceiveRequest(ReceiveRequest(peerIp, filename, fileSize))
        log("[接收] 文件: " + filename + " (" + SizeFormatter.humanSize(fileSize) + ")" +
                (if (resume) " [续传]" else "") + (if (compressed) " [压缩]" else "") +
                " 来自 " + peerIp)

        resumeRepo.saveState(peerIp, savePath.absolutePath, offset, fileSize, 0.0, "recv")

        val startTime = System.currentTimeMillis()
        val lastProgressSave = longArrayOf(0L)
        val lastPercentBucket = intArrayOf(-1)
        val result = receiveData(
            socket = socket,
            destFile = savePath,
            fileSize = fileSize,
            startOffset = offset,
            compressed = compressed,
            progressCallback = { received ->
                val elapsed = (System.currentTimeMillis() - startTime) / 1000.0
                val speed = if (elapsed > 0) (received - offset) / elapsed else 0.0
                val remain = if (speed > 0) (fileSize - received) / speed else 0.0
                val percent = if (fileSize > 0) (received * 100.0 / fileSize).toFloat() else 0f
                onRecvProgress(TransferProgress(
                    percent = percent,
                    speedText = SizeFormatter.formatSpeed(speed),
                    remainText = SizeFormatter.formatTime(remain),
                    statusText = "接收 " + filename + ": " + SizeFormatter.humanSize(received) +
                            " / " + SizeFormatter.humanSize(fileSize)
                ))
                // 实时保存接收方续传进度
                val now = System.currentTimeMillis()
                val bucket = if (fileSize > 0) ((received * 10) / fileSize).toInt() else 0
                if (now - lastProgressSave[0] >= 5000L || bucket != lastPercentBucket[0]) {
                    lastProgressSave[0] = now
                    lastPercentBucket[0] = bucket
                    resumeRepo.saveState(peerIp, savePath.absolutePath, received, fileSize, 0.0, "recv")
                }
            }
        )

        val remoteHash = reader.readFixedAscii(64)
        val sendMatched = result.success && result.hash == remoteHash
        out.write((if (sendMatched) "MATCH     " else "MISMATCH  ").toByteArray())
        out.flush()

        if (sendMatched) {
            val elapsed = (System.currentTimeMillis() - startTime) / 1000.0
            val speed = if (elapsed > 0) fileSize / elapsed else 0.0
            log("[接收] 文件 " + filename + " 校验通过 (" + SizeFormatter.formatSpeed(speed) + ")")
            resumeRepo.deleteState(peerIp, savePath.absolutePath, "recv")
            // 发布到用户选择的位置（默认: 下载/P2PFileTransfer/）
            kotlinx.coroutines.withContext(Dispatchers.IO) {
                val target = publisher(savePath, false)
                if (target != null) {
                    log("[接收] 已保存到: " + target)
                } else {
                    log("[接收] 保存失败，文件仍在应用私有目录: " + savePath.absolutePath)
                }
            }
        } else {
            log("[接收] 文件 " + filename + " 校验失败")
        }
        onRecvProgress(TransferProgress(0f, statusText = "就绪"))
    }

    private data class ReceiveResult(val success: Boolean, val hash: String?)

    /** 打开 RandomAccessFile 并 seek 到指定偏移；失败重试 3 次，全部失败返回 null */
    private suspend fun openRandomAccessFileOrNull(file: File, offset: Long): RandomAccessFile? {
        for (attempt in 0 until 3) {
            try {
                val r = RandomAccessFile(file, "rw")
                r.seek(offset)
                return r
            } catch (e: Exception) {
                log("[接收] 打开文件失败 (" + (attempt + 1) + "/3): " + e.message)
                kotlinx.coroutines.delay(1000)
            }
        }
        return null
    }

    /**
     * 接收数据并计算 SHA-256。
     * 续传时通过 RandomAccessFile.seek(offset) 从指定位置开始写。
     */
    private suspend fun receiveData(
        socket: Socket,
        destFile: File,
        fileSize: Long,
        startOffset: Long,
        compressed: Boolean,
        progressCallback: (Long) -> Unit
    ): ReceiveResult = withContext(Dispatchers.IO) {
        var received = startOffset
        // 文件打开重试 3 次
        val raf = openRandomAccessFileOrNull(destFile, startOffset)
            ?: run {
                log("[接收] 打开文件失败 3 次: " + destFile.absolutePath)
                return@withContext ReceiveResult(false, null)
            }

        val md = java.security.MessageDigest.getInstance("SHA-256")
        // 续传：先写入已有数据到哈希（通过重新读取）
        if (startOffset > 0) {
            destFile.inputStream().use { input ->
                var remaining = startOffset
                val buf = ByteArray(1024 * 1024)
                while (remaining > 0) {
                    val want = minOf(buf.size.toLong(), remaining).toInt()
                    val n = input.read(buf, 0, want)
                    if (n <= 0) break
                    md.update(buf, 0, n)
                    remaining -= n
                }
            }
        }

        val input = socket.getInputStream()
        // 读缓冲：动态"只扩不缩"（起始 3MB，高速时扩到 6MB/12MB）
        //   - 小缓冲 → 更多 read() 系统调用；大缓冲在高带宽链路上能一次排空内核队列
        //   - 只扩不缩，避免慢速网络陷入"缓冲变小 → 更慢"的负反馈
        //
        // 优化（v15.3）：**移除中间的 writeBuf**。
        //   旧流程：read → readBuf → arraycopy → writeBuf → raf.write  ← 多一次 3MB memcpy
        //   新流程：read → readBuf → raf.write                          ← 直接落盘
        // 由于 readBuf 本身就有 3MB，每次读到的数据量通常 ≥1MB，
        // 分批凑 1MB 写盘的收益微乎其微，反而多了一次 memcpy 和多次小 write。
        var dynamicReadSize = Constants.BUFFER_SIZE
        var readBuf = ByteArray(dynamicReadSize)
        // 压缩文件才需要独立的解压输出缓冲（提前分配，避免循环内 null 检查）
        val inflateWorkBuf: ByteArray? = if (compressed) ByteArray(256 * 1024) else null
        val inflater = if (compressed) Inflater() else null
        // lastActivity 在循环内首次读取前必定被赋值（见 while 循环体的首行），
        // 因此声明时不设初值（避免冗余初始化警告）
        var lastActivity: Long
        var lastProgress = System.currentTimeMillis()
        // 速度采样（每 1 秒一次，保留最近 5 个）——用于静默超时判断和缓冲扩容
        var lastSampleTime = System.currentTimeMillis()
        var lastSampleBytes = received
        val speedSamples = ArrayDeque<Double>()
        // 上次扩容时间，避免频繁重分配（至少间隔 2 秒）
        var lastGrowTime = System.currentTimeMillis()

        try {
            while (received < fileSize) {
                val want = minOf(readBuf.size.toLong(), fileSize - received).toInt()
                val n = input.read(readBuf, 0, want)
                if (n < 0) throw IOException("接收中断")
                lastActivity = System.currentTimeMillis()

                if (inflater != null && inflateWorkBuf != null) {
                    // 压缩：解压后直接落盘（复用同一个 256KB 缓冲，跨外层循环不重新分配）
                    inflater.setInput(readBuf, 0, n)
                    while (!inflater.needsInput()) {
                        val got = inflater.inflate(inflateWorkBuf)
                        if (got <= 0) break
                        md.update(inflateWorkBuf, 0, got)
                        raf.write(inflateWorkBuf, 0, got)
                    }
                } else {
                    // 非压缩：直接写 readBuf（零中间拷贝）
                    md.update(readBuf, 0, n)
                    raf.write(readBuf, 0, n)
                }
                received += n

                val now = System.currentTimeMillis()
                // 进度回调节流：仅按时间（500ms 一次），
                // 去掉原来的 "或 1MB 触发" —— 高速传输时每秒几十次 UI 更新
                // 会引发大量 Compose 重组，反而拖慢接收线程。
                if (now - lastProgress >= 500) {
                    progressCallback(received)
                    lastProgress = now
                }
                // 速度采样（用于静默超时判断 + 缓冲扩容）
                val sampleElapsed = (now - lastSampleTime) / 1000.0
                if (sampleElapsed >= 1.0) {
                    val speed = (received - lastSampleBytes) / sampleElapsed
                    speedSamples.addLast(speed)
                    if (speedSamples.size > 5) speedSamples.removeFirst()
                    lastSampleTime = now
                    lastSampleBytes = received

                    // 动态扩容（只扩不缩）：实测平均速度高于当前缓冲档位，且距上次扩容 ≥ 2 秒
                    val avgSpeed = speedSamples.average()
                    val idealSize = Constants.recvBufferSizeFor(avgSpeed)
                    if (idealSize > dynamicReadSize &&
                        now - lastGrowTime >= 2000L &&
                        received + idealSize < fileSize) {
                        // 仅在还有较多数据要接收时扩容，避免临近结束时无谓分配
                        dynamicReadSize = idealSize
                        readBuf = ByteArray(dynamicReadSize)
                        lastGrowTime = now
                        log("[接收] 读缓冲扩容至 " +
                                com.p2p.filetransfer.util.SizeFormatter.humanSize(dynamicReadSize.toLong()) +
                                "（速度 " + com.p2p.filetransfer.util.SizeFormatter.formatSpeed(avgSpeed) + "）")
                    }
                }
                // 静默超时（仅低网络下生效）
                val currentSpeed = if (speedSamples.isEmpty()) Double.MAX_VALUE else speedSamples.average()
                if (currentSpeed < Constants.LOW_NETWORK_THRESHOLD &&
                    now - lastActivity > Constants.RECV_ACTIVITY_TIMEOUT_MS) {
                    throw IOException("接收静默超时")
                }
            }
            if (inflater != null && inflateWorkBuf != null) {
                while (!inflater.finished()) {
                    val got = inflater.inflate(inflateWorkBuf)
                    if (got <= 0) break
                    md.update(inflateWorkBuf, 0, got)
                    raf.write(inflateWorkBuf, 0, got)
                }
            }
        } catch (e: Exception) {
            log("[接收] 数据接收异常: " + e.message)
            try { raf.close() } catch (_: Exception) {}
            return@withContext ReceiveResult(false, null)
        } finally {
            try { inflater?.end() } catch (_: Exception) {}
        }
        try { raf.close() } catch (_: Exception) {}

        val hex = md.digest().joinToString("") { String.format("%02x", it) }
        ReceiveResult(true, hex)
    }

    // ================= 发送 =================

    /**
     * 向单个 IP 串行发送所有项目。
     * [onItemDone] 每个项目完成后回调 (path, success)。
     */
    suspend fun sendFiles(
        targetIp: String,
        items: List<TransferItem>,
        onItemDone: (String, Boolean) -> Unit
    ): Unit = withContext(Dispatchers.IO) {
        if (items.isEmpty()) return@withContext
        log("[发送] 向 " + targetIp + " 串行发送 " + items.size + " 个项目")
        for (item in items) {
            val success = sendItemWithRetry(targetIp, item, Constants.MAX_RETRIES)
            onItemDone(item.path, success)
        }
    }

    private suspend fun sendItemWithRetry(targetIp: String, item: TransferItem, maxRetries: Int): Boolean {
        var retry = 0
        while (retry < maxRetries) {
            var socket: Socket? = null
            try {
                if (item.isFolder) {
                    val s = openSocket(targetIp)
                    socket = s
                    sendFolder(s, File(item.path), targetIp)
                } else {
                    sendSingleFile(targetIp, File(item.path))
                }
                log("[发送] 项目 " + item.path + " 发送成功")
                return true
            } catch (e: Exception) {
                retry++
                log("[发送] 项目 " + item.path + " 发送失败 (尝试 " + retry + "/" + maxRetries + "): " + e.message)
                if (retry >= maxRetries) return false
                val backoff = minOf(1L shl retry, 64L) * 1000L
                log("[发送] 等待 " + (backoff / 1000) + " 秒后重试...")
                kotlinx.coroutines.delay(backoff)
            } finally {
                try { socket?.close() } catch (_: Exception) {}
            }
        }
        return false
    }

    /** 打开到目标 IP 的 TCP 连接并应用优化 */
    private fun openSocket(targetIp: String): Socket {
        val s = Socket()
        s.soTimeout = 30000
        s.connect(InetSocketAddress(targetIp, Constants.TCP_PORT), 30000)
        s.soTimeout = 0
        SocketOptimizer.optimize(s)
        return s
    }

    /**
     * 发送单个文件。
     * 若非压缩且存在续传可能，会先在一个临时连接上执行 QUERY_OFFSET 协商，
     * 然后使用新连接正式发送（与桌面端 Python 行为等价，但连接管理更清晰）。
     */
    private suspend fun sendSingleFile(targetIp: String, file: File) {
        val totalSize = file.length()
        val filename = file.name
        val compressRequested = Constants.isTextFile(filename)
        // offset 在下面必定被赋值（min/max 计算），故声明时不设初值
        var offset: Long

        // 加载双方续传状态
        val senderState = resumeRepo.loadState(targetIp, file.absolutePath, "sender")
        val recvState = resumeRepo.loadState(targetIp, file.absolutePath, "recv")
        val senderOffset = if (senderState != null && senderState.totalSize == totalSize &&
                senderState.offset > 0 && senderState.offset < totalSize) senderState.offset else 0L
        val recvOffset = if (recvState != null && recvState.totalSize == totalSize) recvState.offset else 0L
        offset = if (senderOffset > 0 && recvOffset > 0) minOf(senderOffset, recvOffset)
                 else maxOf(senderOffset, recvOffset)
        if (offset >= totalSize) offset = 0

        var compressedBytes: ByteArray? = null
        if (compressRequested) {
            if (offset > 0) {
                log("[发送] 压缩文件不支持续传，从头发送")
                offset = 0
            }
            compressedBytes = compressFile(file)
        }

        resumeRepo.saveState(targetIp, file.absolutePath, offset, totalSize, file.lastModified() / 1000.0, "sender")

        // 在线协商偏移量（仅非压缩且 offset < totalSize 时）
        if (compressedBytes == null && offset < totalSize) {
            val remoteOffset = queryRemoteOffset(targetIp, filename, totalSize)
            if (remoteOffset in 1..(totalSize - 1)) {
                if (remoteOffset > offset) {
                    offset = remoteOffset
                    log("[发送] 协商续传: 对方已收到 " + SizeFormatter.humanSize(remoteOffset))
                }
            } else if (remoteOffset == 0L) {
                log("[发送] 协商续传: 对方没有该文件记录")
            } else {
                offset = 0
            }
            resumeRepo.saveState(targetIp, file.absolutePath, offset, totalSize, file.lastModified() / 1000.0, "sender")
        }

        val socket = openSocket(targetIp)
        try {
            sendSingleFileWithSocket(socket, file, targetIp, filename, totalSize, offset, compressedBytes)
        } finally {
            try { socket.close() } catch (_: Exception) {}
        }
    }

    private suspend fun sendSingleFileWithSocket(
        socket: Socket,
        file: File,
        targetIp: String,
        filename: String,
        totalSize: Long,
        offset: Long,
        compressedBytes: ByteArray?
    ) {
        val compress = compressedBytes != null
        val sendSize = compressedBytes?.size?.toLong() ?: totalSize
        var flags = 0
        if (compress) flags = flags or Constants.FLAG_COMPRESS
        var useOffset = offset
        if (useOffset > 0 && !compress) {
            flags = flags or Constants.FLAG_RESUME
        } else {
            useOffset = 0
        }

        val out = socket.getOutputStream()
        val writer = ProtoWriter(out)
        writer.writeByte(Constants.FLAG_FILE)
        writer.writeLengthPrefixedString(filename)
        writer.writeByte(flags)
        writer.writeInt64(sendSize)
        if ((flags and Constants.FLAG_RESUME) != 0) writer.writeInt64(useOffset)
        writer.flush()

        val ack = ProtoReader(socket.getInputStream()).readFixedAscii(2)
        if (ack != "OK") {
            log("[发送] 对方拒绝接收文件")
            return
        }

        log("[发送] 文件: " + filename + " (" + SizeFormatter.humanSize(totalSize) + ")" +
                (if (useOffset > 0) " [续传 " + SizeFormatter.humanSize(useOffset) + "]" else "") +
                (if (compress) " [压缩]" else ""))

        val startTime = System.currentTimeMillis()
        val finalOffset = useOffset
        if (compress && compressedBytes != null) {
            out.write(compressedBytes)
            out.flush()
            onSendProgress(TransferProgress(100f, statusText = "发送 " + filename + " 完成"))
        } else {
            // 实时保存续传进度：每 5 秒 或 每 10% 保存一次
            val lastProgressSave = longArrayOf(0L)
            val lastPercentBucket = intArrayOf(-1)
            val mtimeSec = file.lastModified() / 1000.0
            sendFileDataFast(socket, file, sendSize, finalOffset) { sent ->
                val elapsed = (System.currentTimeMillis() - startTime) / 1000.0
                val speed = if (elapsed > 0) (sent - finalOffset) / elapsed else 0.0
                val remain = if (speed > 0) (totalSize - sent) / speed else 0.0
                val percent = if (totalSize > 0) (sent * 100.0 / totalSize).toFloat() else 0f
                onSendProgress(TransferProgress(
                    percent = percent,
                    speedText = SizeFormatter.formatSpeed(speed),
                    remainText = SizeFormatter.formatTime(remain),
                    statusText = "发送 " + filename + ": " + SizeFormatter.humanSize(sent) +
                            " / " + SizeFormatter.humanSize(totalSize)
                ))
                // 实时保存发送方续传进度
                val now = System.currentTimeMillis()
                val bucket = if (totalSize > 0) ((sent * 10) / totalSize).toInt() else 0
                if (now - lastProgressSave[0] >= 5000L || bucket != lastPercentBucket[0]) {
                    lastProgressSave[0] = now
                    lastPercentBucket[0] = bucket
                    resumeRepo.saveState(targetIp, file.absolutePath, sent, totalSize, mtimeSec, "sender")
                }
            }
        }

        // 发送原始文件的 SHA-256
        val hash = HashUtil.sha256(file)
        out.write(hash.toByteArray(Charsets.UTF_8))
        out.flush()

        val result = ProtoReader(socket.getInputStream()).readFixedAscii(10)
        val elapsed = (System.currentTimeMillis() - startTime) / 1000.0
        val speed = if (elapsed > 0) sendSize / elapsed else 0.0
        if (result == "MATCH") {
            log("[发送] 文件 " + filename + " 对方校验通过 (" + SizeFormatter.formatSpeed(speed) + ")")
            resumeRepo.deleteState(targetIp, file.absolutePath, "sender")
            resumeRepo.deleteState(targetIp, file.absolutePath, "recv")
        } else {
            log("[发送] 文件 " + filename + " 对方校验失败")
        }
        onSendProgress(TransferProgress(0f, statusText = "就绪"))
    }

    /**
     * 在一个临时连接上执行 QUERY_OFFSET 协商，获取接收方已保存的字节数。
     * 接收方处理完查询后会关闭连接。
     */
    private fun queryRemoteOffset(targetIp: String, filename: String, totalSize: Long): Long {
        var temp: Socket? = null
        return try {
            val s = Socket()
            temp = s
            s.soTimeout = 5000
            s.connect(InetSocketAddress(targetIp, Constants.TCP_PORT), 5000)
            SocketOptimizer.optimize(s)
            val out = s.getOutputStream()
            val writer = ProtoWriter(out)
            writer.writeByte(Constants.FLAG_QUERY_OFFSET)
            writer.writeInt64(totalSize)
            writer.writeLengthPrefixedString(filename)
            writer.flush()

            val reader = ProtoReader(s.getInputStream())
            reader.readInt64()
        } catch (e: Exception) {
            log("[发送] 协商续传偏移量失败: " + e.message)
            0L
        } finally {
            try { temp?.close() } catch (_: Exception) {}
        }
    }

    private fun compressFile(file: File): ByteArray? {
        return try {
            val raw = file.readBytes()
            val out = java.io.ByteArrayOutputStream()
            DeflaterOutputStream(out, Deflater(6)).use { it.write(raw) }
            out.toByteArray()
        } catch (e: Exception) {
            log("[发送] 压缩失败: " + e.message)
            null
        }
    }

    /**
     * 发送文件数据，自适应块大小，低网络静默超时。
     *
     * 自适应策略（与桌面端 Python 一致）：
     *   每 1 秒采样一次实际发送速度，取最近 5 个样本平均，
     *   根据平均速度动态调整单次读写块大小：
     *     < 128KB/s -> 16KB, < 512KB/s -> 64KB, < 1MB/s -> 128KB,
     *     < 5MB/s   -> 256KB, < 20MB/s -> 512KB, < 50MB/s -> 1MB, 否则 2MB
     */
    private suspend fun sendFileDataFast(
        socket: Socket,
        file: File,
        fileSize: Long,
        offset: Long,
        progress: (Long) -> Unit
    ): Unit = withContext(Dispatchers.IO) {
        // 用 BufferedInputStream 减少系统调用（尤其对机械盘 / SD 卡）
        // 缓冲大小与 BUFFER_SIZE 相同（1MB），足够掩盖单次 read 的延迟
        val rawInput = file.inputStream()
        val input = java.io.BufferedInputStream(rawInput, Constants.BUFFER_SIZE)
        if (offset > 0) {
            var remaining = offset
            while (remaining > 0) {
                val skipped = input.skip(remaining)
                if (skipped <= 0) { input.read(); remaining-- } else remaining -= skipped
            }
        }
        val out = socket.getOutputStream()
        // 大块发送：初始 1MB，随速度动态调整（上限 4MB）
        var adaptiveChunk = Constants.BUFFER_SIZE           // 初始 1MB（之前 256KB）
        var buf = ByteArray(adaptiveChunk)

        var sent = offset
        var lastSampleTime = System.currentTimeMillis()
        var lastBytes = sent
        var avgSpeed = Double.MAX_VALUE
        // lastActivity 在循环内首次读取前必定被赋值（见 while 循环体的首行）
        var lastActivity: Long
        val speedSamples = ArrayDeque<Double>()

        try {
            while (sent < fileSize) {
                val want = minOf(adaptiveChunk.toLong(), fileSize - sent).toInt()
                if (buf.size < want) buf = ByteArray(want)
                val n = input.read(buf, 0, want)
                if (n <= 0) break
                out.write(buf, 0, n)
                sent += n
                lastActivity = System.currentTimeMillis()

                val now = System.currentTimeMillis()
                val elapsed = (now - lastSampleTime) / 1000.0
                if (elapsed >= 1.0) {
                    val sampleSpeed = (sent - lastBytes) / elapsed
                    speedSamples.addLast(sampleSpeed)
                    if (speedSamples.size > 5) speedSamples.removeFirst()
                    avgSpeed = speedSamples.average()
                    val newChunk = Constants.adaptiveChunkFor(avgSpeed)
                    if (newChunk != adaptiveChunk) adaptiveChunk = newChunk
                    lastSampleTime = now
                    lastBytes = sent
                    lastActivity = now
                }
                if (avgSpeed < Constants.LOW_NETWORK_THRESHOLD &&
                    now - lastActivity > Constants.ACTIVITY_TIMEOUT_MS) {
                    throw IOException("发送静默超时")
                }
                progress(sent)
            }
            out.flush()
        } finally {
            try { input.close() } catch (_: Exception) {}
        }
    }

    // ================= 文件夹 =================

    private data class ManifestEntry(val relPath: String, val size: Long, val mtime: Double)

    private fun getFolderManifest(folder: File): List<ManifestEntry> {
        val result = mutableListOf<ManifestEntry>()
        folder.walkTopDown().forEach { f ->
            if (f.isFile) {
                val rel = f.relativeTo(folder).path.replace('\\', '/')
                result.add(ManifestEntry(rel, f.length(), f.lastModified() / 1000.0))
            }
        }
        return result
    }

    private suspend fun sendFolder(socket: Socket, folder: File, targetIp: String) {
        val folderName = folder.name
        val manifest = getFolderManifest(folder)
        val fileCount = manifest.size
        val totalBytes = manifest.sumOf { it.size }
        log("[发送] 文件夹: " + folderName + " (" + fileCount + " 个文件, " + SizeFormatter.humanSize(totalBytes) + ")")

        val out = socket.getOutputStream()
        val writer = ProtoWriter(out)
        writer.writeByte(Constants.FLAG_FOLDER)
        writer.writeLengthPrefixedString(folderName)
        writer.writeInt32(fileCount)
        writer.writeInt64(totalBytes)
        writer.flush()

        val reader = ProtoReader(socket.getInputStream())
        val ack = reader.readFixedAscii(2)
        if (ack != "OK") {
            log("[发送] 对方拒绝传输（文件夹已存在且不覆盖）")
            return
        }

        // 发送清单
        for (e in manifest) {
            writer.writeLengthPrefixedString(e.relPath)
            writer.writeInt64(e.size)
            writer.writeDouble(e.mtime)
        }
        writer.flush()

        val manifestAck = reader.readExact(1)[0].toInt()
        if (manifestAck != 0x01) {
            log("[发送] 接收端清单处理失败")
            return
        }

        // 接收续传指令
        val actions = mutableListOf<Pair<String, Long>>()
        for (i in 0 until fileCount) {
            val b = reader.readByte()
            when (b) {
                0x00 -> actions.add(Pair("skip", 0L))
                0x01 -> actions.add(Pair("full", 0L))
                0x02 -> {
                    val off = reader.readInt64()
                    actions.add(Pair("resume", off))
                }
                else -> actions.add(Pair("full", 0L))
            }
        }

        var sentTotal = 0L
        val startTime = System.currentTimeMillis()
        var failed = 0

        for (idx in 0 until fileCount) {
            val entry = manifest[idx]
            val fullFile = File(folder, entry.relPath)
            var (act, offset) = actions[idx]
            if (act == "skip") {
                log("[发送]   ✓ " + entry.relPath + " (已存在，跳过)")
                resumeRepo.deleteFolderState(targetIp, folderName, entry.relPath)
                continue
            }

            // 本地状态
            val state = resumeRepo.loadFolderState(targetIp, folderName, entry.relPath, null)
            val stateOffset = if (state != null && state.totalSize == entry.size && state.offset > 0) state.offset else 0L
            var effectiveOffset = if (act == "resume" && offset > stateOffset) offset else stateOffset
            if (effectiveOffset in 1 until entry.size) {
                act = "resume"
                offset = effectiveOffset
            } else {
                offset = 0
            }

            resumeRepo.saveFolderState(targetIp, folderName, entry.relPath, offset, entry.size, entry.mtime)

            var fileOk = false
            for (attempt in 0 until Constants.MAX_RETRIES) {
                try {
                    log("[发送]   (" + (idx + 1) + "/" + fileCount + ") " + entry.relPath +
                            " (" + SizeFormatter.humanSize(entry.size) + ")" +
                            (if (act == "resume" && offset > 0) " [续传 " + SizeFormatter.humanSize(offset) + "]" else ""))

                    var compress = false
                    var compressedBytes: ByteArray? = null
                    if (act == "full" && Constants.isTextFile(entry.relPath)) {
                        compressedBytes = compressFile(fullFile)
                        compress = compressedBytes != null
                    }
                    val sendSize = compressedBytes?.size?.toLong() ?: entry.size

                    var flags = 0
                    if (compress) flags = flags or Constants.FLAG_COMPRESS
                    var useOffset = if (act == "resume") offset else 0L
                    if (useOffset > 0 && !compress) flags = flags or Constants.FLAG_RESUME else useOffset = 0

                    writer.writeLengthPrefixedString(entry.relPath)
                    writer.writeByte(flags)
                    writer.writeInt64(sendSize)
                    if ((flags and Constants.FLAG_RESUME) != 0) writer.writeInt64(useOffset)
                    writer.flush()

                    val a = ProtoReader(socket.getInputStream()).readFixedAscii(2)
                    if (a != "OK") {
                        log("[发送] 对方拒绝接收文件")
                        return
                    }

                    if (compressedBytes != null) {
                        out.write(compressedBytes)
                        out.flush()
                    } else {
                        // 进度条 = **当前文件**的进度（sent 含续传起点 useOffset）
                        // statusText 同时显示当前文件 + 全局字节进度
                        val globalBase = sentTotal
                        sendFileDataFast(socket, fullFile, entry.size, useOffset) { sent ->
                            val filePercent = if (entry.size > 0)
                                (sent * 100f / entry.size).coerceIn(0f, 100f) else 0f
                            val globalSent = globalBase + sent
                            val globalPercent = if (totalBytes > 0)
                                (globalSent * 100f / totalBytes).coerceIn(0f, 100f) else 0f
                            onSendProgress(TransferProgress(
                                percent = filePercent,
                                statusText = "当前: " + entry.relPath + "  (" +
                                        SizeFormatter.humanSize(sent) + " / " + SizeFormatter.humanSize(entry.size) + ")  ·  " +
                                        "整体: " + String.format("%.1f%%", globalPercent) + "  (" +
                                        SizeFormatter.humanSize(globalSent) + " / " + SizeFormatter.humanSize(totalBytes) + ")"
                            ))
                        }
                    }

                    val hash = HashUtil.sha256(fullFile)
                    out.write(hash.toByteArray(Charsets.UTF_8))
                    out.flush()

                    val res = ProtoReader(socket.getInputStream()).readFixedAscii(10)
                    if (res == "MATCH") {
                        log("[发送]   ✓ " + entry.relPath)
                        sentTotal += entry.size
                        fileOk = true
                        resumeRepo.deleteFolderState(targetIp, folderName, entry.relPath)
                        break
                    } else {
                        log("[发送]   ✗ " + entry.relPath + " 校验失败")
                    }
                } catch (e: Exception) {
                    log("[发送]   ✗ " + entry.relPath + " 发送异常: " + e.message)
                    // 重连
                    break
                }
            }
            if (!fileOk) {
                log("[发送]   ✗ " + entry.relPath + " 重试 " + Constants.MAX_RETRIES + " 次均失败，放弃")
                failed++
            }
        }

        val elapsed = (System.currentTimeMillis() - startTime) / 1000.0
        val speed = if (elapsed > 0) sentTotal / elapsed else 0.0
        if (failed == 0) {
            log("[发送] 文件夹 " + folderName + " 发送完成，全部文件校验通过 (" + SizeFormatter.formatSpeed(speed) + ")")
        } else {
            log("[发送] 文件夹 " + folderName + " 发送完成，" + failed + " 个文件失败")
        }
        onSendProgress(TransferProgress(0f, statusText = "就绪"))
    }

    // ================= 接收文件夹 =================

    private suspend fun receiveFolder(socket: Socket, reader: ProtoReader, peerIp: String) {
        val folderName = reader.readLengthPrefixedString()
        val fileCount = reader.readInt32()
        val totalBytes = reader.readInt64()

        val saveDir = saveDirProvider()
        val targetDir = File(saveDir, folderName)
        if (!isInside(saveDir, targetDir)) {
            log("[安全] 拒绝非法文件夹名: " + folderName)
            socket.getOutputStream().write("NO".toByteArray())
            socket.getOutputStream().flush()
            return
        }

        val out = socket.getOutputStream()

        // 请求用户接受
        val accept = onAcceptIncoming(ReceiveRequest(peerIp, folderName, totalBytes, isFolder = true))
        if (!accept) {
            out.write("NO".toByteArray()); out.flush()
            log("[接收] 用户拒绝接收文件夹 " + folderName + " (来自 " + peerIp + ")")
            return
        }

        if (targetDir.exists()) {
            val confirmed = onConfirmOverwrite(targetDir)
            if (!confirmed) {
                out.write("NO".toByteArray()); out.flush()
                return
            }
        }
        out.write("OK".toByteArray()); out.flush()
        targetDir.mkdirs()
        val basePath = targetDir.canonicalFile

        log("[接收] 文件夹: " + folderName + " (" + fileCount + " 个文件, " + SizeFormatter.humanSize(totalBytes) + ") 来自 " + peerIp)

        // 接收清单
        val manifest = mutableListOf<ManifestEntry>()
        for (i in 0 until fileCount) {
            val rel = reader.readLengthPrefixedString()
            val size = reader.readInt64()
            val mtime = reader.readDouble()
            manifest.add(ManifestEntry(rel, size, mtime))
        }

        // 决定处理方式
        val actions = mutableListOf<Pair<String, Long>>()
        for (e in manifest) {
            val dest = File(targetDir, e.relPath)
            if (!isInside(basePath, dest)) {
                log("[安全] 拒绝非法路径: " + e.relPath)
                out.write(byteArrayOf(0x00))
                out.flush()
                return
            }
            if (dest.exists()) {
                val localSize = dest.length()
                val localMtime = dest.lastModified() / 1000.0
                when {
                    localSize == e.size && Math.abs(localMtime - e.mtime) < 0.1 -> actions.add(Pair("skip", 0L))
                    localSize < e.size -> actions.add(Pair("resume", localSize))
                    else -> actions.add(Pair("full", 0L))
                }
            } else {
                actions.add(Pair("full", 0L))
            }
        }

        out.write(byteArrayOf(0x01))
        for ((act, off) in actions) {
            when (act) {
                "skip" -> out.write(byteArrayOf(0x00))
                "full" -> out.write(byteArrayOf(0x01))
                else -> {
                    out.write(byteArrayOf(0x02))
                    out.write(longToBytes(off))
                }
            }
        }
        out.flush()

        var receivedGlobal = 0L
        var failed = 0
        val startTime = System.currentTimeMillis()

        for (idx in 0 until fileCount) {
            val entry = manifest[idx]
            val (act, _) = actions[idx]
            if (act == "skip") {
                log("[接收]   ✓ " + entry.relPath + " (已存在，跳过)")
                continue
            }
            val destPath = File(targetDir, entry.relPath)
            destPath.parentFile?.mkdirs()

            var fileOk = false
            for (attempt in 0 until Constants.MAX_RETRIES) {
                try {
                    val relPath = reader.readLengthPrefixedString()
                    val flags = reader.readByte()
                    val compressed = (flags and Constants.FLAG_COMPRESS) != 0
                    val resume = (flags and Constants.FLAG_RESUME) != 0
                    val fileSize = reader.readInt64()
                    var offset = if (resume) reader.readInt64() else 0L

                    if (!isInside(basePath, destPath)) {
                        log("[安全] 拒绝非法路径: " + relPath)
                        out.write("NO".toByteArray()); out.flush()
                        return
                    }

                    if (act == "full" && destPath.exists()) destPath.delete()
                    else if (act == "resume") {
                        if (!destPath.exists()) offset = 0
                        else if (destPath.length() != offset) {
                            offset = 0
                            destPath.delete()
                        }
                    }
                    if (offset > 0) {
                        // 保留已有部分
                    } else if (destPath.exists()) {
                        destPath.delete()
                    }

                    out.write("OK".toByteArray()); out.flush()

                    log("[接收]   (" + (idx + 1) + "/" + fileCount + ") " + relPath +
                            " (" + SizeFormatter.humanSize(fileSize) + ")" +
                            (if (offset > 0) " [续传 " + SizeFormatter.humanSize(offset) + "]" else "") +
                            (if (compressed) " [压缩]" else ""))

                    resumeRepo.saveFolderState(peerIp, folderName, relPath, offset, fileSize, 0.0)

                    val lastProgressSave = longArrayOf(0L)
                    val lastPercentBucket = intArrayOf(-1)
                    val result = receiveData(
                        socket = socket,
                        destFile = destPath,
                        fileSize = fileSize,
                        startOffset = offset,
                        compressed = compressed,
                        progressCallback = { received ->
                            // 进度条 = **当前文件**的进度（received 含续传起点 offset）
                            // statusText 同时显示当前文件 + 全局字节进度
                            val globalBase = receivedGlobal
                            val filePercent = if (fileSize > 0)
                                (received * 100f / fileSize).coerceIn(0f, 100f) else 0f
                            val globalRecv = globalBase + received
                            val globalPercent = if (totalBytes > 0)
                                (globalRecv * 100f / totalBytes).coerceIn(0f, 100f) else 0f
                            onRecvProgress(TransferProgress(
                                percent = filePercent,
                                statusText = "当前: " + relPath + "  (" +
                                        SizeFormatter.humanSize(received) + " / " + SizeFormatter.humanSize(fileSize) + ")  ·  " +
                                        "整体: " + String.format("%.1f%%", globalPercent) + "  (" +
                                        SizeFormatter.humanSize(globalRecv) + " / " + SizeFormatter.humanSize(totalBytes) + ")"
                            ))
                            val now = System.currentTimeMillis()
                            val bucket = if (fileSize > 0) ((received * 10) / fileSize).toInt() else 0
                            if (now - lastProgressSave[0] >= 5000L || bucket != lastPercentBucket[0]) {
                                lastProgressSave[0] = now
                                lastPercentBucket[0] = bucket
                                resumeRepo.saveFolderState(peerIp, folderName, relPath, received, fileSize, 0.0)
                            }
                        }
                    )

                    val remoteHash = reader.readFixedAscii(64)
                    val matched = result.success && result.hash == remoteHash
                    out.write((if (matched) "MATCH     " else "MISMATCH  ").toByteArray())
                    out.flush()

                    if (matched) {
                        log("[接收]   ✓ " + relPath)
                        receivedGlobal += fileSize
                        fileOk = true
                        resumeRepo.deleteFolderState(peerIp, folderName, relPath)
                        break
                    } else {
                        log("[接收]   ✗ " + relPath)
                        if (destPath.exists()) destPath.delete()
                    }
                } catch (e: Exception) {
                    log("[接收]   ✗ " + entry.relPath + " 接收异常: " + e.message)
                    break
                }
            }
            if (!fileOk) {
                log("[接收]   ✗ " + entry.relPath + " 重试 " + Constants.MAX_RETRIES + " 次均失败，放弃")
                failed++
            }
        }

        val elapsed = (System.currentTimeMillis() - startTime) / 1000.0
        val speed = if (elapsed > 0) receivedGlobal / elapsed else 0.0
        if (failed == 0) {
            log("[接收] 文件夹 " + folderName + " 接收完成，全部文件校验通过 (" + SizeFormatter.formatSpeed(speed) + ")")
            // 发布到用户选择的位置
            kotlinx.coroutines.withContext(Dispatchers.IO) {
                val target = publisher(targetDir, true)
                if (target != null) {
                    log("[接收] 已保存到: " + target)
                } else {
                    log("[接收] 保存失败，文件夹仍在应用私有目录: " + targetDir.absolutePath)
                }
            }
        } else {
            log("[接收] 文件夹 " + folderName + " 接收完成，" + failed + " 个文件失败")
        }
        onRecvProgress(TransferProgress(0f, statusText = "就绪"))
    }

    // ================= 工具 =================

    private fun isInside(base: File, child: File): Boolean {
        return try {
            val basePath = base.canonicalPath
            val childPath = child.canonicalPath
            childPath == basePath || childPath.startsWith(basePath + File.separator)
        } catch (_: Exception) {
            false
        }
    }

    private fun longToBytes(value: Long): ByteArray {
        val buf = java.nio.ByteBuffer.allocate(8).order(java.nio.ByteOrder.BIG_ENDIAN)
        buf.putLong(value)
        return buf.array()
    }
}
