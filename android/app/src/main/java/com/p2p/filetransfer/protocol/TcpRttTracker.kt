package com.p2p.filetransfer.protocol

import java.util.concurrent.ConcurrentHashMap

/**
 * TCP 长连接心跳 RTT 探测 —— 本地计时，与 UDP-RTP 心跳对称。
 *
 * 流程（两端对称）：
 *   1. 空闲时（长连接保活循环）发送 FLAG_PING，记下发时刻
 *   2. 对端接收循环读到 FLAG_PING，回 FLAG_PONG
 *   3. 本端接收循环读到 FLAG_PONG，算 RTT 并存表
 *
 * 零协议变更（除新增 2 个单字节标志），不影响文件数据传输协议。
 * 采用 object 单例：发送侧（RoomManager）与接收侧（TcpFileTransfer）
 * 无需互相持有引用即可共享状态。
 */
object TcpRttTracker {

    /** peerKey -> 最近一次 PING 发出时刻（纳秒） */
    private val pingSentAtNanos = ConcurrentHashMap<String, Long>()

    /** peerKey -> 最近一次测得 RTT（毫秒），-1 表示暂无 */
    private val rttMs = ConcurrentHashMap<String, Long>()

    /** 记录已发出 PING（由发送侧调用）。 */
    fun markPingSent(peerKey: String) {
        pingSentAtNanos[peerKey] = System.nanoTime()
    }

    /**
     * 记录已收到 PONG（由接收侧调用），返回本次 RTT（毫秒）。
     * 若没有匹配的在途 PING（如数据流里混入的 PONG），返回 null。
     */
    fun markPongReceived(peerKey: String): Long? {
        val t = pingSentAtNanos.remove(peerKey) ?: return null
        val rtt = (System.nanoTime() - t) / 1_000_000
        rttMs[peerKey] = rtt
        return rtt
    }

    /** 读取最近一次 RTT（毫秒），-1 表示暂无数据。 */
    fun getRttMs(peerKey: String): Long = rttMs[peerKey] ?: -1L

    /** 清理某 peer 的状态（成员离开时调用）。 */
    fun clear(peerKey: String) {
        pingSentAtNanos.remove(peerKey)
        rttMs.remove(peerKey)
    }
}
