# -*- coding: utf-8 -*-
"""UDP 可靠传输层（UDP-RTP v1）—— 共享 socket 模式。

协议（12 字节包头，大端）：
  [1B type][4B seq][4B ack][2B length][1B reserved]
    type: 0=DATA, 1=ACK, 2=FIN

架构：
  · 一个本地 UDP socket 被多个对端共享
  · 全局接收循环（由上层 RmSignalingClient 维护）从 socket 收包，
    按源地址 (ip, port) 找到对应的 UdpReliableSocket，调用其 on_packet()
  · 本类只负责：发送（通过 send_fn）、序号/ACK/重传、按序重组
"""

import struct
import threading
import time

_PKT = struct.Struct("!BIIHB")
_TYPE_DATA = 0
_TYPE_ACK = 1
_TYPE_FIN = 2
_TYPE_KEEPALIVE = 3   # P0: 空闲时定期发送，刷新 NAT conntrack
_TYPE_SYNC_REQ = 4    # P1: 发起方发 SYNC 请求
_TYPE_SYNC_ACK = 5    # P1: 响应方回 SYNC 响应
_TYPE_SYNC_COMMIT = 6 # P1: 发起方约定 TCP 打洞时刻
_TYPE_PUNCH_ROUND = 7 # P2: 打洞轮次对齐（主导方约定下一轮时刻）
_TYPE_TCP_READY = 8   # P2: TCP 映射已就绪信号（#6）
_TYPE_PUNCH_FAIL = 9  # P2: 打洞彻底失败通知（#8）

MAX_PAYLOAD = 1200
WINDOW = 64                    # 兼容名（初始值）
# 自适应窗口（AIMD）：发送端根据 ACK / 丢包动态调在途包数。
#   收到有效 ACK → 窗口 +1（加性增，封顶 WINDOW_MAX）
#   超时重传   → 窗口减半（乘性减，下限 WINDOW_MIN）
# 接收端的乱序接受范围固定为 WINDOW_MAX，保证发送端无论怎么调都能被接住。
WINDOW_MIN = 1
WINDOW_MAX = 256
WINDOW_INIT = 64
RTO_MS = 300
RTX_INTERVAL = 0.05
KEEPALIVE_INTERVAL = 15.0      # 空闲超过该秒数 → 发一个 KEEPALIVE
PEER_DEAD_TIMEOUT = 90.0       # 超过该秒数没收到对端任何包 → 判定失联
SYNC_SAMPLE_COUNT = 4          # SYNC RTT 采样次数
SYNC_TIMEOUT = 1.0             # 单次 SYNC 采样超时（秒）
SYNC_COMMIT_DELAY_MS = 500     # 距 SYNC_COMMIT 后多少毫秒执行 TCP 打洞
SYNC_COMMIT_RESEND = 3         # P2: COMMIT 重传次数（抗丢包）
SYNC_COMMIT_RESEND_INTERVAL = 0.1  # P2: COMMIT 重传间隔（秒）
PROTO_VER = 2                  # P2: 协议版本（>=2 启用 COMMIT 绝对时刻 + commit_id 去重）


