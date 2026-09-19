package com.p2p.filetransfer.model

/** 已发现的设备节点 */
data class DeviceNode(
    val ip: String,
    val hostname: String,
    val lastSeen: Long = System.currentTimeMillis(),
    /** "udp" | "scan" | "manual" */
    val source: String = "udp",
    val heartbeatFail: Int = 0
)
