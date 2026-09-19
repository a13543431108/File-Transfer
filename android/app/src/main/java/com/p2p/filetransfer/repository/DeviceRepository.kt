package com.p2p.filetransfer.repository

import com.p2p.filetransfer.model.DeviceNode
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

/**
 * 设备节点内存管理（线程安全）。
 * 通过 StateFlow 向 UI 暴露当前节点列表。
 */
class DeviceRepository {

    private val lock = Any()
    private val nodes = LinkedHashMap<String, DeviceNode>()

    private val _nodesFlow = MutableStateFlow<List<DeviceNode>>(emptyList())
    val nodesFlow: StateFlow<List<DeviceNode>> = _nodesFlow.asStateFlow()

    /** 添加或更新节点；返回是否为新增节点 */
    fun upsert(ip: String, hostname: String, source: String): Boolean {
        var isNew = false
        synchronized(lock) {
            val existing = nodes[ip]
            if (existing == null) {
                nodes[ip] = DeviceNode(
                    ip = ip,
                    hostname = hostname,
                    lastSeen = System.currentTimeMillis(),
                    source = source,
                    heartbeatFail = 0
                )
                isNew = true
            } else {
                nodes[ip] = existing.copy(
                    hostname = if (hostname.isNotEmpty()) hostname else existing.hostname,
                    lastSeen = System.currentTimeMillis(),
                    source = source,
                    heartbeatFail = 0
                )
            }
        }
        publish()
        return isNew
    }

    /** 更新节点心跳时间（不改变其他字段），若不存在则忽略 */
    fun touch(ip: String) {
        synchronized(lock) {
            val existing = nodes[ip] ?: return
            nodes[ip] = existing.copy(lastSeen = System.currentTimeMillis(), heartbeatFail = 0)
        }
    }

    /** 心跳失败一次；返回 true 表示已连续失败达到阈值并可移除 */
    fun failHeartbeat(ip: String, threshold: Int): Boolean {
        var shouldRemove = false
        synchronized(lock) {
            val existing = nodes[ip] ?: return false
            val fails = existing.heartbeatFail + 1
            if (fails >= threshold) {
                nodes.remove(ip)
                shouldRemove = true
            } else {
                nodes[ip] = existing.copy(heartbeatFail = fails)
            }
        }
        if (shouldRemove) publish()
        return shouldRemove
    }

    /** 心跳成功，重置失败计数 */
    fun succeedHeartbeat(ip: String) {
        synchronized(lock) {
            val existing = nodes[ip] ?: return
            nodes[ip] = existing.copy(heartbeatFail = 0, lastSeen = System.currentTimeMillis())
        }
    }

    fun remove(ip: String) {
        val removed = synchronized(lock) { nodes.remove(ip) != null }
        if (removed) publish()
    }

    fun snapshot(): List<DeviceNode> = synchronized(lock) { nodes.values.toList() }

    fun has(ip: String): Boolean = synchronized(lock) { nodes.containsKey(ip) }

    /** 清理超过 [timeoutMs] 未响应的节点 */
    fun cleanExpired(timeoutMs: Long) {
        val now = System.currentTimeMillis()
        var changed = false
        synchronized(lock) {
            val it = nodes.entries.iterator()
            while (it.hasNext()) {
                if (now - it.next().value.lastSeen > timeoutMs) {
                    it.remove()
                    changed = true
                }
            }
        }
        if (changed) publish()
    }

    private fun publish() {
        _nodesFlow.value = snapshot().sortedByDescending { it.lastSeen }
    }
}
