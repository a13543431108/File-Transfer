package com.p2p.filetransfer.room

import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.Semaphore
import kotlinx.coroutines.sync.withPermit
import java.net.Socket

/** 单条到某成员的长连接。 */
class RoomConn(
    val member: RoomMember,
    @Volatile var state: RoomConnState = RoomConnState.CONNECTING,
    @Volatile var socket: Socket? = null,
    @Volatile var addr: String? = null
) {
    /** 读写锁（协程 Mutex）：发送时持锁，接收循环空闲时持锁读，串行化收发。 */
    val ioLock = kotlinx.coroutines.sync.Mutex()
}

/**
 * 房间管理器 —— 与电脑端 net/room.py 对应。
 *
 * 单一职责：维护房间成员表与“每对设备”的连接状态，调度打洞，对外提供长连接。
 *   - 一进房间即对房间内所有成员发起打洞（全连接）
 *   - 成员加入/离开时增删连接
 *   - 受并发上限约束调度打洞任务
 *   - 打洞成功后持有长连接 socket
 *   - 用 keepalive 保活，失效则重新打洞
 * 不做：信令收发（SignalingClient）、打洞算法（HolePuncher）、文件传输。
 */
class RoomManager(
    private val localTcpPort: Int,
    private val scope: CoroutineScope,
    private val log: (String) -> Unit
) {
    /** 打洞成功、长连接就绪时回调（用于启动接收循环）。 */
    var onSocketReady: ((String, Socket, RoomMember) -> Unit)? = null

    // 统一使用 synchronized(this) 保护 members/connections（避免与协程 Mutex 双锁竞争）
    private val members = HashMap<String, RoomMember>()
    private val connections = HashMap<String, RoomConn>()
    private val punchSem = Semaphore(RoomConfig.PUNCH_CONCURRENCY)
    private val puncher = HolePuncher(localTcpPort, log)

    private var keepaliveJob: Job? = null
    @Volatile private var running = false

    /** 信令客户端引用（由 Service 注入，供发起打洞请求）。 */
    @Volatile var signaling: SignalingClient? = null

    fun start() {
        running = true
        keepaliveJob = scope.launch(Dispatchers.IO) { keepaliveLoop() }
    }

    fun stop() {
        running = false
        keepaliveJob?.cancel()
        synchronized(this) {
            for (c in connections.values) closeConn(c)
            connections.clear()
        }
    }

    // ---------- 信令回调 ----------
    fun onJoined(list: List<RoomMember>) { for (m in list) addMember(m) }
    fun onMemberJoin(member: RoomMember) { addMember(member) }
    fun onMemberLeave(peerId: String) {
        synchronized(this) {
            members.remove(peerId)
            connections.remove(peerId)?.let { closeConn(it) }
        }
        log("[房间] 成员离开 " + peerId)
    }

    /** 服务器协调：与对端同时打洞。 */
    fun onPunchGo(peer: RoomMember, atMs: Long) {
        val pid = peer.id
        val need = synchronized(this) {
            members[pid] = peer
            val c = connections[pid]
            if (c != null && (c.state == RoomConnState.CONNECTING || c.state == RoomConnState.CONNECTED)) {
                false
            } else {
                connections[pid] = RoomConn(peer)
                true
            }
        }
        if (need) scope.launch(Dispatchers.IO) { punchTask(peer, atMs) }
    }

    // ---------- 入站连接识别 ----------
    /**
     * 处理 listener accept 到的连接：若匹配某成员的 TCP 公网映射，则接管为房间连接。
     * 返回 true 表示已接管（调用方不应再按普通文件接收处理）。
     */
    fun onInbound(socket: Socket, ip: String, port: Int): Boolean {
        val key = "$ip:$port"
        var readyMember: RoomMember? = null
        var readyPid: String? = null
        synchronized(this) {
            for ((pid, m) in members) {
                if (m.pubTcp.isNotEmpty() && m.pubTcp == key) {
                    val c = connections[pid]
                    if (c != null && c.state == RoomConnState.CONNECTED && c.socket != null) {
                        return true  // 已有连接，忽略重复
                    }
                    val nc = RoomConn(m, RoomConnState.CONNECTED, socket, key)
                    applyKeepalive(socket)
                    connections[pid] = nc
                    readyPid = pid
                    readyMember = m
                    break
                }
            }
        }
        // 回调移到锁外，避免在持锁时触发下游逻辑
        if (readyPid != null && readyMember != null) {
            log("[房间] 入站连接 " + readyMember!!.name + " <- " + key)
            onSocketReady?.invoke(readyPid!!, socket, readyMember!!)
            return true
        }
        return false
    }

    // ---------- 对外查询 ----------
    fun getSocket(peerId: String): Socket? = synchronized(this) {
        val c = connections[peerId]
        if (c != null && c.state == RoomConnState.CONNECTED) c.socket else null
    }

    fun getConn(peerId: String): RoomConn? = synchronized(this) { connections[peerId] }

    fun hasPeer(peerId: String): Boolean = synchronized(this) { connections.containsKey(peerId) }

    fun getPeerDid(peerId: String): String = synchronized(this) {
        members[peerId]?.did?.takeIf { it.isNotEmpty() } ?: peerId
    }

    fun getMembers(): List<RoomMemberStatus> = synchronized(this) {
        members.values.map { m ->
            val c = connections[m.id]
            RoomMemberStatus(m, c?.state ?: RoomConnState.IDLE, c?.addr)
        }
    }

    // ---------- 内部 ----------
    private fun addMember(member: RoomMember) {
        val pid = member.id
        synchronized(this) { members[pid] = member }
        // 请求打洞（服务器会给双方下发 punch_go）
        signaling?.requestPunch(pid)
    }

    private suspend fun punchTask(peer: RoomMember, atMs: Long) {
        val pid = peer.id
        punchSem.withPermit {
            var attempt = 1
            var at = atMs
            while (attempt <= RoomConfig.PUNCH_RETRY && running) {
                val result = puncher.punch(peer, at)
                if (result != null) {
                    installSocket(pid, result)
                    return
                }
                attempt++
                if (attempt <= RoomConfig.PUNCH_RETRY) {
                    delay(attempt * RoomConfig.PUNCH_RETRY_BACKOFF_S * 1000)
                    at = 0
                }
            }
            synchronized(this) {
                connections[pid]?.state = RoomConnState.FAILED
            }
            log("[房间] 连接失败 " + peer.name)
        }
    }

    private fun installSocket(peerId: String, result: PunchResult) {
        val member: RoomMember
        synchronized(this) {
            val old = connections[peerId]
            if (old != null && old.state == RoomConnState.CONNECTED && old.socket != null) {
                try { result.socket.close() } catch (_: Exception) {}
                return
            }
            if (old != null) closeConn(old)
            val nc = RoomConn(members[peerId] ?: RoomMember(peerId, "", peerId, "", "", emptyList(), 0),
                RoomConnState.CONNECTED, result.socket, result.ip + ":" + result.port)
            connections[peerId] = nc
            member = nc.member
        }
        applyKeepalive(result.socket)
        log("[房间] 已连接 " + member.name + " -> " + result.ip + ":" + result.port)
        onSocketReady?.invoke(peerId, result.socket, member)
    }

    private fun applyKeepalive(socket: Socket) {
        try { socket.keepAlive = true } catch (_: Exception) {}
    }

    private fun closeConn(c: RoomConn) {
        try { c.socket?.close() } catch (_: Exception) {}
        c.socket = null
    }

    private suspend fun keepaliveLoop() {
        while (running && scope.isActive) {
            delay(RoomConfig.NAT_KEEPALIVE_INTERVAL_MS)
            if (!running) break
            val snapshot = synchronized(this) {
                connections.filterValues { it.state == RoomConnState.CONNECTED }.toMap()
            }
            for ((pid, c) in snapshot) {
                val s = c.socket
                if (s == null || s.isClosed || !s.isConnected) {
                    log("[保活] " + pid + " 通道失效，重新打洞")
                    synchronized(this) {
                        closeConn(c)
                        c.state = RoomConnState.FAILED
                    }
                    signaling?.requestPunch(pid)
                }
            }
        }
    }
}
