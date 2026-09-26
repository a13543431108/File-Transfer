package com.p2p.filetransfer.room

/**
 * 房间模式（公网信令 + TCP 打洞）配置 —— 单一来源。
 *
 * 所有房间/信令/打洞相关可调参数集中在此，改一处全局生效。
 * 与电脑端 net/config.py 严格对应。
 */
object RoomConfig {

    // ==================== 信令协议 ====================
    const val VER = 1

    // 消息类型：客户端 -> 服务器
    const val T_JOIN = "join"
    const val T_HB = "hb"
    const val T_PUNCH_REQ = "punch_req"
    const val T_BYE = "bye"
    const val T_NAT_PROBE = "nat_probe"        // 客户端 -> 服务器：请求回复观察到的公网地址
    const val T_TIME_REQ = "time_req"          // 客户端 -> 服务器：NTP 式时间同步请求
    const val T_TIME_REPLY = "time_reply"      // 服务器 -> 客户端：回显 t1 + 服务器时刻 t2
    const val T_UDP_HELLO = "udp_hello"        // 客户端 -> 服务器：登记 UDP 打洞 socket 公网映射

    // 消息类型：服务器 -> 客户端
    const val T_JOINED = "joined"
    const val T_NAT_PROBE_REPLY = "nat_probe_reply"  // 服务器 -> 客户端：观察到的公网地址
    const val T_MEMBER_JOIN = "member_join"
    const val T_MEMBER_LEAVE = "member_leave"
    const val T_MEMBER_UPDATE = "member_update"   // 成员信息更新（如映射登记完成）
    const val T_PUNCH_GO = "punch_go"
    const val T_ERROR = "error"

    // ==================== 信令连接 ====================
    const val DEFAULT_SERVER_PORT = 3336      // 信令服务器默认 UDP 端口
    const val DEFAULT_SERVER_TCP_PORT = 3337  // 服务器 TCP 映射观测端口
    const val NAT_PROBE_PORT = 3338           // 服务器 NAT 探测端口（对称 NAT 检测用）
    const val HEARTBEAT_INTERVAL_MS = 20_000L // 心跳间隔（需 < 服务器 30s 超时）
    const val RECV_SO_TIMEOUT_MS = 1000       // 信令 socket 接收超时

    // ==================== 打洞 ====================
    const val PUNCH_CONCURRENCY = 6           // 同时打洞最大任务数
    // 方向 A：单次打洞中【并发 connect 的候选数】上限。
    // 9998 端口上并存的打洞 socket 越多，SO_REUSEPORT 入站匹配越易分错，
    // TCP 打洞成功率越低。限制为 2，降低端口竞争。
    const val PUNCH_CANDIDATE_CONCURRENCY = 2
    const val PUNCH_CONNECT_TIMEOUT_MS = 2500  // 单次 TCP connect 超时
                                               // （风暴模式缩短，让重连更密）
                                               // TCP 同时打开需双方 SYN 在 NAT 间
                                               // 往返匹配；6 秒覆盖 3 次 SYN 重传
    const val PUNCH_RETRY = 3                 // 打洞失败重试次数
    const val PUNCH_RETRY_BACKOFF_S = 1L      // 重试退避基数（秒）
    // 每轮"重连风暴"持续时长（毫秒）：窗口内不停做并行 connect 轮次，
    // 直到成功（含握手确认）或超时。提高两端 SYN 交叉命中概率。
    const val PUNCH_STORM_DURATION_MS = 6000L
    // TCP 打洞握手确认超时（毫秒）
    const val PUNCH_HANDSHAKE_TIMEOUT_MS = 2000
    // TCP_READY 收到后，等待 SYNC 协调的窗口（毫秒）。
    // 有 UDP-RTP 时优先走 SYNC 精确对齐（同 tGo 同时打洞）；
    // 超过此窗口 SYNC 仍未完成 → 回退到"直接打洞"。
    // SYNC 理论耗时：4 次采样(~100ms) + COMMIT 延迟 500ms ≈ 700ms，取 1.5s 留余量。
    const val TCP_READY_SYNC_WAIT_MS = 1500L
    // SYNC 超时回退打洞时，收到 TCP_READY 后再等多久才发 SYN（毫秒）。
    // 给对端足够时间也收到本端 TCP_READY 并进入等待，使两端发出时刻
    // 误差 ≈ RTT/2，优于"立即打"的随机错开。
    const val TCP_READY_FALLBACK_DELAY_MS = 300L
    // 握手魔数（两端一致）：区分"真通"与"半开连接"
    val PUNCH_HANDSHAKE_MAGIC = byteArrayOf(0x50, 0x32, 0x50, 0x48)  // "P2PH"
                                              // 每轮打洞 6 秒，间隔 1/2/3 秒

    // ==================== UDP 打洞 ====================
    /**
     * UDP 打洞专用本地端口。
     * 与局域网发现端口 UDP 9998 冲突，故独立为 9996。
     * 两端从该端口同时向对方公网 UDP 映射发包，打通后用于文件传输。
     */
    const val UDP_HOLE_PORT = 9996
    const val UDP_PROBE_INTERVAL_MS = 100L    // 探测发送间隔
    const val UDP_PROBE_DURATION_MS = 5000L   // 探测持续时间

    // ==================== 连接保活 ====================
    const val NAT_KEEPALIVE_INTERVAL_MS = 15_000L  // 维持 NAT 映射间隔
}
