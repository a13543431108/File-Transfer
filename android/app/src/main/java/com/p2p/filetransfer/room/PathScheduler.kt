package com.p2p.filetransfer.room

/**
 * 梯度冗余多路径调度（Kotlin 端，与电脑端 文件互传.py 的 Rm* 调度类对称）。
 *
 * 设计：叠加在 RoomManager 的 connections/udpConns 之上，不改动其存储。
 * 每个 peer 一个 PeerPathScheduler，管理该 peer 的多条路径的角色与保活间隔。
 *
 * 角色：hot(热备,传数据) / warm_safe(保守暖备) / warm_loose(宽松暖备) / dead
 * 保活：软性探测（指数增长逼近 NAT 超时）+ 硬性下限兜底
 */

// 协议硬性下限（秒）—— 不可突破
private val PROTO_FLOOR = mapOf("tcp" to 30, "udp" to 15)
// 协议安全上限（秒）—— 保守暖备不可超过
private val PROTO_SAFE_CAP = mapOf("tcp" to 3600, "udp" to 60)
// 角色系数
private val ROLE_FACTOR = mapOf("hot" to 1.0, "warm_safe" to 1.5, "warm_loose" to 4.0)

/**
 * 单条路径的保活间隔调度（软性探测 + 硬性下限）。
 */
class KeepaliveScheduler(val role: String, val proto: String) {
    private val floor = PROTO_FLOOR[proto] ?: 15
    private val safeCap = PROTO_SAFE_CAP[proto] ?: 60

    @Volatile var currentInterval: Int = floor * 2
        private set

    private var upperBound: Int? = null
    private var lowerBound: Int? = null
    private var successCount = 0
    private var failCount = 0
    private var stable = false

    private fun clamp(v: Int): Int {
        var x = maxOf(v, floor)
        x = if (role == "warm_safe") minOf(x, safeCap) else minOf(x, 3600)
        return x
    }

    /** 保活成功 → 软性探测：指数增长逼近边界。 */
    @Synchronized
    fun onSuccess() {
        successCount++
        upperBound = currentInterval
        if (stable) return
        val total = successCount + failCount
        if (total >= 5 && failCount.toDouble() / (total + 1) > 0.05) {
            stable = true
            return
        }
        // C 修复：纯成功（无任何失败样本）时，间隔不应无限翻倍到 3600s。
        // 没有失败样本就无从"逼近 NAT 超时边界"，继续翻倍只会让保活越来越稀疏，
        // 反而拖慢断线感知。封顶为当前角色的探测上限：
        //   · hot 需要最快感知断线 → 保持 floor*2（不增长）
        //   · warm_safe/warm_loose 允许适度增长，但不超过各自钳制上限
        if (failCount == 0) {
            val cap = if (role == "hot") floor * 2 else safeCap
            currentInterval = clamp(minOf(currentInterval * 2, cap))
            return
        }
        var c = (currentInterval * 2 * (ROLE_FACTOR[role] ?: 1.0)).toInt()
        if (lowerBound != null) {
            c = minOf(c, (upperBound!! + lowerBound!!) / 2)
            stable = true
        }
        currentInterval = clamp(c)
    }

    /** 保活失败 → 回退到安全值。 */
    @Synchronized
    fun onFailure() {
        failCount++
        lowerBound = currentInterval
        stable = false
        currentInterval = if (upperBound != null) {
            (upperBound!! + lowerBound!!) / 2
        } else {
            floor
        }
        currentInterval = clamp(currentInterval)
        if (failCount >= 3) currentInterval = floor
    }
}

/** 协议优先级：数字越小越优先当 hot。TCP > UDP-RTP。 */
private val PROTO_PRIORITY = mapOf("tcp" to 0, "udp" to 1)

/** 单条路径的运行时状态。 */
class PathRecord(
    val pathId: String,
    val proto: String,
    @Volatile var role: String,
    @Volatile var rttMs: Long = 0L
) {
    @Volatile var scheduler = KeepaliveScheduler(role, proto)
    @Volatile var lastSeen = System.currentTimeMillis()

    /** 改角色时同步重建 scheduler（保活参数随角色变化）。 */
    fun changeRole(newRole: String) {
        role = newRole
        scheduler = KeepaliveScheduler(newRole, proto)
    }
}

/**
 * 单个 peer 的路径分级与保活决策。
 */
class PeerPathScheduler(private val peerId: String, private val log: (String) -> Unit) {
    private val lock = Object()
    private val paths = HashMap<String, PathRecord>()

    /** 新路径接入（TCP/UDP 打洞成功时调用）。 */
    fun register(pathId: String, proto: String): PathRecord {
        synchronized(lock) {
            val existing = paths[pathId]
            if (existing != null && existing.role != "dead") return existing
            // 新路径先以 "warm_loose" 占位，再由 regradeLocked 按
            // (协议优先级, RTT) 重新分级；不能用 "dead" 占位，否则会被
            // regradeLocked 的 alive 过滤掉、永不上位（多路径调度失效）。
            val p = PathRecord(pathId, proto, "warm_loose")
            paths[pathId] = p
            regradeLocked()
            log("[路径] peer=" + peerId + " 注册 " + pathId + "/" + proto + " role=" + p.role)
            return p
        }
    }

    /** 按协议优先级（TCP > UDP）重排存活路径角色：hot → warm_safe → warm_loose。 */
    private fun regradeLocked() {
        val alive = paths.values.filter { it.role != "dead" }
            .sortedWith(compareBy({ PROTO_PRIORITY[it.proto] ?: 9 }, { it.rttMs }))
        val roles = listOf("hot", "warm_safe", "warm_loose")
        alive.forEachIndexed { i, p ->
            val newRole = if (i < roles.size) roles[i] else "warm_loose"
            if (p.role != newRole) p.changeRole(newRole)
        }
    }

    fun regrade() {
        synchronized(lock) { regradeLocked() }
    }

    fun remove(pathId: String) {
        synchronized(lock) { paths.remove(pathId) }
    }

    fun roles(): Map<String, String> =
        synchronized(lock) { paths.mapValues { it.value.role } }

    fun snapshot(): List<PathRecord> =
        synchronized(lock) { paths.values.toList() }

    /**
     * 热备失效 → 保守暖备上位，宽松暖备升保守。返回新的热备 pathId。
     */
    fun promoteOnHotFailure(): String? {
        synchronized(lock) {
            var hot: PathRecord? = null
            var safe: PathRecord? = null
            var loose: PathRecord? = null
            for (p in paths.values) {
                when (p.role) {
                    "hot" -> hot = p
                    "warm_safe" -> safe = p
                    "warm_loose" -> loose = p
                }
            }
            hot?.let { it.role = "dead" }
            var newHot: String? = null
            safe?.let { it.changeRole("hot"); newHot = it.pathId }
            loose?.let { it.changeRole("warm_safe") }
            return newHot
        }
    }
}
