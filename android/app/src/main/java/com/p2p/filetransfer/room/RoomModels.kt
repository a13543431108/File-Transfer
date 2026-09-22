package com.p2p.filetransfer.room

/** 房间成员。与服务器下发的精简结构对应。 */
data class RoomMember(
    val id: String,
    val did: String,
    val name: String,
    val pub: String,       // UDP 公网映射 "ip:port"
    val pubTcp: String,    // TCP 公网映射 "ip:port"（打洞用）
    val lan: List<String>, // 内网地址
    val tcp: Int           // 对方 TCP 监听端口
)

/** 打洞协调事件：与某成员同时打洞。 */
data class PunchGo(
    val peer: RoomMember,
    val atMs: Long
)

/** 成员连接状态（供 UI 展示）。 */
enum class RoomConnState { IDLE, CONNECTING, CONNECTED, FAILED }

/** 成员 + 连接状态。 */
data class RoomMemberStatus(
    val member: RoomMember,
    val state: RoomConnState,
    val addr: String?
)