class UdpReliableSocket:
    """可靠 UDP 字节流（共享 socket 模式）。

    参数：
      send_fn(peer_addr, data)：发送一个 UDP 包到 peer_addr
      peer_addr：对端的 (ip, port)

    类属性 is_udp_rtp=True：供上层识别传输类型（TCP vs UDP）。
    """

    is_udp_rtp = True

    def __init__(self, send_fn, peer_addr, log=None, on_peer_dead=None,
                 on_sync_ready=None, on_punch_round=None,
                 on_tcp_ready=None, on_punch_fail=None):
        # 收发串行化锁：与 TCP 的 RoomConn.io_lock 对齐。
        # 发送侧 get_send_channel 与接收侧 handle_room_receive 必须用【同一把】，
        # 否则应用层"发送整个文件"与"接收循环读一帧"没有互斥。
        self.io_lock = threading.Lock()
        self._send_fn = send_fn
        self.peer = peer_addr
        self.log = log or (lambda m: None)
        self._on_peer_dead = on_peer_dead   # P0: 对端失联回调(peer_addr)
        # P1: SYNC 完成回调(T_go_local_ms) —— 发起方或响应方均会触发
        self._on_sync_ready = on_sync_ready
        # P2: 打洞轮次对齐回调(round_no, t_go_r)
        self._on_punch_round = on_punch_round
        # P2 #6/#8: 对端 TCP 就绪信号 / 打洞失败通知
        self._on_tcp_ready = on_tcp_ready
        self._on_punch_fail = on_punch_fail
        self._sync_offset = None          # P2: 最近一次 SYNC offset（responder钟-本机钟）
        self._sync_rtt = None             # P2 #9: 最近一次 SYNC 最小 RTT（毫秒）
        self._peer_ver = 1                # P2: 对端协议版本（默认 1）
        self._commit_seq = 0              # P2: COMMIT 自增轮次号
        self._last_commit_id = -1         # P2: 已处理的 commit_id（去重）
        self._commit_lock = threading.Lock()

        self._send_seq = 0
        self._send_queue = {}        # {seq: [packet, sent_ts]}
        self._send_lock = threading.Lock()
        self._window = WINDOW_INIT    # 自适应发送窗口（AIMD，受 _send_lock 保护）

        self._recv_next = 0
        self._recv_buf = bytearray()
        self._out_of_order = {}      # {seq: payload}
        self._recv_cond = threading.Condition()

        self._closed = False
        self._peer_closed = False
        self._timeout = None

        # P0：keepalive + 失联检测
        self._last_send_time = time.time()
        self._last_recv_time = time.time()
        self._peer_dead_fired = False
        # P1：SYNC 状态
        self._sync_lock = threading.Lock()
        self._sync_in_progress = False   # 防止重复发起
        self._sync_samples = []          # [(rtt_ms, offset_ms)]
        self._sync_pending = {}          # {t1: threading.Event}
        self._sync_replies = {}          # {t1: (t2, t3, t4)}

        self._rtx_thread = threading.Thread(target=self._rtx_loop, daemon=True)
        self._rtx_thread.start()
        self._ka_thread = threading.Thread(target=self._keepalive_loop, daemon=True)
        self._ka_thread.start()

    # ---------- 对外接口 ----------

    def sendall(self, data):
        if not data or self._closed:
            return
        offset = 0
        while offset < len(data):
            chunk = data[offset:offset + MAX_PAYLOAD]
            offset += len(chunk)
            with self._send_lock:
                seq = self._send_seq
                self._send_seq += 1
                hdr = _PKT.pack(_TYPE_DATA, seq, self._recv_next, len(chunk), 0)
                pkt = hdr + chunk
                self._send_queue[seq] = [pkt, time.time()]
                while len(self._send_queue) >= self._window and not self._closed:
                    self._send_lock.release()
                    time.sleep(0.005)
                    self._send_lock.acquire()
            try:
                self._send_fn(self.peer, pkt)
                self._last_send_time = time.time()
            except Exception as e:
                self.log("[UDP-RTP] send 失败: %s" % e)
                return

    def recv(self, n):
        with self._recv_cond:
            deadline = time.time() + self._timeout if self._timeout else None
            while len(self._recv_buf) == 0 and not self._peer_closed:
                if deadline is None:
                    self._recv_cond.wait(0.5)
                else:
                    remain = deadline - time.time()
                    if remain <= 0:
                        raise TimeoutError("recv timeout")
                    self._recv_cond.wait(remain)
            if not self._recv_buf:
                return b""
            take = min(n, len(self._recv_buf))
            data = bytes(self._recv_buf[:take])
            del self._recv_buf[:take]
            return data

    def recv_into(self, buffer, nbytes=0):
        """兼容 socket.recv_into(buf, n)：把数据读进 buffer，返回字节数。

        _recv_file_data_with_hash 用此方法批量读数据到 memoryview。
        """
        if nbytes <= 0:
            nbytes = len(buffer)
        data = self.recv(nbytes)
        if not data:
            return 0
        buffer[:len(data)] = data
        return len(data)

    def recv_exact(self, n):
        buf = bytearray()
        while len(buf) < n:
            chunk = self.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("UDP-RTP 对端关闭")
            buf.extend(chunk)
        return bytes(buf)

    def settimeout(self, t):
        self._timeout = t

    def send_punch_round(self, round_no, t_go_r):
        """P2: 主导方向对端下发下一轮打洞时刻（responder 钟绝对毫秒）。"""
        try:
            payload = struct.pack("!QQ", round_no, t_go_r)
            self._send_fn(self.peer,
                          _PKT.pack(_TYPE_PUNCH_ROUND, 0, self._recv_next,
                                    len(payload), 0) + payload)
            self._last_send_time = time.time()
            return True
        except Exception as e:
            self.log("[打洞] 发送 PUNCH_ROUND 失败: %s" % e)
            return False

    def send_tcp_ready(self):
        """P2 #6: 告知对端本机 TCP 映射已就绪，可开始打洞。"""
        try:
            self._send_fn(self.peer,
                          _PKT.pack(_TYPE_TCP_READY, 0, self._recv_next, 0, 0))
            self._last_send_time = time.time()
            return True
        except Exception as e:
            self.log("[打洞] 发送 TCP_READY 失败: %s" % e)
            return False

    def send_punch_fail(self):
        """P2 #8: 告知对端本机 TCP 打洞彻底失败，避免对端空等。"""
        try:
            self._send_fn(self.peer,
                          _PKT.pack(_TYPE_PUNCH_FAIL, 0, self._recv_next, 0, 0))
            self._last_send_time = time.time()
            return True
        except Exception as e:
            self.log("[打洞] 发送 PUNCH_FAIL 失败: %s" % e)
            return False

    def get_sync_offset(self):
        """P2: 返回最近一次 SYNC offset（responder钟-本机钟），无则 None。"""
        return self._sync_offset

    def get_sync_rtt(self):
        """P2 #9: 返回最近一次 SYNC 最小 RTT（毫秒），无则 None。"""
        return self._sync_rtt

    def get_peer_ver(self):
        """P2: 返回对端协议版本（默认 1）。"""
        return self._peer_ver

    def start_sync(self):
        """P1：发起 SYNC 采样，完成后发送 SYNC_COMMIT 约定 TCP 打洞时刻。

        阻塞在调用线程，直至采样完成（约 1-4 秒）。由上层发起方线程调用。
        """
        with self._sync_lock:
            if self._sync_in_progress:
                self.log("[SYNC] 已有采样在进行，跳过")
                return
            self._sync_in_progress = True
            self._sync_samples = []
            self._sync_pending = {}
            self._sync_replies = {}
        try:
            # 1) 采样 SYNC_SAMPLE_COUNT 次
            for i in range(SYNC_SAMPLE_COUNT):
                if self._closed:
                    return
                t1 = int(time.time() * 1000)
                evt = threading.Event()
                with self._sync_lock:
                    self._sync_pending[t1] = evt
                try:
                    payload = struct.pack("!QB", t1, PROTO_VER)
                    self._send_fn(self.peer,
                                  _PKT.pack(_TYPE_SYNC_REQ, 0, self._recv_next,
                                            len(payload), 0) + payload)
                    self._last_send_time = time.time()
                except Exception as e:
                    self.log("[SYNC] 发送 REQ 失败: %s" % e)
                    break
                ok = evt.wait(SYNC_TIMEOUT)
                with self._sync_lock:
                    reply = self._sync_replies.pop(t1, None)
                    self._sync_pending.pop(t1, None)
                if ok and reply:
                    rtt, offset, _t4 = reply
                    self._sync_samples.append((rtt, offset))
                    self.log("[SYNC] 采样 %d/%d: RTT=%dms offset=%dms"
                             % (i + 1, SYNC_SAMPLE_COUNT, rtt, offset))
                else:
                    self.log("[SYNC] 采样 %d 超时" % (i + 1))
                time.sleep(0.05)

            # 2) 取最小 RTT 那次
            if not self._sync_samples:
                self.log("[SYNC] 无有效采样，放弃 SYNC")
                return
            best_rtt, best_offset = min(self._sync_samples, key=lambda s: s[0])
            self._sync_offset = best_offset
            self._sync_rtt = best_rtt
            self.log("[SYNC] 最佳：RTT=%dms offset=%dms（共 %d 次采样，peer_ver=%d）"
                     % (best_rtt, best_offset, len(self._sync_samples), self._peer_ver))

            # 3) 发 SYNC_COMMIT
            delta_ms = SYNC_COMMIT_DELAY_MS
            t_send = int(time.time() * 1000)
            t_go = t_send + delta_ms          # 本机钟
            best_offset = self._sync_offset or 0
            self._commit_seq += 1
            commit_id = self._commit_seq
            try:
                if self._peer_ver >= PROTO_VER:
                    # v2：绝对时刻（responder 钟）+ commit_id，重传多次（幂等）
                    t_go_r = t_go + best_offset
                    payload = struct.pack("!QQ", t_go_r, commit_id)
                    for _ in range(SYNC_COMMIT_RESEND):
                        self._send_fn(self.peer,
                                      _PKT.pack(_TYPE_SYNC_COMMIT, 0, self._recv_next,
                                                len(payload), 0) + payload)
                        self._last_send_time = time.time()
                        time.sleep(SYNC_COMMIT_RESEND_INTERVAL)
                    self.log("[SYNC] 已发 COMMIT(v2) T_go_I=%d T_go_R=%d id=%d x%d"
                             % (t_go, t_go_r, commit_id, SYNC_COMMIT_RESEND))
                else:
                    # v1 兼容：相对 delta，单发
                    payload = struct.pack("!Q", delta_ms)
                    self._send_fn(self.peer,
                                  _PKT.pack(_TYPE_SYNC_COMMIT, 0, self._recv_next,
                                            len(payload), 0) + payload)
                    self._last_send_time = time.time()
                    self.log("[SYNC] 已发 COMMIT(v1) T_go=%d（%dms 后）" % (t_go, delta_ms))
            except Exception as e:
                self.log("[SYNC] 发送 COMMIT 失败: %s" % e)
                return

            # 4) 通知上层（发起方）：到 t_go 执行 TCP 打洞
            cb = self._on_sync_ready
            if cb:
                try:
                    cb(t_go)
                except Exception as e:
                    self.log("[SYNC] on_sync_ready 回调异常: %s" % e)
        finally:
            with self._sync_lock:
                self._sync_in_progress = False

    def is_dead(self):
        """对端是否已判定失联（供 RoomManager 保活循环检查）。"""
        if self._closed:
            return True
        if self._peer_dead_fired:
            return True
        return (time.time() - self._last_recv_time) > PEER_DEAD_TIMEOUT

    def gettimeout(self):
        return self._timeout

    def getpeername(self):
        return self.peer

    def close(self):
        if self._closed:
            return
        self._closed = True
        try:
            self._send_fn(self.peer, _PKT.pack(_TYPE_FIN, 0, self._recv_next, 0, 0))
        except Exception:
            pass
        with self._recv_cond:
            self._recv_cond.notify_all()

    # ---------- 接收（由全局接收循环调用） ----------

    def on_packet(self, data):
        """处理一个来自本对端的 UDP 包（已由上层按源地址过滤）。"""
        if len(data) < 12 or self._closed:
            return
        try:
            ptype, seq, ack, length, _ = _PKT.unpack(data[:12])
        except struct.error:
            return
        payload = data[12:12 + length]
        # P0：记录对端最后活动时间（任何类型的包都算）
        self._last_recv_time = time.time()

        # 累积确认：删除所有 seq < ack
        if ack > 0:
            with self._send_lock:
                stale = [s for s in self._send_queue if s < ack]
                advanced = len(stale)
                for s in stale:
                    self._send_queue.pop(s, None)
                # AIMD 加性增：有确认推进 → 窗口 +advanced，封顶 WINDOW_MAX
                if advanced > 0:
                    self._window = min(WINDOW_MAX, self._window + advanced)

        if ptype == _TYPE_KEEPALIVE:
            # 收到对端 keepalive：回一个空 ACK，让对端也更新 lastRecvTime
            # （实现双向存活检测：A 发 keepalive，B 回 ACK，A 就知道 B 还活着）
            try:
                self._send_fn(self.peer,
                              _PKT.pack(_TYPE_ACK, 0, self._recv_next, 0, 0))
                self._last_send_time = time.time()
            except Exception:
                pass
            return

        # ===== P1: SYNC 消息处理 =====
        if ptype == _TYPE_SYNC_REQ:
            # 收到 SYNC_REQ(t1[, ver])：记录 t2（收时刻），立即回 SYNC_ACK(t1, t2, t3)
            if len(payload) >= 8:
                t1 = struct.unpack("!Q", payload[:8])[0]
                if len(payload) >= 9:
                    self._peer_ver = payload[8]   # P2: 记录对端协议版本
                t2 = int(time.time() * 1000)
                t3 = int(time.time() * 1000)
                try:
                    ack_payload = struct.pack("!QQQB", t1, t2, t3, PROTO_VER)
                    self._send_fn(self.peer,
                                  _PKT.pack(_TYPE_SYNC_ACK, 0, self._recv_next,
                                            len(ack_payload), 0) + ack_payload)
                    self._last_send_time = time.time()
                except Exception:
                    pass
            return

        if ptype == _TYPE_SYNC_ACK:
            # 收到 SYNC_ACK(t1, t2, t3[, ver])：算 RTT 和 offset，触发等待中的采样
            if len(payload) >= 24:
                t1, t2, t3 = struct.unpack("!QQQ", payload[:24])
                if len(payload) >= 25:
                    self._peer_ver = payload[24]   # P2: initiator 从 ACK 学 responder 版本
                t4 = int(time.time() * 1000)
                rtt = (t4 - t1) - (t3 - t2)
                # offset = 对端时钟 - 本机时钟（通过 NTP 公式）
                offset = ((t2 - t1) + (t3 - t4)) // 2
                with self._sync_lock:
                    evt = self._sync_pending.get(t1)
                    if evt is not None:
                        self._sync_replies[t1] = (rtt, offset, t4)
                        evt.set()
            return

        if ptype == _TYPE_SYNC_COMMIT:
            if len(payload) >= 16:
                # P2: v2 —— t_go_R 是 responder 钟绝对时刻，commit_id 去重
                t_go_r, commit_id = struct.unpack("!QQ", payload[:16])
                with self._commit_lock:
                    if commit_id == self._last_commit_id:
                        return   # 重传包，已处理
                    self._last_commit_id = commit_id
                self.log("[SYNC] 收到 COMMIT(v2) T_go=%d id=%d" % (t_go_r, commit_id))
                cb = self._on_sync_ready
                if cb:
                    try:
                        cb(t_go_r)
                    except Exception as e:
                        self.log("[SYNC] on_sync_ready 回调异常: %s" % e)
            elif len(payload) >= 8:
                # v1 兼容：delta 相对本机
                delta_ms = struct.unpack("!Q", payload[:8])[0]
                t_go = int(time.time() * 1000) + delta_ms
                self.log("[SYNC] 收到 COMMIT(v1) T_go=%d（%dms 后）" % (t_go, delta_ms))
                cb = self._on_sync_ready
                if cb:
                    try:
                        cb(t_go)
                    except Exception as e:
                        self.log("[SYNC] on_sync_ready 回调异常: %s" % e)
            return

        if ptype == _TYPE_PUNCH_ROUND:
            # P2: 主导方下发下一轮打洞时刻（responder 钟绝对时刻）
            if len(payload) >= 16:
                round_no, t_go_r = struct.unpack("!QQ", payload[:16])
                self.log("[打洞] 收到轮次对齐 round=%d T_go=%d" % (round_no, t_go_r))
                cb = self._on_punch_round
                if cb:
                    try:
                        cb(round_no, t_go_r)
                    except Exception as e:
                        self.log("[打洞] on_punch_round 回调异常: %s" % e)
            return

        if ptype == _TYPE_TCP_READY:
            # P2 #6: 对端 TCP 映射已就绪
            self.log("[打洞] 收到对端 TCP_READY（可开始打洞）")
            cb = self._on_tcp_ready
            if cb:
                try:
                    cb()
                except Exception as e:
                    self.log("[打洞] on_tcp_ready 回调异常: %s" % e)
            return

        if ptype == _TYPE_PUNCH_FAIL:
            # P2 #8: 对端 TCP 打洞彻底失败
            self.log("[打洞] 收到对端 PUNCH_FAIL（对端已放弃 TCP）")
            cb = self._on_punch_fail
            if cb:
                try:
                    cb()
                except Exception as e:
                    self.log("[打洞] on_punch_fail 回调异常: %s" % e)
            return

        if ptype == _TYPE_DATA:
            ack_to_send = self._recv_next
            with self._recv_cond:
                if seq == self._recv_next:
                    self._recv_buf.extend(payload)
                    self._recv_next += 1
                    while self._recv_next in self._out_of_order:
                        self._recv_buf.extend(self._out_of_order.pop(self._recv_next))
                        self._recv_next += 1
                    self._recv_cond.notify_all()
                    ack_to_send = self._recv_next
                elif seq > self._recv_next and seq < self._recv_next + WINDOW_MAX:
                    self._out_of_order[seq] = payload
                    ack_to_send = self._recv_next
                # else：重复包，仍回 ACK
            try:
                self._send_fn(self.peer, _PKT.pack(_TYPE_ACK, 0, ack_to_send, 0, 0))
                self._last_send_time = time.time()
            except Exception:
                pass
        elif ptype == _TYPE_FIN:
            with self._recv_cond:
                self._peer_closed = True
                self._recv_cond.notify_all()

    # ---------- 重传线程 ----------

    def _rtx_loop(self):
        while not self._closed:
            time.sleep(RTX_INTERVAL)
            now = time.time()
            with self._send_lock:
                to_resend = [(s, q[0]) for s, q in self._send_queue.items()
                             if now - q[1] > RTO_MS / 1000.0]
            if to_resend:
                # AIMD 乘性减：出现超时重传（疑似丢包/拥塞）→ 窗口减半
                with self._send_lock:
                    self._window = max(WINDOW_MIN, self._window // 2)
            for seq, pkt in to_resend:
                try:
                    self._send_fn(self.peer, pkt)
                    self._last_send_time = time.time()
                except Exception:
                    continue
                with self._send_lock:
                    if seq in self._send_queue:
                        self._send_queue[seq][1] = now

    # ---------- P0: keepalive + 失联检测 ----------

    def _keepalive_loop(self):
        """空闲超过 KEEPALIVE_INTERVAL 秒 → 发一个 KEEPALIVE 包（刷新 conntrack）。

        同时检测对端是否失联（PEER_DEAD_TIMEOUT 秒无收包 → 通知上层）。
        """
        while not self._closed:
            time.sleep(5.0)
            if self._closed:
                break
            now = time.time()
            # 1) 发送 keepalive（仅当空闲）
            if now - self._last_send_time >= KEEPALIVE_INTERVAL:
                # 双检：sleep 期间可能已被 close
                if self._closed:
                    break
                try:
                    pkt = _PKT.pack(_TYPE_KEEPALIVE, 0, self._recv_next, 0, 0)
                    self._send_fn(self.peer, pkt)
                    self._last_send_time = now
                except Exception:
                    pass
            # 2) 对端失联检测（只触发一次）
            if (not self._peer_dead_fired
                    and now - self._last_recv_time > PEER_DEAD_TIMEOUT):
                self._peer_dead_fired = True
                self.log("[UDP-RTP] 对端 %s 失联（%.0f 秒无包）"
                         % (self.peer, now - self._last_recv_time))
                cb = self._on_peer_dead
                if cb:
                    try:
                        cb(self.peer)
                    except Exception as e:
                        self.log("[UDP-RTP] on_peer_dead 回调异常: %s" % e)
