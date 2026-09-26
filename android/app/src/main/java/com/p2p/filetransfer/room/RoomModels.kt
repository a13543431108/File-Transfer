package com.p2p.filetransfer.room

/** 房间成员。与服务器下发的精简结构对应。 */
data class RoomMember(
    val id: String,
    val did: String,
    val name: String,
    val pub: String,       // 信令 UDP 公网映射 "ip:port"
    val pubTcp: String,    // TCP 公网映射 "ip:port"（TCP 打洞用）
    val pubUdp: String,    // UDP 打洞 socket 公网映射 "ip:port"（UDP 打洞用）
    val lan: List<String>, // 内网地址
    val tcp: Int,          // 对方 TCP 监听端口
    // 服务器观测的 TCP/UDP 端口分配偏移（TCP端口 - UDP端口，同一目标 IP）。
    // 供 pub_tcp 为空时修正 UDP 端口预测基准；缺省 null 时退化为 ±2 盲猜。
    val tcpUdpOffset: Int? = null,
    // 对端能否"监听+映射同端口"（Windows=true，Android=false）。
    // 用于决定 C/S 打洞角色：能 listen 的一方 listen，另一方 connect。
    val canListen: Boolean = true
)

/** 打洞协调事件：与某成员同时打洞。 */
data class PunchGo(
    val peer: RoomMember,
    val atMs: Long
)

/** 成员连接状态（供 UI 展示）。 */
enum class RoomConnState { IDLE, CONNECTING, CONNECTED, FAILED }

/** 发送通道（按路径角色选出）。 */
data class SendChannel(
    /** TCP Socket 或 UdpReliableSocket（都实现 StreamSocket）。 */
    val stream: com.p2p.filetransfer.protocol.StreamSocket,
    /** 收发串行化锁（TCP 用 conn.ioLock；UDP 用 rtp.ioLock）。 */
    val ioLock: kotlinx.coroutines.sync.Mutex,
    val isUdp: Boolean,
    val role: String
)

/** 成员 + 连接状态。 */
data class RoomMemberStatus(
    val member: RoomMember,
    val state: RoomConnState,
    val addr: String?,
    /** 梯度冗余：{pathId: role}，role ∈ hot/warm_safe/warm_loose/dead */
    val pathRoles: Map<String, String> = emptyMap()
)
