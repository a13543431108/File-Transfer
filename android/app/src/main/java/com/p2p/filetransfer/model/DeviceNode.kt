package com.p2p.filetransfer.model

/** 已发现的设备节点 */
data class DeviceNode(
    val ip: String,
    val hostname: String,
    val lastSeen: Long = System.currentTimeMillis(),
    /** "udp" | "scan" | "manual" */
    val source: String = "udp",
    val heartbeatFail: Int = 0,
    /** 对端持久化设备 UUID（识别主键，跨 IP 变化跟踪同一设备） */
    val deviceId: String = "",
    /** 参考 MAC（Android 10+ 多为空） */
    val mac: String = ""
)
