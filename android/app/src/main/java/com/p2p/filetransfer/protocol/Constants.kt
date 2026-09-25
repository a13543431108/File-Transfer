package com.p2p.filetransfer.protocol

/** 网络协议常量（与桌面端 Python 实现严格一致） */
object Constants {

    // 端口
    const val UDP_PORT = 9998      // 设备发现（心跳/广播/回复）
    const val TCP_PORT = 9999      // 文件传输（局域网模式）
    const val SCAN_PORT = 9997     // 扫描探测
    // 房间模式（公网打洞）专用 TCP 端口。
    // 必须与 LAN 用的 9999 分离：房间模式需要三个 socket 同时占用同一端口
    // （映射观测 + 入站监听 + 打洞出站），Android 的 SO_REUSEADDR 严格，
    // 若与文件接收监听共用 9999 会 EADDRINUSE，导致 pub_tcp 无法登记、打洞必败。
    const val ROOM_TCP_PORT = 9998

    // 时间/大小
    const val NODE_TIMEOUT_MS = 600_000L          // 节点 10 分钟无响应则移除
    const val BUFFER_SIZE = 4 * 1024 * 1024       // 初始缓冲区 4MB（提升高带宽链路上的吞吐）
    const val MAX_BUFFER_SIZE = 16 * 1024 * 1024   // 最大 16MB
    /**
     * Socket 收发缓冲区（SO_SNDBUF / SO_RCVBUF）期望值。
     *
     * 重要：监听 socket 的 RCVBUF **必须在 bind() 之前设置**，
     * 因为 TCP window scale 在三次握手时按当时的缓冲区大小协商，
     * accept 之后再改对已建立的连接无效。
     */
    const val SOCKET_BUFFER_SIZE = 16 * 1024 * 1024
    const val AUTO_SCAN_INTERVAL_MS = 20_000L     // 后台扫描间隔
    const val HEARTBEAT_MAX_FAIL = 3              // 心跳检测次数（如果超过该数会去除名字）
    const val MAX_RETRIES = 3                     // 最大重试传输次数

    // 扫描
    const val SCAN_TIMEOUT_MS = 500               // 单次探测超时
    const val MAX_SCAN_IPS = 65536                // 单次扫描最大 IP 数
    const val SCAN_CONCURRENCY = 200              // Android 上并发扫描数（低于桌面 1000）

    // 低网络 / 静默超时
    const val LOW_NETWORK_THRESHOLD = 512 * 1024.0
    const val RECV_ACTIVITY_TIMEOUT_MS = 60_000L  // 接收静默超时
    const val ACTIVITY_TIMEOUT_MS = 30_000L       // 发送静默超时
    const val WRITE_BATCH_SIZE = 1 * 1024 * 1024  // 磁盘写入批量

    // 动态缓冲区下限 / 上限
    const val MIN_BUFFER_SIZE = 64 * 1024         // 最小 64KB
    const val MIN_ADAPTIVE_CHUNK = 256 * 1024     // 发送端最小块 256KB
    const val MAX_ADAPTIVE_CHUNK = 8 * 1024 * 1024  // 发送端最大块 8MB

    // 广播
    const val BROADCAST_BURST_INTERVAL_MS = 200L
    const val BROADCAST_BURST_DURATION_MS = 5000L
    const val BROADCAST_INTERVAL_MS = 1000L

    // 消息类型（TCP 首个字节）
    const val FLAG_FILE = 0x00
    const val FLAG_FOLDER = 0x01
    const val FLAG_QUERY_OFFSET = 0x02

    // flags 位
    const val FLAG_COMPRESS = 0x01   // bit0
    const val FLAG_RESUME = 0x02     // bit1

    // 文本文件压缩白名单
    val TEXT_EXTENSIONS = setOf(
        ".txt", ".log", ".json", ".xml", ".py", ".js", ".html", ".htm",
        ".css", ".csv", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf",
        ".md", ".rst", ".sh", ".bat", ".ps1", ".vbs"
    )

    /** 判断文件名是否为可压缩文本文件 */
    fun isTextFile(name: String): Boolean {
        val idx = name.lastIndexOf('.')
        if (idx < 0) return false
        return TEXT_EXTENSIONS.contains(name.substring(idx).lowercase())
    }

    /**
     * 接收端：根据速度（字节/秒）计算接收缓冲区大小。
     *
     * 仅供 [TcpFileTransfer.receiveData] 的"只扩不缩"逻辑使用：
     * 起始 1MB，仅当实测速度更高时才扩大到 2MB / 4MB，
     * 绝不因为速度慢而缩小（避免"慢 → 缓冲变小 → 系统调用更频繁 → 更慢"的负反馈）。
     */
    fun recvBufferSizeFor(speed: Double): Int = when {
        speed < 5.0 * 1024 * 1024 -> 1024 * 1024          // 起步 1MB
        speed < 20.0 * 1024 * 1024 -> 2 * 1024 * 1024
        else -> 4 * 1024 * 1024
    }.coerceIn(MIN_BUFFER_SIZE, MAX_BUFFER_SIZE)

    /**
     * 发送端：根据速度（字节/秒）计算自适应发送块大小。
     *
     * 注意：不做"变小"调整。低速度下也至少维持 256KB~1MB 的块，
     * 避免慢 → 缓冲变小 → 更慢的负反馈循环。发送块只往上调，不往下调。
     */
    fun adaptiveChunkFor(speed: Double): Int = when {
        // 极低网络也保持 256KB（原 16KB 太小，导致频繁系统调用）
        speed < 512 * 1024 -> 256 * 1024
        speed < 2 * 1024 * 1024 -> 512 * 1024
        speed < 10 * 1024 * 1024 -> 1024 * 1024
        speed < 40 * 1024 * 1024 -> 2 * 1024 * 1024
        else -> 4 * 1024 * 1024
    }.coerceIn(MIN_ADAPTIVE_CHUNK, MAX_ADAPTIVE_CHUNK)
}
