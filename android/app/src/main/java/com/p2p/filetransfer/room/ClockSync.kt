package com.p2p.filetransfer.room

/**
 * 全局时钟校准（NTP 式单次往返，多次采样取最小 RTT）。
 *
 * offsetMs = 服务器时钟 - 本地时钟
 * 服务器下发的打洞时刻 at_ms 是【服务器时钟】，本地需换算：
 *     local_target = at_ms - offsetMs
 *
 * 由 SignalingClient 在加入房间后调用 sync() 更新；HolePuncher 读取。
 */
object ClockSync {
    @Volatile var offsetMs: Long = 0
        private set

    @Volatile var lastRttMs: Long = -1
        private set

    fun update(offset: Long, rtt: Long) {
        offsetMs = offset
        lastRttMs = rtt
    }

    /** 把服务器时刻换算成本地时刻（毫秒）。 */
    fun toLocal(serverMs: Long): Long = serverMs - offsetMs
}
