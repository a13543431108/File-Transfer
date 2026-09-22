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

    // 消息类型：服务器 -> 客户端
    const val T_JOINED = "joined"
    const val T_MEMBER_JOIN = "member_join"
    const val T_MEMBER_LEAVE = "member_leave"
    const val T_PUNCH_GO = "punch_go"
    const val T_ERROR = "error"

    // ==================== 信令连接 ====================
    const val DEFAULT_SERVER_PORT = 3336      // 信令服务器默认 UDP 端口
    const val DEFAULT_SERVER_TCP_PORT = 3337  // 服务器 TCP 映射观测端口
    const val HEARTBEAT_INTERVAL_MS = 20_000L // 心跳间隔（需 < 服务器 30s 超时）
    const val RECV_SO_TIMEOUT_MS = 1000       // 信令 socket 接收超时

    // ==================== 打洞 ====================
    const val PUNCH_CONCURRENCY = 6           // 同时打洞最大任务数
    const val PUNCH_CONNECT_TIMEOUT_MS = 300  // 单次 TCP connect 超时
    const val PUNCH_RETRY = 3                 // 打洞失败重试次数
    const val PUNCH_RETRY_BACKOFF_S = 2L      // 重试退避基数（秒）

    // ==================== 连接保活 ====================
    const val NAT_KEEPALIVE_INTERVAL_MS = 20_000L  // 维持 NAT 映射间隔
}
