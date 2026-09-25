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
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import java.io.File
import java.io.IOException
import java.io.InputStream
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
@Suppress("unused")
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
    private var roomServerSocket: ServerSocket? = null   // 房间模式专用端口
    private var serverJob: Job? = null
    private var running = false

    /**
     * 入站连接钩子：房间模式下，accept 到的连接先交给它判断是否为打洞入站连接。
     * 返回 true 表示已被房间管理接管（不再走普通文件接收）。
     */
    var onInboundConnection: ((Socket, String, Int) -> Boolean)? = null

    // ================= TCP 服务器 =================

    fun startServer(parentScope: CoroutineScope) {
        if (running) return
        running = true
        // 局域网文件端口（9999）
        serverJob = parentScope.launch(Dispatchers.IO) {
            bindAndAccept(this, Constants.TCP_PORT, isRoomPort = false)
        }
        // 房间模式端口（9998）不监听。
        //
        // 实测证实（2026-09-24）：Android 上 ServerSocket 无法与出站 socket
        // 用 SO_REUSEPORT 共存——一旦监听 9998，映射观测 socket 再 bind 9998
        // 会 EADDRINUSE，退化成随机端口，导致服务器登记错误的 pub_tcp，
        // 打洞靶子全错，比不监听更糟。故保持"只出站"策略，依赖 TCP 同时
        // 打开（simultaneous open）。成功率是概率性的，但配合握手确认可
        // 保证正确性（不成则回退 UDP）。
    }

    /**
     * 通用 accept 循环。
     * @param port 监听端口
     * @param isRoomPort 是否为房间模式专用端口：
     *   true  = 只接受打洞入站连接，其它一律关闭
     *   false = 普通文件接收；若钩子识别为打洞连接则交由房间管理接管
     */
    private fun bindAndAccept(scope: CoroutineScope, port: Int, isRoomPort: Boolean) {
        val ss: ServerSocket
        try {
            ss = ServerSocket()
            // 关键：SO_RCVBUF 必须在 bind 之前设置。
            // TCP window scale 在三次握手时按当时的缓冲区大小协商，
            // accept 之后再设置对已建立连接无效。
            try {
                ss.receiveBufferSize = Constants.SOCKET_BUFFER_SIZE
                if (!isRoomPort) {
                    log("[TCP] 监听 socket RCVBUF 设为 " +
                            com.p2p.filetransfer.util.SizeFormatter.humanSize(
                                ss.receiveBufferSize.toLong()) +
                            "（期望 " + com.p2p.filetransfer.util.SizeFormatter.humanSize(
                                Constants.SOCKET_BUFFER_SIZE.toLong()) + "）")
                }
            } catch (e: Exception) {
                if (!isRoomPort) log("[TCP] 设置监听 socket RCVBUF 失败: " + e.message)
            }
            ss.reuseAddress = true
            // 注：房间模式 9998 不监听（见 startServer 注释）。
            // 若未来恢复房间监听，此处需对 ss 调 ReusePort.enableServerSocket(ss, log)。
            ss.bind(InetSocketAddress(port), 50)
            if (isRoomPort) roomServerSocket = ss else serverSocket = ss
            log(if (isRoomPort) "[房间] 房间端口 $port 监听成功" else "[TCP] 监听端口 $port 成功")
        } catch (e: Exception) {
            log(if (isRoomPort) "[房间] 端口 $port 绑定失败: " + e.message
                else "[TCP] 端口绑定失败: " + e.message)
            return
        }
        while (running) {
            try {
                val conn = ss.accept()
                SocketOptimizer.optimize(conn)
                val hook = onInboundConnection
                var taken = false
                if (hook != null) {
                    val ip = conn.inetAddress?.hostAddress?.substringBefore('%') ?: ""
                    taken = hook.invoke(conn, ip, conn.port)
                }
                if (taken) continue
                if (isRoomPort) {
                    // 房间端口不接受普通文件传输
                    try { conn.close() } catch (_: Exception) {}
                    continue
                }
                conn.soTimeout = 15000
                scope.launch(Dispatchers.IO) { handleReceive(conn) }
            } catch (e: Exception) {
                if (running) Log.d("TcpFileTransfer", "accept error($port): " + e.message)
            }
        }
        try { ss.close() } catch (_: Exception) {}
    }

    fun stopServer() {
        running = false
        try { serverSocket?.close() } catch (_: Exception) {}
        try { roomServerSocket?.close() } catch (_: Exception) {}
        serverJob?.cancel()
    }

    // ================= 接收 =================

    private suspend fun handleReceive(rawSocket: Socket) {
        val socket = TcpStreamSocket(rawSocket)
        try {
            val reader = ProtoReader(socket.inputStream)
            socket.soTimeout = 0
            val peerIp = rawSocket.inetAddress?.hostAddress?.substringBefore('%') ?: "unknown"
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
                    Constants.FLAG_QUERY_OFFSET -> handleQueryOffset(socket, reader, peerIp)
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
    private fun handleQueryOffset(socket: StreamSocket, reader: ProtoReader, peerIp: String) {
        try {
            val totalSize = reader.readInt64()
            val filename = reader.readLengthPrefixedString()
            val localOffset = lookupLocalOffset(filename, totalSize, peerIp)
            val out = socket.outputStream
            out.write(longToBytes(localOffset))
            out.flush()
            log("[接收] 续传查询: " + filename + "，本地已收 " + SizeFormatter.humanSize(localOffset))
        } catch (e: Exception) {
            log("[接收] 续传查询异常: " + e.message)
            try {
                socket.outputStream.write(longToBytes(0))
                socket.outputStream.flush()
            } catch (_: Exception) {}
        }
    }

    private fun lookupLocalOffset(filename: String, totalSize: Long, peerIp: String): Long {
        // 关键：以【续传记录】为唯一判据。
        // 仅当存在匹配的 recv 续传记录（total_size 一致，且记录里实际文件大小 == offset）
        // 时，才回报已收字节数；否则一律回报 0（视为新文件）。
        // 这样避免"同名但不同内容"的旧文件被误判为续传起点。
        val rec = resumeRepo.loadRecvByKey(peerIp, filename) ?: return 0
        if (rec.totalSize != totalSize) return 0
        val actual = File(rec.filepath)
        if (!actual.exists() || actual.length() != rec.offset) return 0
        return rec.offset
    }

    private suspend fun receiveSingleFile(socket: StreamSocket, reader: ProtoReader, peerIp: String) {
        // 关键：进入文件接收后重置超时。
        // handleReceiveOnSocket 每轮会设 soTimeout=500（用于检测无数据），
        // 若传播到 receiveData，500ms 无数据即抛 SocketTimeoutException，
        // 导致大文件/慢网络下误判中断。
        socket.soTimeout = 0
        val filename = reader.readLengthPrefixedString()
        val flags = reader.readByte()
        val compressed = (flags and Constants.FLAG_COMPRESS) != 0
        val resume = (flags and Constants.FLAG_RESUME) != 0
        val fileSize = reader.readInt64()
        var offset = if (resume) reader.readInt64() else 0

        val saveDir = saveDirProvider()
        saveDir.mkdirs()
        var savePath = File(saveDir, filename)
        if (!isInside(saveDir, savePath)) {
            log("[安全] 拒绝非法文件名: " + filename)
            socket.outputStream.write("NO".toByteArray())
            socket.outputStream.flush()
            return
        }
        // 续传记录键 = 发送方原始文件名（改名后仍用它查记录）
        val resumeKey = filename

        val out = socket.outputStream

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
            // 非续传：同名文件已存在 → 改名 name(1).ext，不覆盖、不删除
            if (savePath.exists()) {
                savePath = uniquePath(saveDir, filename)
                log("[接收] 同名文件已存在，改名为: " + savePath.name)
            }
            offset = 0
        }
        out.write("OK".toByteArray()); out.flush()

        onReceiveRequest(ReceiveRequest(peerIp, filename, fileSize))
        log("[接收] 文件: " + filename + " (" + SizeFormatter.humanSize(fileSize) + ")" +
                (if (resume) " [续传]" else "") + (if (compressed) " [压缩]" else "") +
                " 来自 " + peerIp)

        resumeRepo.saveState(peerIp, savePath.absolutePath, offset, fileSize, 0.0, "recv", resumeKey)

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
                    resumeRepo.saveState(peerIp, savePath.absolutePath, received, fileSize, 0.0, "recv", resumeKey)
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
            resumeRepo.deleteState(peerIp, savePath.absolutePath, "recv", resumeKey)
            // 发布到用户选择的位置（默认: 下载/P2PFileTransfer/）
            kotlinx.coroutines.withContext(Dispatchers.IO) {
                val target = publisher(savePath, false)
                if (target != null) {
                    log("[接收] 已保存到: " + target)
                    // 关键：发布成功后删除【私有暂存副本】。
                    // 私有目录(getExternalFilesDir/Received)只是中转站，
                    // 若不清理会不断累积；下次接收同名文件时，第 286 行的
                    // savePath.exists() 会命中这些残留 → 误判"重名" → 无谓
                    // 改名 name(1).ext（而真正的保存目录并无重名）。
                    try { savePath.delete() } catch (_: Exception) {}
                } else {
                    log("[接收] 保存失败，文件仍在应用私有目录: " + savePath.absolutePath)
                }
            }
        } else {
            log("[接收] 文件 " + filename + " 校验失败")
            // 关键：删除损坏的接收文件与续传状态，否则对方重传时续传协商会再次
            // 读到该文件、仅凭大小回报"已收 N 字节"，导致永远续传到错误位置、反复失败。
            try { if (savePath.exists()) savePath.delete() } catch (_: Exception) {}
            resumeRepo.deleteState(peerIp, savePath.absolutePath, "recv", resumeKey)
        }
        onRecvProgress(TransferProgress(0f, statusText = "就绪"))
    }

    /** 同名文件已存在时，生成 name(1).ext / name(2).ext ... 的可用文件。 */
    private fun uniquePath(dir: File, filename: String): File {
        val dot = filename.lastIndexOf('.')
        val stem = if (dot > 0) filename.substring(0, dot) else filename
        val ext = if (dot > 0) filename.substring(dot) else ""
        for (i in 1..9999) {
            val cand = File(dir, stem + "(" + i + ")" + ext)
            if (!cand.exists()) return cand
        }
        return File(dir, stem + "_" + System.currentTimeMillis() + ext)
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
        socket: StreamSocket,
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

        val input = socket.inputStream
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

    // ================= 房间模式：复用长连接收发 =================

    /**
     * 在【已有的打洞长连接 socket】上串行发送多个项目（房间模式）。
     *
     * 与 [sendFiles] 的区别：
     *   - 不建立/关闭连接（复用打洞得到的长连接）
     *   - targetKey 为稳定标识（设备 did，用于跨会话续传状态键）
     *   - ioLock：发送期间持锁，避免与接收循环争抢同一 socket 的读
     * 返回成功发送的项目数；连接损坏时提前停止。
     */
    suspend fun sendItemsOnSocket(
        socket: StreamSocket,
        items: List<TransferItem>,
        targetKey: String,
        ioLock: Mutex,
        onItemDone: (String, Boolean) -> Unit,
        roomMgr: com.p2p.filetransfer.room.RoomManager? = null,
        peerId: String? = null
    ): Int = withContext(Dispatchers.IO) {
        val MAX_SWITCH = 3
        var ok = 0
        var curSock = socket
        var curLock = ioLock
        val used = HashSet<Int>()
        for (item in items) {
            var sentOk = false
            for (switch in 0..MAX_SWITCH) {
                try {
                    curLock.withLock {
                        if (item.isFolder) {
                            sendFolder(curSock, File(item.path), targetKey)
                        } else {
                            sendSingleFileOnSocket(curSock, File(item.path), targetKey)
                        }
                    }
                    sentOk = true
                    break
                } catch (e: Exception) {
                    used.add(System.identityHashCode(curSock))
                    log("[房间发送] 通道失败: " + e.message)
                    if (roomMgr == null || peerId == null || switch >= MAX_SWITCH) break
                    val alt = roomMgr.getSendChannel(peerId, used)
                    if (alt == null) {
                        log("[房间发送] 无其他可用通道，放弃 " + item.path)
                        break
                    }
                    curSock = alt.stream
                    curLock = alt.ioLock
                    log("[房间发送] 换路续传 " + item.path +
                            " -> " + (if (alt.isUdp) "UDP-RTP" else "TCP") + "（角色=" + alt.role + "）")
                }
            }
            if (sentOk) {
                ok++
                onItemDone(item.path, true)
            } else {
                onItemDone(item.path, false)
                break
            }
        }
        ok
    }

    /** 房间模式：在已有 socket 上发送单文件（跳过临时连接的续传协商，用本地状态）。 */
    private suspend fun sendSingleFileOnSocket(socket: StreamSocket, file: File, targetKey: String) {
        val totalSize = file.length()
        val filename = file.name
        val compressRequested = Constants.isTextFile(filename)

        val senderState = resumeRepo.loadState(targetKey, file.absolutePath, "sender")
        val recvState = resumeRepo.loadState(targetKey, file.absolutePath, "recv")
        val senderOffset = if (senderState != null && senderState.totalSize == totalSize &&
                senderState.offset > 0 && senderState.offset < totalSize) senderState.offset else 0L
        val recvOffset = if (recvState != null && recvState.totalSize == totalSize) recvState.offset else 0L
        var offset = if (senderOffset > 0 && recvOffset > 0) minOf(senderOffset, recvOffset)
                     else maxOf(senderOffset, recvOffset)
        if (offset >= totalSize) offset = 0

        var compressedBytes: ByteArray? = null
        if (compressRequested) {
            if (offset > 0) { log("[房间发送] 压缩文件不支持续传，从头发送"); offset = 0 }
            compressedBytes = compressFile(file)
        }
        resumeRepo.saveState(targetKey, file.absolutePath, offset, totalSize,
            file.lastModified() / 1000.0, "sender")
        sendSingleFileWithSocket(socket, file, targetKey, filename, totalSize, offset, compressedBytes)
    }

    /**
     * 房间模式：在长连接上循环接收多条消息（与电脑端 handle_room_receive 对应）。
     * 用 ioLock 串行化：仅在短超时内可读时持锁读一帧，否则释放锁让发送方使用。
     */
    suspend fun handleReceiveOnSocket(
        socket: StreamSocket,
        ioLock: Mutex,
        peerKey: String,
        epoch: Int = -1,
        roomMgr: com.p2p.filetransfer.room.RoomManager? = null
    ): Unit = withContext(Dispatchers.IO) {
        val reader = ProtoReader(socket.inputStream)
        // epoch 键按协议区分（与启动侧一致）
        val isUdp = (socket as? com.p2p.filetransfer.room.UdpReliableSocket) != null
        val epochKey = peerKey + (if (isUdp) ":udp" else ":tcp")
        try {
            while (true) {
                // 代际检查：若【同协议】通道已被替换，旧循环退出（避免双重写盘）
                if (roomMgr != null && epoch >= 0 && !roomMgr.isCurrentEpoch(epochKey, epoch)) {
                    log("[房间接收] " + epochKey + " 已被新通道接管，旧接收循环退出")
                    break
                }
                var stop = false
                ioLock.withLock {
                    socket.soTimeout = 500
                    val flag = try {
                        reader.readByte()
                    } catch (e: java.net.SocketTimeoutException) {
                        -1
                    } catch (e: Exception) {
                        stop = true
                        -1
                    }
                    if (flag < 0) return@withLock
                    socket.soTimeout = 0
                    // 写盘前再确认代际
                    if (roomMgr != null && epoch >= 0 && !roomMgr.isCurrentEpoch(epochKey, epoch)) {
                        stop = true
                        return@withLock
                    }
                    when (flag) {
                        Constants.FLAG_FILE -> receiveSingleFile(socket, reader, peerKey)
                        Constants.FLAG_FOLDER -> receiveFolder(socket, reader, peerKey)
                        Constants.FLAG_QUERY_OFFSET -> handleQueryOffset(socket, reader, peerKey)
                        else -> stop = true
                    }
                }
                if (stop) break
            }
        } catch (_: Exception) {
        } finally {
            try { socket.close() } catch (_: Exception) {}
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
                    sendFolder(TcpStreamSocket(s), File(item.path), targetIp)
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

    /**
     * 创建并连接一个出站 TCP socket —— 本类中【唯一】主动 connect 的入口。
     *
     * 统一在此完成：connect 前设收发缓冲区（TCP 窗口缩放在握手时协商，必须
     * 在此之前设置，否则接收窗口退回内核默认值，吃不满带宽）→ connect →
     * 连接后常规调优（Nagle/KeepAlive）。
     *
     * 所有出站连接（发送、续传协商）都必须经由此函数。
     * 严禁在别处直接 new Socket().connect()，否则会重新引入性能问题。
     */
    private fun createOutboundSocket(
        targetIp: String,
        connectTimeoutMs: Int,
        readTimeoutAfter: Int
    ): Socket =
        // 统一走 SocketOptimizer 的唯一出站连接工厂（connect 前设缓冲区）
        SocketOptimizer.connectOutbound(targetIp, Constants.TCP_PORT, connectTimeoutMs, readTimeoutAfter)

    /** 打开到目标 IP 的 TCP 连接（发送用，连接后读超时=0 即无限等待）。 */
    private fun openSocket(targetIp: String): Socket =
        createOutboundSocket(targetIp, 30000, 0)

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
            sendSingleFileWithSocket(TcpStreamSocket(socket), file, targetIp, filename, totalSize, offset, compressedBytes)
        } finally {
            try { socket.close() } catch (_: Exception) {}
        }
    }

    private suspend fun sendSingleFileWithSocket(
        socket: StreamSocket,
        file: File,
        targetIp: String,
        filename: String,
        totalSize: Long,
        offset: Long,
        compressedBytes: ByteArray?
    ) {
        // 重置读超时：接收循环可能把它设为 500ms（用于检测无数据），
        // 若发送方读取 ACK 时继承该超时，会立刻 SocketTimeoutException。
        socket.soTimeout = 0
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

        val out = socket.outputStream
        val writer = ProtoWriter(out)
        writer.writeByte(Constants.FLAG_FILE)
        writer.writeLengthPrefixedString(filename)
        writer.writeByte(flags)
        writer.writeInt64(sendSize)
        if ((flags and Constants.FLAG_RESUME) != 0) writer.writeInt64(useOffset)
        writer.flush()

        val ack = ProtoReader(socket.inputStream).readFixedAscii(2)
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

        val result = ProtoReader(socket.inputStream).readFixedAscii(10)
        val elapsed = (System.currentTimeMillis() - startTime) / 1000.0
        val speed = if (elapsed > 0) sendSize / elapsed else 0.0
        if (result == "MATCH") {
            log("[发送] 文件 " + filename + " 对方校验通过 (" + SizeFormatter.formatSpeed(speed) + ")")
            resumeRepo.deleteState(targetIp, file.absolutePath, "sender")
            resumeRepo.deleteState(targetIp, file.absolutePath, "recv")
        } else {
            log("[发送] 文件 " + filename + " 对方校验失败")
            // 关键：清除双方续传状态，并抛异常触发上层从头重传。
            // 否则残留的续传记录会让下次仍从错误偏移续传，反复失败。
            resumeRepo.deleteState(targetIp, file.absolutePath, "sender")
            resumeRepo.deleteState(targetIp, file.absolutePath, "recv")
            throw java.io.IOException("对方校验失败: " + filename)
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
            // 经统一入口创建（connect 前设缓冲区 + connect 后调优）
            val s = createOutboundSocket(targetIp, 5000, 5000)
            temp = s
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
        socket: StreamSocket,
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
        val out = socket.outputStream
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

    private suspend fun sendFolder(socket: StreamSocket, folder: File, targetIp: String) {
        // 重置读超时（同 sendSingleFileWithSocket，避免继承接收循环的 500ms）
        socket.soTimeout = 0
        val folderName = folder.name
        val manifest = getFolderManifest(folder)
        val fileCount = manifest.size
        val totalBytes = manifest.sumOf { it.size }
        log("[发送] 文件夹: " + folderName + " (" + fileCount + " 个文件, " + SizeFormatter.humanSize(totalBytes) + ")")

        val out = socket.outputStream
        val writer = ProtoWriter(out)
        writer.writeByte(Constants.FLAG_FOLDER)
        writer.writeLengthPrefixedString(folderName)
        writer.writeInt32(fileCount)
        writer.writeInt64(totalBytes)
        writer.flush()

        val reader = ProtoReader(socket.inputStream)
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

                    val a = ProtoReader(socket.inputStream).readFixedAscii(2)
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

                    val res = ProtoReader(socket.inputStream).readFixedAscii(10)
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
                    log("[发送]   ✗ " + entry.relPath + " 异常: " + e.message)
                }
            }
            if (!fileOk) failed++
        }

        if (failed == 0) {
            log("[发送] 文件夹 " + folderName + " 发送完成 ✓ (共 " + fileCount + " 个文件)")
        } else {
            log("[发送] 文件夹 " + folderName + " 发送完成，$failed 个文件失败")
        }
        onSendProgress(TransferProgress(0f, statusText = "就绪"))
    }

    // ================= 文件夹接收 =================

    private suspend fun receiveFolder(socket: StreamSocket, reader: ProtoReader, peerIp: String) {
        // 同 receiveSingleFile：重置超时，避免 500ms 检测超时传播到数据接收
        socket.soTimeout = 0
        val folderName = reader.readLengthPrefixedString()
        val fileCount = reader.readInt32()
        val totalBytes = reader.readInt64()

        val rootDir = saveDirProvider()
        val saveDir = File(rootDir, folderName)
        if (!isInside(rootDir, saveDir)) {
            log("[安全] 拒绝非法文件夹名: " + folderName)
            socket.outputStream.write("NO".toByteArray())
            socket.outputStream.flush()
            return
        }

        val out = socket.outputStream

        // 请求用户接受
        val accept = onAcceptIncoming(ReceiveRequest(peerIp, folderName, totalBytes, isFolder = true))
        if (!accept) {
            out.write("NO".toByteArray()); out.flush()
            log("[接收] 用户拒绝接收文件夹 " + folderName + " (来自 " + peerIp + ")")
            return
        }

        out.write("OK".toByteArray()); out.flush()
        saveDir.mkdirs()
        log("[接收] 文件夹: " + folderName + " (" + fileCount + " 个文件, " +
                SizeFormatter.humanSize(totalBytes) + ") 来自 " + peerIp)

        // 接收清单
        val manifest = mutableListOf<Triple<String, Long, Double>>()
        for (i in 0 until fileCount) {
            val relPath = reader.readLengthPrefixedString()
            val size = reader.readInt64()
            val mtime = reader.readDouble()
            manifest.add(Triple(relPath, size, mtime))
        }

        // 决定每个文件的处理方式
        val actions = mutableListOf<Pair<String, Long>>()
        for ((relPath, size, mtime) in manifest) {
            val dest = File(saveDir, relPath)
            if (!isInside(saveDir, dest)) {
                log("[安全] 拒绝非法路径: " + relPath)
                out.write(byteArrayOf(0x00)); out.flush()
                return
            }
            if (dest.exists()) {
                val localSize = dest.length()
                val localMtime = dest.lastModified() / 1000.0
                if (localSize == size && Math.abs(localMtime - mtime) < 0.1) {
                    actions.add(Pair("skip", 0L))
                } else if (localSize < size) {
                    actions.add(Pair("resume", localSize))
                } else {
                    actions.add(Pair("full", 0L))
                }
            } else {
                actions.add(Pair("full", 0L))
            }
        }

        // 回复清单处理成功
        out.write(byteArrayOf(0x01)); out.flush()

        // 发送每个文件的处理指令
        for ((act, off) in actions) {
            when (act) {
                "skip" -> out.write(byteArrayOf(0x00))
                "full" -> out.write(byteArrayOf(0x01))
                "resume" -> {
                    out.write(byteArrayOf(0x02))
                    out.write(longToBytes(off))
                }
            }
        }
        out.flush()

        // 逐个接收文件（最多重试 Constants.MAX_RETRIES 次）
        var receivedGlobal = 0L
        var failed = 0

        for (idx in 0 until fileCount) {
            val (relPath, size, _) = manifest[idx]
            val (act, _) = actions[idx]
            if (act == "skip") {
                log("[接收]   ✓ " + relPath + " (已存在，跳过)")
                continue
            }

            var fileOk = false
            for (attempt in 0 until Constants.MAX_RETRIES) {
                try {
                    // 接收文件头
                    val rp = reader.readLengthPrefixedString()
                    val flags = reader.readByte()
                    val compressed = (flags and Constants.FLAG_COMPRESS) != 0
                    val resume = (flags and Constants.FLAG_RESUME) != 0
                    val fileSize = reader.readInt64()
                    var curOffset = if (resume) reader.readInt64() else 0L

                    val dest = File(saveDir, rp)
                    if (!isInside(saveDir, dest)) {
                        log("[安全] 拒绝非法路径: " + rp)
                        out.write("NO".toByteArray()); out.flush()
                        return
                    }
                    dest.parentFile?.mkdirs()

                    if (act == "full" && dest.exists()) {
                        dest.delete()
                        curOffset = 0L
                    } else if (act == "resume") {
                        if (!dest.exists()) {
                            curOffset = 0L
                        } else if (dest.length() != curOffset) {
                            log("[接收] 续传偏移不匹配，从头发送 " + rp)
                            curOffset = 0L
                            dest.delete()
                        }
                    }

                    out.write("OK".toByteArray()); out.flush()
                    log("[接收]   (" + (idx + 1) + "/" + fileCount + ") " + rp +
                            " (" + SizeFormatter.humanSize(fileSize) + ")" +
                            (if (resume) " [续传]" else "") +
                            (if (compressed) " [压缩]" else ""))

                    resumeRepo.saveFolderState(peerIp, folderName, rp, curOffset, fileSize, 0.0)

                    val result = receiveData(
                        socket = socket,
                        destFile = dest,
                        fileSize = fileSize,
                        startOffset = curOffset,
                        compressed = compressed,
                        progressCallback = { received ->
                            val percent = if (fileSize > 0) (received * 100.0 / fileSize).toFloat() else 0f
                            onRecvProgress(TransferProgress(
                                percent = percent,
                                statusText = "接收 " + rp + ": " + SizeFormatter.humanSize(received) +
                                        " / " + SizeFormatter.humanSize(fileSize)
                            ))
                        }
                    )

                    val remoteHash = reader.readFixedAscii(64)
                    if (result.success && result.hash == remoteHash) {
                        out.write("MATCH     ".toByteArray()); out.flush()
                        fileOk = true
                        receivedGlobal += size
                        resumeRepo.deleteFolderState(peerIp, folderName, rp)
                        break
                    } else {
                        out.write("MISMATCH  ".toByteArray()); out.flush()
                        try { if (dest.exists()) dest.delete() } catch (_: Exception) {}
                    }
                } catch (e: Exception) {
                    log("[接收]   ✗ " + relPath + " 异常: " + e.message)
                    try { out.write("MISMATCH  ".toByteArray()); out.flush() } catch (_: Exception) {}
                }
            }
            if (!fileOk) failed++
        }

        if (failed == 0) {
            log("[接收] 文件夹 " + folderName + " 接收完成 ✓")
            kotlinx.coroutines.withContext(Dispatchers.IO) {
                val target = publisher(saveDir, true)
                if (target != null) log("[接收] 已保存到: " + target)
            }
        } else {
            log("[接收] 文件夹 " + folderName + " 接收完成，$failed 个文件失败")
        }
        onRecvProgress(TransferProgress(0f, statusText = "就绪"))
    }

    // ================= 工具 =================

    private fun longToBytes(v: Long): ByteArray =
        java.nio.ByteBuffer.allocate(8).order(java.nio.ByteOrder.BIG_ENDIAN).putLong(v).array()

    /** 检查 file 是否位于 dir 内（防路径穿越）。 */
    private fun isInside(dir: File, file: File): Boolean {
        return try {
            val dirPath = dir.canonicalPath
            val filePath = file.canonicalPath
            filePath == dirPath || filePath.startsWith(dirPath + File.separator)
        } catch (_: Exception) {
            false
        }
    }
}
