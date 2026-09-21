import socket
import struct
import threading
import hashlib
import os
import sys
import time
import json
import ipaddress
import re
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import zlib
import logging
import uuid

# 跨程序拖拽支持（可选依赖）。
# 未安装时程序照常运行，只是列表区域不支持拖拽，会在启动日志中提示安装。
try:
    from tkinterdnd2 import TkinterDnD, DND_FILES
    DND_AVAILABLE = True
except ImportError:
    TkinterDnD = None
    DND_FILES = None
    DND_AVAILABLE = False

# ================= 配置 =================
UDP_PORT = 9998          # 设备发现（心跳、广播）
TCP_PORT = 9999          # 文件传输
SCAN_PORT = 9997         # 扫描探测端口（与设备发现隔离，防止扫描影响收发）
IPC_PORT  = 59998        # 单实例转发端口（本地回环 127.0.0.1）：把拖到 .py/.exe 图标上的
                         # 文件路径转给已运行实例；冷启动时由本进程作为服务器
BROADCAST_INTERVAL = 1      # 正常广播间隔（1秒）
BROADCAST_BURST_INTERVAL = 0.2  # 启动时爆发广播间隔（0.2秒）
BROADCAST_BURST_DURATION = 5    # 爆发广播持续秒数
NODE_TIMEOUT = 600
BUFFER_SIZE = 1 * 1024 * 1024   # 1 MB 初始值，会动态调整
MAX_BUFFER_SIZE = 8 * 1024 * 1024  # 最大缓冲区 8MB（防止内存占用过高）
SOCKET_BUFFER_SIZE = 16 * 1024 * 1024  # socket 缓冲区大小 16MB（高延迟网络适配）
FLAG_FILE   = 0                 # 标志位：文件
FLAG_FOLDER = 1                 #  
FLAG_COMPRESS = 0x01
FLAG_RESUME   = 0x02            # 断点续传标志
AUTO_SCAN_INTERVAL = 20         # 心跳扫描间隔
SCAN_TIMEOUT = 0.5              # 扫描超时500ms，适配异地组网高延迟
SCAN_THREADS = 1000             # 1000线程并发扫描
MAX_SCAN_IPS = 65536     # 单次扫描最大IP数，防止大网段（如/8）耗尽资源

# 设置日志
logging.basicConfig(
    filename='transfer.log',
    level=logging.INFO,
    format='[%(asctime)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

TEXT_EXTENSIONS = {
    '.txt', '.log', '.json', '.xml', '.py', '.js', '.html', '.htm',
    '.css', '.csv', '.yaml', '.yml', '.toml', '.ini', '.cfg', '.conf',
    '.md', '.rst', '.sh', '.bat', '.ps1', '.vbs'
}

SENDFILE_SUPPORT = hasattr(os, 'sendfile')
RESUME_DIR = Path.home() / '.p2p_resume'
RESUME_DIR.mkdir(exist_ok=True)
# 默认保存目录
SAVE_DIR = Path("Received")
CONFIG_FILE = Path.home() / '.p2p_config.json'


def get_or_create_device_id():
    """获取或生成持久化的设备 UUID（稳定标识，跨重启不变）。

    存在 ~/.p2p_config.json 的 device_id 字段里。
    用途：
      · 跨设备识别同一台机器（IP 变化不影响识别）
      · 去重：同一设备多网卡/多 IP 只显示一个条目
    """
    cfg = {}
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, 'r') as f:
                cfg = json.load(f) or {}
        except Exception:
            cfg = {}
    did = cfg.get('device_id')
    if did:
        return did
    did = str(uuid.uuid4())
    cfg['device_id'] = did
    try:
        with open(CONFIG_FILE, 'w') as f:
            json.dump(cfg, f)
    except Exception:
        pass
    return did


def get_mac_address():
    """尽力获取本机 MAC 地址。

    注意：这是**参考信息**（展示用），不作为识别主键。
    原因：Windows 多网卡时 uuid.getnode() 只返回一个；
    虚拟网卡/VPN 下 MAC 常变。
    """
    try:
        node = uuid.getnode()
        # uuid.getnode() 第 41 位为 1 表示随机生成（拿不到真实 MAC）
        if (node >> 40) & 1:
            return ""
        hexs = '%012X' % node
        return ':'.join(hexs[i:i+2] for i in range(0, 12, 2))
    except Exception:
        return ""

# ================= 通用工具 =================
def get_all_local_ips(ipv6=False):
    """获取本机所有IP地址，若ipv6=True则包括IPv6"""
    ips = set()
    try:
        addrinfos = socket.getaddrinfo(socket.gethostname(), None, socket.AF_UNSPEC, socket.SOCK_STREAM)
        for info in addrinfos:
            addr = info[4][0]
            if ipv6:
                if ':' in addr and not addr.startswith('::1'):
                    ips.add(addr)
            else:
                if '.' in addr and not addr.startswith('127.'):
                    ips.add(addr)
    except:
        pass
    if not ips:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(('8.8.8.8', 1))
            ips.add(s.getsockname()[0])
            s.close()
        except:
            pass
    return list(ips)

def get_subnet_for_ip(ip_str):
    if ':' in ip_str:  # IPv6，暂不支持子网推断
        return None
    try:
        ip = ipaddress.IPv4Address(ip_str)
        if ip_str.startswith('169.254'):
            return f'{ip_str}/16'
        elif ip_str.startswith('10.'):
            # 对10.x.x.x用/24代替/8，避免ZeroTier/Tailscale大网段扫描1600万IP
            return f'{ip_str}/24'
        elif ip_str.startswith('172.') and 16 <= int(ip_str.split('.')[1]) <= 31:
            return f'{ip_str}/12'
        else:
            return f'{ip_str}/24'
    except:
        return None

def get_all_subnets():
    subnets = set()
    for ip in get_all_local_ips(ipv6=False):
        cidr = get_subnet_for_ip(ip)
        if cidr:
            subnets.add(cidr)
    return list(subnets)

def get_broadcast_addrs():
    addrs = set()
    for ip_str in get_all_local_ips(ipv6=False):
        cidr = get_subnet_for_ip(ip_str)
        if cidr:
            try:
                net = ipaddress.IPv4Network(cidr, strict=False)
                addrs.add(str(net.broadcast_address))
            except:
                pass
    if not addrs:
        addrs.add('255.255.255.255')
    return list(addrs)

def human_size(n):
    for unit in ('B','KB','MB','GB','TB'):
        if n < 1024:
            return f"{n:.2f} {unit}"
        n /= 1024
    return f"{n:.2f} PB"

def compute_sha256(filepath, offset=0, length=None):
    sha = hashlib.sha256()
    with open(filepath, 'rb', buffering=BUFFER_SIZE) as f:
        if offset:
            f.seek(offset)
        remaining = length if length is not None else os.path.getsize(filepath) - offset
        while remaining > 0:
            chunk = f.read(min(BUFFER_SIZE, remaining))
            if not chunk:
                break
            sha.update(chunk)
            remaining -= len(chunk)
    return sha.hexdigest()

def recv_exact(sock, size):
    """精确接收指定字节数，使用 bytearray 避免频繁内存分配"""
    if size <= 0:
        return b''
    buf = bytearray(size)
    offset = 0
    while offset < size:
        chunk = sock.recv(size - offset)
        if not chunk:
            raise ConnectionError("连接中断")
        buf[offset:offset+len(chunk)] = chunk
        offset += len(chunk)
    return bytes(buf)

def format_speed(bytes_per_sec):
    if bytes_per_sec < 1024:
        return f"{bytes_per_sec:.1f} B/s"
    elif bytes_per_sec < 1024**2:
        return f"{bytes_per_sec/1024:.1f} KB/s"
    elif bytes_per_sec < 1024**3:
        return f"{bytes_per_sec/1024**2:.1f} MB/s"
    else:
        return f"{bytes_per_sec/1024**3:.1f} GB/s"


class SpeedTracker:
    """滑动窗口瞬时速度追踪器。

    背景：进度回调里如果直接用「累计字节 / 累计时间」，得到的是**平均速度**。
    传输长时间任务（562 个文件、19GB）时，速度波动大，平均值会严重滞后，
    用户看到的"速度"其实是历史平均，不符合直觉。

    本类维护最近 WINDOW 秒内的 (时间戳, 字节) 采样，用「窗口内字节差 / 时间差」
    计算**瞬时速度**，更接近任务管理器/下载器显示的效果。

    用法：
        tracker = SpeedTracker()
        ...
        speed = tracker.update(current_bytes)   # 返回字节/秒
    """

    def __init__(self, window=2.0, min_interval=0.3):
        self.window = window
        self.min_interval = min_interval
        self._samples = []   # [(t, bytes), ...]

    def update(self, current_bytes):
        """传入累计字节数，返回近期瞬时速度（字节/秒）。"""
        now = time.time()
        # 采样节流：距离上次采样太近就跳过，避免高频抖动
        if self._samples and (now - self._samples[-1][0]) < self.min_interval:
            # 但仍要返回当前估算值
            return self._compute()
        self._samples.append((now, current_bytes))
        # 丢弃窗口外的旧样本，至少保留 2 个
        cutoff = now - self.window
        while len(self._samples) > 2 and self._samples[0][0] < cutoff:
            self._samples.pop(0)
        return self._compute()

    def _compute(self):
        if len(self._samples) < 2:
            return 0.0
        t0, b0 = self._samples[0]
        t1, b1 = self._samples[-1]
        dt = t1 - t0
        if dt <= 0:
            return 0.0
        return max(0.0, (b1 - b0) / dt)

    def reset(self):
        self._samples.clear()

def format_time(seconds):
    if seconds < 60:
        return f"{seconds:.0f}s"
    elif seconds < 3600:
        return f"{seconds//60:.0f}m {seconds%60:.0f}s"
    else:
        return f"{seconds//3600:.0f}h {(seconds%3600)//60:.0f}m"

def is_text_file(filepath):
    ext = os.path.splitext(filepath)[1].lower()
    return ext in TEXT_EXTENSIONS

# ================= 断点续传状态管理 =================
def get_resume_file(target_ip, filename, role='sender'):
    """获取续传状态文件路径，role=sender/recv 区分发送方和接收方"""
    safe_name = filename.replace('/', '_').replace('\\', '_')
    return RESUME_DIR / f"resume_{role}_{target_ip}_{safe_name}.json"

def save_resume_state(target_ip, filepath, offset, total_size, mtime, role='sender'):
    """保存续传状态，role=sender（发送方记录要发的文件）或 recv（接收方记录已收的文件）"""
    resume_file = get_resume_file(target_ip, os.path.basename(filepath), role)
    data = {
        'filepath': filepath,
        'offset': offset,
        'total_size': total_size,
        'mtime': mtime,
        'target_ip': target_ip,
        'role': role,
        'timestamp': time.time()
    }
    with open(resume_file, 'w') as f:
        json.dump(data, f)

def load_resume_state(target_ip, filepath, role='sender'):
    """加载续传状态，role=sender 时检查源文件是否变更，role=recv 时检查目标文件是否变更"""
    resume_file = get_resume_file(target_ip, os.path.basename(filepath), role)
    if not resume_file.exists():
        return None
    try:
        with open(resume_file, 'r') as f:
            data = json.load(f)
        if role == 'sender':
            # 发送方：检查源文件是否被修改
            if data.get('filepath') == filepath and os.path.exists(filepath):
                current_mtime = os.path.getmtime(filepath)
                if current_mtime == data.get('mtime'):
                    return data
        else:
            # 接收方：检查目标文件是否存在且未变化
            dest_path = Path(SAVE_DIR) / os.path.basename(filepath)
            if dest_path.exists():
                current_size = dest_path.stat().st_size
                if current_size == data.get('offset'):
                    return data
        # 不匹配则删除记录
        resume_file.unlink()
        return None
    except:
        return None

def delete_resume_state(target_ip, filepath, role='sender'):
    """删除续传状态"""
    resume_file = get_resume_file(target_ip, os.path.basename(filepath), role)
    if resume_file.exists():
        resume_file.unlink()

def save_resume_progress(target_ip, filepath, offset, total_size, mtime, role='sender'):
    """传输中实时保存进度，供接收方和发送方中途崩溃恢复"""
    save_resume_state(target_ip, filepath, offset, total_size, mtime, role)

# 文件夹续传状态管理（每个文件独立状态）
def get_folder_resume_file(target_ip, folder_name, rel_path):
    """获取文件夹中单个文件的续传状态文件路径"""
    safe_folder = folder_name.replace('/', '_').replace('\\', '_')
    safe_path = rel_path.replace('/', '_').replace('\\', '_')
    return RESUME_DIR / f"folder_resume_{target_ip}_{safe_folder}_{safe_path}.json"

def save_folder_resume_state(target_ip, folder_name, rel_path, offset, total_size, mtime):
    """保存文件夹中单个文件的续传状态"""
    resume_file = get_folder_resume_file(target_ip, folder_name, rel_path)
    data = {
        'folder_name': folder_name,
        'rel_path': rel_path,
        'offset': offset,
        'total_size': total_size,
        'mtime': mtime,
        'target_ip': target_ip,
        'timestamp': time.time()
    }
    with open(resume_file, 'w') as f:
        json.dump(data, f)

def load_folder_resume_state(target_ip, folder_name, rel_path, dest_path):
    """加载文件夹中单个文件的续传状态，检查目标文件是否有效"""
    resume_file = get_folder_resume_file(target_ip, folder_name, rel_path)
    if not resume_file.exists():
        return None
    try:
        with open(resume_file, 'r') as f:
            data = json.load(f)
        if dest_path and dest_path.exists():
            current_size = dest_path.stat().st_size
            if current_size == data.get('offset') and current_size < data.get('total_size', 0):
                return data
        resume_file.unlink()
        return None
    except:
        return None

def delete_folder_resume_state(target_ip, folder_name, rel_path):
    """删除文件夹中单个文件的续传状态"""
    resume_file = get_folder_resume_file(target_ip, folder_name, rel_path)
    if resume_file.exists():
        resume_file.unlink()

# ================= 网络节点 =================
class Node(threading.Thread):
    def __init__(self, gui):
        super().__init__(daemon=True)
        self.gui = gui
        self.running = True
        self.my_ips = get_all_local_ips(ipv6=False)  # IPv4用于广播
        self.my_ips_v6 = get_all_local_ips(ipv6=True)
        self.broadcast_addrs = get_broadcast_addrs()
        # 设备名优先使用用户配置（GUI 从 ~/.p2p_config.json 读取），
        # 未配置则用系统主机名
        custom_name = getattr(gui, 'device_name', None)
        self.hostname = custom_name if custom_name else socket.gethostname()
        # 稳定设备标识（跨重启不变）+ 参考 MAC
        self.device_id = get_or_create_device_id()
        self.mac = get_mac_address()
        self.nodes = {}
        self.lock = threading.Lock()
        self.broadcast_sockets = []
        # 所有需要主动关闭的监听 socket（用于干净退出）
        self.listen_sockets = []
        self.auto_scan_enabled = False
        self.scanning = False
        self.pausing_network = False  # 传输时暂停广播/扫描/心跳

    def run(self):
        threading.Thread(target=self.udp_listener, daemon=True).start()
        threading.Thread(target=self.scan_listener, daemon=True).start()
        self._start_broadcasters()
        threading.Thread(target=self.tcp_file_receiver, daemon=True).start()
        self.gui.root.after(2000, self.clean_nodes)
        # 启动后立即扫描，快速发现设备
        self.gui.root.after(500, self._startup_scan)
        self.gui.log(f"本机 IPv4: {', '.join(self.my_ips)}")
        if self.my_ips_v6:
            self.gui.log(f"本机 IPv6: {', '.join(self.my_ips_v6)}")
        self.gui.log(f"广播地址: {', '.join(self.broadcast_addrs)}")
        self.gui.log(f"扫描端口: {SCAN_PORT}（与设备发现端口 {UDP_PORT} 隔离）")
        if SENDFILE_SUPPORT:
            self.gui.log("零拷贝 sendfile 已启用")
        self.gui.log("传输优化: 动态缓冲区, zlib压缩(文本), 断点续传, IPv6支持")

    def _startup_scan(self):
        """启动后立即自动扫描所有子网"""
        self.gui.log("[扫描] 启动快速扫描...")
        threading.Thread(target=self.auto_scan_all_subnets, daemon=True).start()

    def broadcast_discovery(self):
        """广播搜索：向所有广播地址发送爆发式探测包，等待设备回复（比逐IP扫描快得多）"""
        self.gui.log("[广播搜索] 发送广播探测，等待设备回复...")
        msg = self._build_msg(discovery=True)
        # 向所有广播地址爆发发送 3 次
        for _ in range(3):
            for bcast_addr in self.broadcast_addrs:
                try:
                    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                    s.settimeout(1)
                    s.sendto(msg, (bcast_addr, UDP_PORT))
                    s.close()
                except:
                    pass
            time.sleep(0.2)
        # 等待 5 秒让回复包到达（udp_listener 会自动添加回复的设备）
        time.sleep(5)
        self.gui.log("[广播搜索] 完成")

    # ---------- 广播 (仅IPv4) ----------
    def _start_broadcasters(self):
        for ip_str in self.my_ips:
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.bind((ip_str, 0))
                self.broadcast_sockets.append(sock)
                threading.Thread(target=self._udp_broadcaster_on_sock, args=(sock, ip_str), daemon=True).start()
            except Exception as e:
                self.gui.log(f"[警告] 无法为 {ip_str} 创建广播 socket: {e}")
        try:
            global_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            global_sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            global_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.broadcast_sockets.append(global_sock)
            threading.Thread(target=self._udp_broadcaster_on_sock, args=(global_sock, None), daemon=True).start()
        except Exception as e:
            self.gui.log(f"[警告] 全局广播 socket 创建失败: {e}")

    def _udp_broadcaster_on_sock(self, sock, bind_ip):
        """周期性广播搜索（替代原来的每秒 announce）。

        统一设备发现机制：
          · 启动后前 5 秒：每 0.2 秒发一次搜索（快速发现）
          · 之后：每 20 秒发一次搜索（低成本维持）
          · 接收方收到 {discovery:true} 会立刻回 {reply:true}
        这样"发现"完全依赖"请求→响应"，不再依赖对方是否在广播窗口。
        """
        start_time = time.time()
        while self.running:
            try:
                if not self.pausing_network:
                    # 广播搜索包（带 device_id/mac，接收方回 reply）
                    msg = self._build_msg(discovery=True)
                    if bind_ip:
                        cidr = get_subnet_for_ip(bind_ip)
                        if cidr:
                            try:
                                net = ipaddress.IPv4Network(cidr, strict=False)
                                bcast = str(net.broadcast_address)
                            except Exception:
                                bcast = '255.255.255.255'
                        else:
                            bcast = '255.255.255.255'
                        sock.sendto(msg, (bcast, UDP_PORT))
                    else:
                        sock.sendto(msg, ('255.255.255.255', UDP_PORT))
            except Exception:
                pass
            # 前 5 秒：爆发搜索（0.2s）；之后：常规（20s）
            if time.time() - start_time < BROADCAST_BURST_DURATION:
                time.sleep(BROADCAST_BURST_INTERVAL)
            else:
                time.sleep(AUTO_SCAN_INTERVAL)  # 20 秒
        sock.close()

    # ---------- 节点管理 ----------
    def _build_msg(self, **extra):
        """构造出站 JSON 消息，自动带上 hostname / device_id / mac。"""
        msg = {
            'hostname': self.hostname,
            'device_id': self.device_id,
            'mac': self.mac,
        }
        msg.update(extra)
        return json.dumps(msg).encode('utf-8')

    def _upsert_node(self, remote_ip, msg, source='udp'):
        """按 device_id 去重地插入/更新节点。

        返回 True 表示是**新节点**（此前不存在）。
        逻辑：
          1. 若消息里带 device_id，且本机已有同一 device_id 的条目（IP 不同）
             → 视为同一设备 IP 变化，删除旧 IP 条目
          2. 按 remote_ip 插入/更新
        """
        device_id = msg.get('device_id', '') or ''
        hostname = msg.get('hostname', remote_ip) or remote_ip
        mac = msg.get('mac', '') or ''
        is_new = False
        with self.lock:
            # 1) 同 device_id 但 IP 变了 → 迁移条目
            if device_id:
                for old_ip in list(self.nodes.keys()):
                    if old_ip == remote_ip:
                        continue
                    if self.nodes[old_ip].get('device_id') == device_id:
                        del self.nodes[old_ip]
            # 2) 按 IP upsert
            if remote_ip in self.nodes:
                self.nodes[remote_ip]['hostname'] = hostname
                self.nodes[remote_ip]['device_id'] = device_id
                self.nodes[remote_ip]['mac'] = mac
                self.nodes[remote_ip]['last_seen'] = time.time()
                self.nodes[remote_ip]['heartbeat_fail'] = 0
            else:
                self.nodes[remote_ip] = {
                    'hostname': hostname,
                    'device_id': device_id,
                    'mac': mac,
                    'last_seen': time.time(),
                    'source': source,
                    'heartbeat_fail': 0,
                }
                is_new = True
        return is_new

    def add_manual_node(self, ip, source='manual', hostname=None):
        with self.lock:
            self.nodes[ip] = {
                'hostname': hostname or ip,
                'last_seen': time.time(),
                'source': source,
                'heartbeat_fail': 0
            }
        self.gui.root.after(0, self.gui.refresh_nodes)
        # 手动添加节点时主动向对方发送UDP探测，让对方也发现我们
        self._send_probe_to(ip)
        # 手动添加节点时也检查续传
        threading.Thread(target=self.check_resume_on_online, args=(ip, hostname or ip), daemon=True).start()

    def _send_probe_to(self, ip):
        """向指定IP发送UDP探测包（UDP_PORT + SCAN_PORT），让对方发现我们"""
        try:
            family = socket.AF_INET6 if ':' in ip else socket.AF_INET
            s = socket.socket(family, socket.SOCK_DGRAM)
            s.settimeout(2)
            msg = self._build_msg()
            s.sendto(msg, (ip, UDP_PORT))
            # 也向SCAN_PORT发一份，覆盖扫描端口的监听
            try:
                s.sendto(self._build_msg(scan_reply=True), (ip, SCAN_PORT))
            except Exception:
                pass
            s.close()
            self.gui.log(f"[手动添加] 已向 {ip} 发送探测")
        except Exception as e:
            self.gui.log(f"[手动添加] 向 {ip} 发送探测失败: {e}")

    # ---------- 续传上线提醒 ----------
    def check_resume_on_online(self, ip, hostname):
        """检测到设备上线时，检查是否有对该IP的待续传记录，有则弹窗询问"""
        import glob
        # 查找对该IP的发送方续传记录
        resume_files = []
        # 单文件：resume_sender_{ip}_*.json
        pattern1 = str(RESUME_DIR / f"resume_sender_{ip}_*.json")
        resume_files.extend(glob.glob(pattern1))
        # 文件夹：folder_resume_{ip}_*.json
        pattern2 = str(RESUME_DIR / f"folder_resume_{ip}_*.json")
        resume_files.extend(glob.glob(pattern2))

        if not resume_files:
            return

        # 收集待续传的文件信息
        pending_items = []
        for fpath in resume_files:
            try:
                with open(fpath, 'r') as f:
                    data = json.load(f)
                fname = data.get('filepath', data.get('rel_path', os.path.basename(fpath)))
                offset = data.get('offset', 0)
                total = data.get('total_size', 0)
                pct = (offset / total * 100) if total > 0 else 0
                # 检查源文件是否还存在
                if data.get('filepath') and not os.path.exists(data['filepath']):
                    continue
                if data.get('rel_path'):  # 文件夹续传
                    pending_items.append(f"  📁 {data.get('folder_name','?')}/{fname} ({pct:.0f}%)")
                else:
                    pending_items.append(f"  📄 {os.path.basename(fname)} ({pct:.0f}%)")
            except:
                pass

        if not pending_items:
            return

        msg = f"检测到设备 {hostname} ({ip}) 上线！\n以下文件未完成传输，是否继续上传？\n\n"
        msg += '\n'.join(pending_items[:10])
        if len(pending_items) > 10:
            msg += f"\n  ... 还有 {len(pending_items)-10} 个文件"

        # 在主线程弹窗
        result = [None]
        def ask():
            result[0] = messagebox.askyesno("续传提醒", msg)
        self.gui.root.after(0, ask)
        while result[0] is None:
            try:
                self.gui.root.update_idletasks()
            except:
                break
            time.sleep(0.01)

        if result[0]:
            self.gui.log(f"[续传] 用户确认向 {ip} 续传 {len(resume_files)} 个文件")
            threading.Thread(target=self._do_resume_upload, args=(ip, resume_files), daemon=True).start()
        else:
            self.gui.log(f"[续传] 用户放弃向 {ip} 续传，清理状态文件")
            for fpath in resume_files:
                try:
                    os.unlink(fpath)
                except:
                    pass

    def _do_resume_upload(self, ip, resume_files):
        """执行续传上传"""
        folder_items = {}  # {folder_path: [rel_paths]}
        file_items = []

        for fpath in resume_files:
            try:
                with open(fpath, 'r') as f:
                    data = json.load(f)
                if data.get('rel_path'):  # 文件夹续传
                    folder_path = data.get('filepath', '')
                    if folder_path and os.path.isdir(folder_path):
                        if folder_path not in folder_items:
                            folder_items[folder_path] = set()
                        folder_items[folder_path].add(data.get('rel_path'))
                else:  # 单文件续传
                    filepath = data.get('filepath', '')
                    if filepath and os.path.exists(filepath):
                        file_items.append(filepath)
            except:
                pass

        items = []
        for fp in file_items:
            items.append((fp, False))
        for fp in folder_items:
            if os.path.isdir(fp):
                items.append((fp, True))

        if not items:
            self.gui.log(f"[续传] 没有找到有效的续传文件")
            return

        def on_item_done(path, success):
            self.gui.root.after(0, lambda s=success, pt=path: self.gui.log(f"[续传] {'✓' if s else '✗'} {pt}"))

        self.gui.root.after(0, lambda: self.gui.set_status(f"正在向 {ip} 续传 {len(items)} 个项目..."))
        self.send_files(ip, items, callback=on_item_done)

    # ---------- UDP 监听 (IPv4 & IPv6) ----------
    def udp_listener(self):
        # IPv4
        sock4 = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock4.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock4.bind(('', UDP_PORT))
        # 1 秒超时，让循环能定期检查 self.running（Windows 关闭 socket 不一定唤醒阻塞的 recvfrom）
        sock4.settimeout(1.0)
        self.listen_sockets.append(sock4)
        # IPv6
        try:
            sock6 = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM)
            sock6.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock6.bind(('::', UDP_PORT))
            sock6.settimeout(1.0)
            self.listen_sockets.append(sock6)
        except:
            sock6 = None

        def listen_sock(sock, is_ipv6=False):
            while self.running:
                try:
                    data, addr = sock.recvfrom(1024)
                    msg = json.loads(data.decode('utf-8'))
                    remote_ip = addr[0]
                    if remote_ip in self.my_ips or remote_ip in self.my_ips_v6:
                        continue
                    # 过滤所有本机回环地址和通配地址，绝不可能搜索到自己
                    if remote_ip in ('127.0.0.1', '::1', '0.0.0.0'):
                        continue
                    # 处理"正常下线"通知：立即从设备列表移除（不等待心跳超时）
                    if msg.get('bye'):
                        with self.lock:
                            if remote_ip in self.nodes:
                                del self.nodes[remote_ip]
                                removed = True
                            else:
                                removed = False
                        if removed:
                            self.gui.root.after(0, self.gui.refresh_nodes)
                            self.gui.root.after(0, lambda ip=remote_ip, hn=msg.get('hostname', remote_ip):
                                                self.gui.log(f"[发现] 设备 {hn} ({ip}) 已下线"))
                        continue
                    # 处理心跳包，只更新时间不触发GUI刷新
                    if msg.get('heartbeat'):
                        with self.lock:
                            if remote_ip in self.nodes:
                                self.nodes[remote_ip]['last_seen'] = time.time()
                        reply = self._build_msg(heartbeat=True, ack=True)
                        sock.sendto(reply, (remote_ip, UDP_PORT))
                        continue
                    # 扫描回复或普通回复，收到即添加/更新节点
                    if msg.get('scan_reply') or msg.get('reply'):
                        is_new = self._upsert_node(remote_ip, msg, source='udp')
                        if is_new:
                            self.gui.root.after(0, self.gui.refresh_nodes)
                            hn = msg.get('hostname', remote_ip)
                            threading.Thread(target=self.check_resume_on_online, args=(remote_ip, hn), daemon=True).start()
                        continue
                    # 普通广播：upsert + 回 reply
                    is_new = self._upsert_node(remote_ip, msg, source='udp')
                    if is_new:
                        self.gui.root.after(0, self.gui.refresh_nodes)
                        hn = msg.get('hostname', remote_ip)
                        threading.Thread(target=self.check_resume_on_online, args=(remote_ip, hn), daemon=True).start()
                    if not msg.get('reply'):
                        sock.sendto(self._build_msg(reply=True), (remote_ip, UDP_PORT))
                except socket.timeout:
                    continue
                except OSError:
                    break
                except Exception:
                    pass
            try:
                sock.close()
            except Exception:
                pass

        threading.Thread(target=listen_sock, args=(sock4, False), daemon=True).start()
        if sock6:
            threading.Thread(target=listen_sock, args=(sock6, True), daemon=True).start()

    def clean_nodes(self):
        now = time.time()
        with self.lock:
            to_del = [ip for ip, n in self.nodes.items() if now - n['last_seen'] > NODE_TIMEOUT]
            for ip in to_del:
                del self.nodes[ip]
        self.gui.refresh_nodes()
        if self.running:
            aid = self.gui.root.after(2000, self.clean_nodes)
            try:
                self.gui._after_ids.append(aid)
            except Exception:
                pass

    # ---------- 扫描监听（SCAN_PORT，与设备发现端口隔离） ----------
    def scan_listener(self):
        """在 SCAN_PORT 上监听扫描探测请求，支持 IPv4 + IPv6 双栈，回复到双端口"""
        # IPv4
        try:
            sock4 = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock4.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock4.bind(('', SCAN_PORT))
            sock4.settimeout(1.0)
            self.listen_sockets.append(sock4)
        except Exception as e:
            self.gui.log(f"[警告] 扫描监听 IPv4 端口 {SCAN_PORT} 绑定失败: {e}")
            sock4 = None
        # IPv6
        try:
            sock6 = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM)
            sock6.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock6.bind(('::', SCAN_PORT))
            sock6.settimeout(1.0)
            self.listen_sockets.append(sock6)
        except:
            sock6 = None

        def handle_scan_request(sock, is_ipv6=False):
            while self.running:
                try:
                    data, addr = sock.recvfrom(1024)
                    msg = json.loads(data.decode('utf-8'))
                    remote_ip = addr[0]
                    if remote_ip in self.my_ips or remote_ip in self.my_ips_v6:
                        continue
                    if remote_ip in ('127.0.0.1', '::1', '0.0.0.0'):
                        continue
                    # 回复主机名，同时向 SCAN_PORT 和 UDP_PORT 回复，确保对方能收到
                    reply = self._build_msg(scan_reply=True)
                    for port in (SCAN_PORT, UDP_PORT):
                        try:
                            sock.sendto(reply, (remote_ip, port))
                        except:
                            pass
                except socket.timeout:
                    continue
                except OSError:
                    break
                except Exception:
                    pass
            try:
                sock.close()
            except Exception:
                pass

        if sock4:
            threading.Thread(target=handle_scan_request, args=(sock4, False), daemon=True).start()
        if sock6:
            threading.Thread(target=handle_scan_request, args=(sock6, True), daemon=True).start()

    # ---------- 搜索 ----------
    def scan_subnet(self, network_cidr, callback=None):
        """扫描指定CIDR网段，发现设备（使用 SCAN_PORT 隔离扫描流量）"""
        if not self.running:
            return
        try:
            net = ipaddress.IPv4Network(network_cidr, strict=False)
            hosts = list(net.hosts())
            if not hosts:
                self.gui.log(f"[扫描] {network_cidr} 中没有主机")
                return
            # 过滤掉本机IP，减少无效扫描
            my_ips_set = set(self.my_ips) | set(self.my_ips_v6) | {'127.0.0.1', '::1', '0.0.0.0'}
            hosts = [ip for ip in hosts if str(ip) not in my_ips_set]
            if not hosts:
                self.gui.log(f"[扫描] {network_cidr} 中所有IP都是本机，跳过")
                return
            # 限制扫描IP数量，防止大网段耗尽资源
            if len(hosts) > MAX_SCAN_IPS:
                self.gui.log(f"[扫描] {network_cidr} 过大（{len(hosts)} 个IP），限制扫描前 {MAX_SCAN_IPS} 个")
                hosts = hosts[:MAX_SCAN_IPS]
            self.gui.log(f"[扫描] 开始扫描 {network_cidr}，共 {len(hosts)} 个 IP...")
            found = 0
            scanned = 0
            def scan_one(ip):
                ip_str = str(ip)
                hostname = None
                # 双端口探测：先试 SCAN_PORT，再试 UDP_PORT，任一回复即发现
                for port in (SCAN_PORT, UDP_PORT):
                    try:
                        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                        s.settimeout(SCAN_TIMEOUT)
                        s.sendto(json.dumps({'hostname': self.hostname}).encode(), (ip_str, port))
                        data, _ = s.recvfrom(1024)
                        s.close()
                        reply = json.loads(data.decode('utf-8'))
                        hostname = reply.get('hostname', ip_str)
                        break  # 任一端口收到回复即成功
                    except:
                        try:
                            s.close()
                        except:
                            pass
                if hostname:
                    return (ip_str, hostname)
                return None

            with ThreadPoolExecutor(max_workers=SCAN_THREADS) as executor:
                futures = {executor.submit(scan_one, ip): ip for ip in hosts}
                for future in as_completed(futures):
                    if not self.running:
                        # 程序关闭，取消剩余任务
                        future.cancel()
                        break
                    scanned += 1
                    result = future.result()
                    if result:
                        ip_str, hostname = result
                        # 过滤本机IP，避免搜索到自己
                        if ip_str in self.my_ips or ip_str in self.my_ips_v6:
                            continue
                        if ip_str in ('127.0.0.1', '::1', '0.0.0.0'):
                            continue
                        self.add_manual_node(ip_str, source='scan', hostname=hostname)
                        found += 1
                        if callback:
                            callback(ip_str, hostname)
            if self.running:
                self.gui.log(f"[扫描] {network_cidr} 扫描完成，扫描 {scanned} 个IP，发现 {found} 个设备")
            else:
                self.gui.log(f"[扫描] {network_cidr} 扫描已取消（程序关闭），已发现 {found} 个设备")
        except Exception as e:
            self.gui.log(f"[扫描] 出错: {e}")

    def auto_scan_all_subnets(self, callback=None):
        if self.scanning:
            self.gui.log("[扫描] 扫描已在运行，请稍后")
            return
        self.scanning = True
        self.gui.log("[扫描] 开始自动扫描所有子网...")
        subnets = get_all_subnets()
        if not subnets:
            self.gui.log("[扫描] 未找到任何有效子网")
            self.scanning = False
            return
        self.gui.log(f"[扫描] 将扫描子网: {', '.join(subnets)}")
        for cidr in subnets:
            if not self.running:
                self.gui.log("[扫描] 程序已关闭，停止扫描")
                break
            self.scan_subnet(cidr, callback)
        if self.running:
            self.gui.log("[扫描] 自动扫描完成")
        self.scanning = False

    def start_auto_scan(self):
        if self.auto_scan_enabled:
            return
        self.auto_scan_enabled = True
        self.gui.log("[扫描] 后台自动扫描已开启（每20秒）")
        threading.Thread(target=self._auto_scan_loop, daemon=True).start()

    def stop_auto_scan(self):
        self.auto_scan_enabled = False
        self.gui.log("[扫描] 后台自动扫描已关闭")

    def _auto_scan_loop(self):
        while self.running and self.auto_scan_enabled:
            if not self.pausing_network:  # 传输中暂停扫描和心跳
                self.auto_scan_all_subnets()
                self.heartbeat_check()
            for _ in range(AUTO_SCAN_INTERVAL):
                if not self.auto_scan_enabled:
                    return
                time.sleep(1)

    def heartbeat_check(self):
        """使用 UDP 进行心跳检测，连续3次无回复才判定离线"""
        with self.lock:
            ips = list(self.nodes.keys())
        if not ips:
            return
        HEARTBEAT_MAX_FAIL = 3
        def check(ip):
            try:
                if ':' in ip:
                    s = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM)
                else:
                    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.settimeout(1.0)
                s.sendto(json.dumps({'heartbeat': True}).encode(), (ip, UDP_PORT))
                data, _ = s.recvfrom(1024)  # 等待回复，验证对方在线
                s.close()
                return ip, True
            except:
                return ip, False
        offline_ips = []
        with ThreadPoolExecutor(max_workers=min(16, len(ips))) as executor:
            futures = {executor.submit(check, ip): ip for ip in ips}
            for future in as_completed(futures):
                ip, online = future.result()
                with self.lock:
                    if ip in self.nodes:
                        if online:
                            # 收到回复，重置失败计数
                            self.nodes[ip]['heartbeat_fail'] = 0
                            self.nodes[ip]['last_seen'] = time.time()
                        else:
                            # 无回复，递增失败计数
                            self.nodes[ip]['heartbeat_fail'] = self.nodes[ip].get('heartbeat_fail', 0) + 1
                            if self.nodes[ip]['heartbeat_fail'] >= HEARTBEAT_MAX_FAIL:
                                offline_ips.append(ip)
        if offline_ips:
            with self.lock:
                for ip in offline_ips:
                    if ip in self.nodes:
                        del self.nodes[ip]
            self.gui.log(f"[心跳] {len(offline_ips)} 个设备连续{HEARTBEAT_MAX_FAIL}次心跳无回复，已移除")
            self.gui.root.after(0, self.gui.refresh_nodes)

    def remove_offline_nodes(self):
        with self.lock:
            ips_to_test = list(self.nodes.keys())
        if not ips_to_test:
            return
        self.gui.log("[扫描] 正在检查已有设备在线状态...")
        def test_one(ip):
            try:
                # 使用 UDP 探测，避免触发对方的 TCP handle_receive
                if ':' in ip:
                    s = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM)
                else:
                    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.settimeout(0.5)
                s.sendto(json.dumps({'heartbeat': True}).encode(), (ip, UDP_PORT))
                data, _ = s.recvfrom(1024)
                s.close()
                return (ip, True)
            except:
                return (ip, False)

        with ThreadPoolExecutor(max_workers=min(32, len(ips_to_test))) as executor:
            futures = {executor.submit(test_one, ip): ip for ip in ips_to_test}
            for future in as_completed(futures):
                ip, online = future.result()
                with self.lock:
                    if online:
                        if ip in self.nodes:
                            self.nodes[ip]['last_seen'] = time.time()
                    else:
                        if ip in self.nodes:
                            del self.nodes[ip]
        self.gui.root.after(0, self.gui.refresh_nodes)

    # ---------- TCP 服务器 ----------
    def _optimize_socket(self, sock):
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        sock.settimeout(None)  # 大文件传输不设置超时
        # 启用 TCP keepalive（减少防火墙超时断开）
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 60)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 10)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 3)
        except AttributeError:
            pass  # 部分系统不支持
        # 设置 socket 缓冲区为 64MB（内核可能会限制实际值，但尽量设大）
        for opt in (socket.SO_SNDBUF, socket.SO_RCVBUF):
            for buf_size in (SOCKET_BUFFER_SIZE * 2, SOCKET_BUFFER_SIZE):
                try:
                    sock.setsockopt(socket.SOL_SOCKET, opt, buf_size)
                    break
                except:
                    continue

    def tcp_file_receiver(self):
        # IPv4优先，兼容性更好
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(('0.0.0.0', TCP_PORT))
            sock.listen(50)
            self.gui.log(f"[TCP] 监听端口 {TCP_PORT} 成功")
        except Exception as e:
            self.gui.log(f"[TCP] IPv4绑定失败，尝试IPv6: {e}")
            try:
                sock = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
                sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.bind(('::', TCP_PORT))
                sock.listen(50)
                self.gui.log(f"[TCP] IPv6 监听端口 {TCP_PORT} 成功（双栈）")
            except Exception as e2:
                self.gui.log(f"[TCP] 所有端口绑定失败: {e2}")
                return
        # 加入注册表 + 1 秒超时，让 accept 能被关闭/running 标记唤醒
        self.listen_sockets.append(sock)
        sock.settimeout(1.0)
        # 每30秒最多记录一次连接日志，避免刷屏
        _last_log_time = [0.0]
        while self.running:
            try:
                conn, addr = sock.accept()
                self._optimize_socket(conn)
                # 收到连接时立即设置短超时，验证连接会快速失败
                conn.settimeout(5)
                now = time.time()
                if now - _last_log_time[0] >= 30:
                    self.gui.log(f"[TCP] 收到连接: {addr[0]}:{addr[1]}")
                    _last_log_time[0] = now
                threading.Thread(target=self.handle_receive, args=(conn, addr), daemon=True).start()
            except socket.timeout:
                continue
            except OSError:
                break
            except Exception as e:
                if self.running:
                    self.gui.log(f"[TCP] 接受连接异常: {e}")
        try:
            sock.close()
        except Exception:
            pass

    def handle_receive(self, conn, addr):
        try:
            # 设置读超时15秒（低网络下第一个字节可能需要更长时间）
            conn.settimeout(15)
            flag_byte = recv_exact(conn, 1)
            flag = flag_byte[0]
            # 读取到有效标志，说明是真正的文件传输请求，取消超时
            conn.settimeout(None)
            if flag == FLAG_FILE:
                self._receive_single_file(conn, addr)
            elif flag == FLAG_FOLDER:
                self._receive_folder(conn, addr)
            elif flag == 0x02:  # FLAG_QUERY_OFFSET：续传偏移量查询
                self._handle_query_offset(conn, addr)
        except socket.timeout:
            pass  # 验证连接超时，无声关闭
        except Exception as e:
            error_msg = str(e)
            # 只记录有意义的错误，忽略正常的连接关闭和验证连接错误
            if error_msg and error_msg not in ('连接中断', '', 'Connection aborted', 'timed out'):
                self.gui.log(f"[接收] 连接异常: {error_msg}")
        finally:
            try:
                conn.close()
            except:
                pass

    def _handle_query_offset(self, conn, addr):
        """处理续传偏移量查询：A问B"这个文件你收到多少了？"，B回复已收字节数（8字节）"""
        try:
            # 接收总大小
            raw_total = recv_exact(conn, 8)
            total_size = struct.unpack('!Q', raw_total)[0]
            # 接收文件名长度和文件名
            raw_plen = recv_exact(conn, 4)
            plen = struct.unpack('!I', raw_plen)[0]
            fname_enc = recv_exact(conn, plen)
            filename = fname_enc.decode('utf-8')
            # 查找本地是否有该文件的部分接收
            local_offset = 0
            save_dir = SAVE_DIR
            save_path = (save_dir / filename).resolve()
            if save_path.exists():
                local_offset = save_path.stat().st_size
                if local_offset > total_size:
                    local_offset = 0
            conn.sendall(struct.pack('!Q', local_offset))
            self.gui.log(f"[接收] 续传查询: {filename}，本地已收 {human_size(local_offset)}")
        except Exception as e:
            self.gui.log(f"[接收] 续传查询处理异常: {e}")
            conn.sendall(struct.pack('!Q', 0))

    # ---------- 动态缓冲区 ----------
    def _adjust_buffer_size(self, current_speed):
        global BUFFER_SIZE
        if current_speed < 1024 * 1024:        # < 1 MB/s
            new_size = 256 * 1024
        elif current_speed < 5 * 1024 * 1024:  # 1-5 MB/s
            new_size = 512 * 1024
        elif current_speed < 20 * 1024 * 1024: # 5-20 MB/s
            new_size = 1 * 1024 * 1024
        elif current_speed < 50 * 1024 * 1024: # 20-50 MB/s
            new_size = 2 * 1024 * 1024
        else:                                   # > 50 MB/s
            new_size = 4 * 1024 * 1024
        new_size = max(64*1024, min(new_size, MAX_BUFFER_SIZE))
        if new_size != BUFFER_SIZE:
            BUFFER_SIZE = new_size
            self.gui.log(f"[优化] 缓冲区调整为 {human_size(BUFFER_SIZE)}")

    def _recv_file_data_with_hash(self, conn, dest_path, file_size, offset=0,
                                   progress_cb=None, hash_obj=None, decompressor=None):
        """
        接收文件数据，写入 dest_path，同时更新 hash_obj（如果提供）。
        如果 decompressor 提供，则先解压再写入，并更新 hash。
        支持偏移量（续传），此时 dest_path 必须存在且大小等于 offset，
        并且 hash_obj 必须已经包含了该部分数据的哈希。
        返回 (成功标志, 接收字节数)
        """
        received = offset
        buf = bytearray(1024 * 1024)  # 接收缓冲区固定 1MB，避免大文件时内存膨胀
        start_time = time.time()
        last_update_time = start_time
        last_update_bytes = received
        # 低网络静默超时检测（仅当速度<512KB/s 时启用）
        last_activity_time = start_time
        RECV_ACTIVITY_TIMEOUT = 60
        LOW_NETWORK_THRESHOLD = 512 * 1024
        recv_speed = float('inf')
        # 保存原始超时设置，大文件传输不设超时
        original_timeout = conn.gettimeout()
        conn.settimeout(None)
        mode = 'ab' if offset > 0 else 'wb'
        # 文件打开重试（借鉴下载器设计）
        file_open_retries = 3
        file_open_wait = 1
        try:
            for file_attempt in range(file_open_retries):
                try:
                    with open(dest_path, mode, buffering=0) as f:
                        # 批量写入缓冲区：累计到 1MB 或最后一波才一次写入，减少磁盘IO次数
                        WRITE_BATCH_SIZE = 1024 * 1024  # 1MB
                        write_buf = bytearray()
                        while received < file_size:
                            # 检测接收静默超时（仅低网络下启用）
                            now = time.time()
                            elapsed = now - start_time
                            current_speed = (received - offset) / elapsed if elapsed > 0 else float('inf')
                            if current_speed < LOW_NETWORK_THRESHOLD and now - last_activity_time > RECV_ACTIVITY_TIMEOUT:
                                raise ConnectionError(f"接收静默超时 {RECV_ACTIVITY_TIMEOUT}秒，触发续传（低网络）")
                            chunk_size = min(BUFFER_SIZE, file_size - received)
                            view = memoryview(buf)[:chunk_size]
                            n = conn.recv_into(view, chunk_size)
                            if n == 0:
                                raise ConnectionError("接收中断")
                            last_activity_time = time.time()
                            data = view[:n]
                            if decompressor:
                                decompressed = decompressor.decompress(data)
                                if decompressed:
                                    write_buf.extend(decompressed)
                                    if hash_obj:
                                        hash_obj.update(decompressed)
                                    # 压缩数据到达 WRITE_BATCH_SIZE 或最后一波时刷入磁盘
                                    if len(write_buf) >= WRITE_BATCH_SIZE or received + n >= file_size:
                                        f.write(write_buf)
                                        write_buf = bytearray()
                            else:
                                write_buf.extend(data)
                                if hash_obj:
                                    hash_obj.update(data)
                                # 达到 WRITE_BATCH_SIZE 或最后一波时刷入磁盘
                                if len(write_buf) >= WRITE_BATCH_SIZE or received + n >= file_size:
                                    f.write(write_buf)
                                    write_buf = bytearray()
                            received += n
                            # 速度平滑采样更新进度（借鉴下载器）
                            now = time.time()
                            if now - last_update_time >= 0.5 or (received - last_update_bytes) >= 1024 * 1024:
                                if progress_cb:
                                    progress_cb(received, file_size, start_time)
                                last_update_time = now
                                last_update_bytes = received
                        if decompressor:
                            tail = decompressor.flush()
                            if tail:
                                write_buf.extend(tail)
                                if hash_obj:
                                    hash_obj.update(tail)
                        # 最后一次刷入剩余数据
                        if write_buf:
                            f.write(write_buf)
                            write_buf = bytearray()
                    return True, received
                except (IOError, PermissionError, OSError) as e:
                    if file_attempt < file_open_retries - 1:
                        self.gui.log(f"[接收] 打开文件失败（{file_attempt+1}/{file_open_retries}），{file_open_wait}秒后重试: {e}")
                        time.sleep(file_open_wait)
                    else:
                        raise  # 最后一次尝试失败，抛出异常
        except Exception as e:
            self.gui.log(f"[接收] 数据接收异常: {e}")
            return False, received
        finally:
            conn.settimeout(original_timeout)
        return False, received  # 不应到达这里，仅为防御性编程

    def _recv_and_reply_hash(self, conn, filepath, success, offset=0, total_size=None):
        remote_hash = recv_exact(conn, 64).decode('utf-8')
        if success:
            local_hash = compute_sha256(filepath, offset=offset, length=total_size)
            if local_hash == remote_hash:
                conn.sendall(b'MATCH     ')
                return True
            else:
                conn.sendall(b'MISMATCH  ')
                return False
        else:
            conn.sendall(b'MISMATCH  ')
            return False

    # ---------- 接收文件（支持续传） ----------
    def _receive_single_file(self, conn, addr):
        raw_len = recv_exact(conn, 4)
        fname_len = struct.unpack('!I', raw_len)[0]
        fname_enc = recv_exact(conn, fname_len)
        filename = fname_enc.decode('utf-8')
        flags = recv_exact(conn, 1)[0]
        compressed = bool(flags & FLAG_COMPRESS)
        resume = bool(flags & FLAG_RESUME)
        raw_size = recv_exact(conn, 8)
        file_size = struct.unpack('!Q', raw_size)[0]
        offset = 0
        if resume:
            raw_offset = recv_exact(conn, 8)
            offset = struct.unpack('!Q', raw_offset)[0]

        save_dir = SAVE_DIR
        save_dir.mkdir(exist_ok=True)
        save_path = (save_dir / filename).resolve()
        if not str(save_path).startswith(str(save_dir.resolve())):
            self.gui.log(f"[安全] 拒绝非法文件名: {filename}")
            conn.sendall(b'NO')
            return

        if save_path.exists():
            current_size = os.path.getsize(save_path)
            if resume and current_size == offset:
                pass
            else:
                result = [None]
                def ask():
                    result[0] = messagebox.askyesno("文件已存在", f"文件 {filename} 已存在，是否覆盖？")
                self.gui.root.after(0, ask)
                while result[0] is None:
                    self.gui.root.update_idletasks()
                    time.sleep(0.01)
                if not result[0]:
                    conn.sendall(b'NO')
                    return
                save_path.unlink()
                offset = 0
        else:
            offset = 0

        conn.sendall(b'OK')
        self.gui.log(f"[接收] 文件: {filename} ({human_size(file_size)}){' [续传]' if resume else ''}{' [压缩]' if compressed else ''} 来自 {addr[0]}")
        self.gui.set_status(f"正在接收 {filename} ...")
        start_time = time.time()

        sha = hashlib.sha256()
        if offset > 0 and save_path.exists():
            try:
                with open(save_path, 'rb', buffering=BUFFER_SIZE) as f_existing:
                    while True:
                        chunk = f_existing.read(BUFFER_SIZE)
                        if not chunk:
                            break
                        sha.update(chunk)
                self.gui.log(f"[接收] 已读取现有 {human_size(offset)} 数据用于哈希计算")
            except Exception as e:
                self.gui.log(f"[接收] 续传哈希初始化失败，改为全量接收: {e}")
                offset = 0
                save_path.unlink()
                sha = hashlib.sha256()

        # 保存接收方初始续传状态（从当前偏移量开始）
        save_resume_state(addr[0], str(save_path), offset, file_size, 0, role='recv')
        last_progress_save = [0.0]  # 上次实时保存进度的时间
        _tracker = SpeedTracker()    # 瞬时速度追踪器

        def progress_cb(received, total, start, _tracker=_tracker):
            # 瞬时速度（滑动窗口 2 秒），比累计平均更贴合实际
            speed = _tracker.update(received)
            remaining = (total - received) / speed if speed > 0 else 0
            percent = (received / total) * 100
            speed_str = format_speed(speed)
            remain_str = format_time(remaining)
            self.gui.root.after(0, lambda p=percent, s=speed_str, r=remain_str: self.gui.update_recv_progress(p, s, r))
            self.gui.root.after(0, lambda: self.gui.set_status(f"接收 {filename}: {human_size(received)} / {human_size(total)}  {speed_str}  剩余 {remain_str}"))
            # 每5秒或每10%实时保存进度
            now = time.time()
            if now - last_progress_save[0] >= 5 or (received - offset) % max(1, total // 10) < 1024*1024:
                save_resume_state(addr[0], str(save_path), received, file_size, 0, role='recv')
                last_progress_save[0] = now

        success = False
        received_bytes = 0
        if compressed:
            if offset != 0:
                self.gui.log("[警告] 压缩文件续传不支持，重新全量接收")
                offset = 0
                if save_path.exists():
                    save_path.unlink()
                sha = hashlib.sha256()
            decompressor = zlib.decompressobj()
            success, received_bytes = self._recv_file_data_with_hash(
                conn, str(save_path), file_size, 0,
                progress_cb, sha, decompressor
            )
        else:
            if offset == 0:
                temp_path = save_path.with_suffix('.tmp')
            else:
                temp_path = save_path
            success, received_bytes = self._recv_file_data_with_hash(
                conn, str(temp_path), file_size, offset,
                progress_cb, sha, None
            )
            if success and offset == 0:
                os.rename(temp_path, save_path)

        local_hash = sha.hexdigest() if success else None
        remote_hash = recv_exact(conn, 64).decode('utf-8')

        if success and local_hash == remote_hash:
            conn.sendall(b'MATCH     ')
            elapsed = time.time() - start_time
            speed = os.path.getsize(save_path) / elapsed if elapsed > 0 else 0
            self.gui.log(f"[接收] 文件 {filename} 校验通过 ✓  ({format_speed(speed)})")
            self.gui.root.after(0, lambda: messagebox.showinfo("接收完成", f"文件 {filename} 接收成功！"))
            self.gui.root.after(200, lambda: os.startfile(os.path.dirname(save_path)))
            # 删除接收方续传记录
            delete_resume_state(addr[0], str(save_path), role='recv')
        else:
            conn.sendall(b'MISMATCH  ')
            self.gui.log(f"[接收] 文件 {filename} 校验失败 ✗")
            if success:
                self.gui.root.after(0, lambda: messagebox.showwarning("接收失败", f"文件 {filename} 校验失败！"))
            delete_resume_state(addr[0], str(save_path), role='recv')

        self.gui.root.after(0, lambda: self.gui.update_recv_progress(0))
        self.gui.set_status("就绪")

    def _get_folder_manifest(self, folder_path):
        """生成文件夹清单：返回列表 [(rel_path, size, mtime), ...]"""
        manifest = []
        for root, dirs, files in os.walk(folder_path):
            for fname in files:
                full = os.path.join(root, fname)
                rel = os.path.relpath(full, folder_path)
                size = os.path.getsize(full)
                mtime = os.path.getmtime(full)
                manifest.append((rel, size, mtime))
        return manifest

    def _receive_folder(self, conn, addr):
        # 1. 读取文件夹头
        raw_len = recv_exact(conn, 4)
        dname_len = struct.unpack('!I', raw_len)[0]
        dname_enc = recv_exact(conn, dname_len)
        folder_name = dname_enc.decode('utf-8')
        raw_count = recv_exact(conn, 4)
        file_count = struct.unpack('!I', raw_count)[0]
        raw_total = recv_exact(conn, 8)
        total_bytes = struct.unpack('!Q', raw_total)[0]

        save_dir = SAVE_DIR / folder_name
        if save_dir.exists():
            result = [None]
            def ask():
                result[0] = messagebox.askyesno("文件夹已存在", f"文件夹 {folder_name} 已存在，是否覆盖？")
            self.gui.root.after(0, ask)
            # 等待主线程处理完消息框
            while result[0] is None:
                self.gui.root.update_idletasks()
                time.sleep(0.01)
            if not result[0]:
                conn.sendall(b'NO')
                return
        conn.sendall(b'OK')
        save_dir.mkdir(parents=True, exist_ok=True)
        base_path = save_dir.resolve()

        self.gui.log(f"[接收] 文件夹: {folder_name} ({file_count} 个文件, {human_size(total_bytes)}) 来自 {addr[0]}")
        self.gui.set_status(f"接收文件夹 {folder_name} ...")

        # 2. 接收清单
        manifest = []  # [(rel_path, size, mtime), ...]
        for _ in range(file_count):
            plen = struct.unpack('!I', recv_exact(conn, 4))[0]
            path_enc = recv_exact(conn, plen)
            rel_path = path_enc.decode('utf-8')
            size = struct.unpack('!Q', recv_exact(conn, 8))[0]
            mtime = struct.unpack('!d', recv_exact(conn, 8))[0]
            manifest.append((rel_path, size, mtime))

        # 3. 决定每个文件的处理方式
        actions = []  # 每个元素为 (action, offset)，action为'skip','full','resume'
        for rel_path, size, mtime in manifest:
            dest_path = (save_dir / rel_path).resolve()
            if not str(dest_path).startswith(str(base_path)):
                self.gui.log(f"[安全] 拒绝非法路径: {rel_path}")
                conn.sendall(b'\x00')  # 清单处理失败
                return
            if dest_path.exists():
                local_size = dest_path.stat().st_size
                local_mtime = dest_path.stat().st_mtime
                # 如果大小和修改时间完全匹配，跳过
                if local_size == size and abs(local_mtime - mtime) < 0.1:
                    actions.append(('skip', 0))
                elif local_size < size:
                    # 大小小于清单，可续传（偏移量=本地大小）
                    actions.append(('resume', local_size))
                else:
                    # 大小大于清单或修改时间不同，覆盖重传
                    actions.append(('full', 0))
            else:
                actions.append(('full', 0))

        # 4. 回复清单处理成功
        conn.sendall(b'\x01')

        # 5. 发送每个文件的指令
        for act in actions:
            if act[0] == 'skip':
                conn.sendall(b'\x00')  # skip
            elif act[0] == 'full':
                conn.sendall(b'\x01')  # full
            else:  # resume
                conn.sendall(b'\x02')  # resume
                conn.sendall(struct.pack('!Q', act[1]))  # 偏移量

        # 6. 逐个接收文件（支持重试，最多3次）
        received_global = 0
        failed = 0
        start_time = time.time()
        MAX_RETRIES = 3
        retry_counts = {}

        for idx in range(file_count):
            rel_path, size, mtime = manifest[idx]
            act = actions[idx]
            if act[0] == 'skip':
                self.gui.log(f"[接收]   ✓ {rel_path} (已存在，跳过)")
                continue

            file_ok = False
            for attempt in range(MAX_RETRIES):
                if attempt > 0:
                    self.gui.log(f"[接收] 重试 ({attempt}/{MAX_RETRIES}): {rel_path}")

                raw_plen = recv_exact(conn, 4)
                plen = struct.unpack('!I', raw_plen)[0]
                path_enc = recv_exact(conn, plen)
                recv_rel_path = path_enc.decode('utf-8')
                flags = recv_exact(conn, 1)[0]
                compressed = bool(flags & FLAG_COMPRESS)
                resume = bool(flags & FLAG_RESUME)
                raw_fsize = recv_exact(conn, 8)
                file_size = struct.unpack('!Q', raw_fsize)[0]
                cur_offset = 0
                if resume:
                    raw_offset = recv_exact(conn, 8)
                    cur_offset = struct.unpack('!Q', raw_offset)[0]

                dest_path = (save_dir / recv_rel_path).resolve()
                if not str(dest_path).startswith(str(base_path)):
                    self.gui.log(f"[安全] 拒绝非法路径: {recv_rel_path}")
                    conn.sendall(b'NO')
                    return
                dest_path.parent.mkdir(parents=True, exist_ok=True)

                if act[0] == 'full' and dest_path.exists():
                    dest_path.unlink()
                elif act[0] == 'resume':
                    if not dest_path.exists():
                        cur_offset = 0
                    else:
                        current_size = dest_path.stat().st_size
                        if current_size != cur_offset:
                            self.gui.log(f"[接收] 续传偏移不匹配，从头发送 {recv_rel_path}")
                            cur_offset = 0
                            dest_path.unlink()

                self.gui.log(f"[接收]   ({idx+1}/{file_count}) {recv_rel_path} ({human_size(file_size)})" +
                            (f" [续传 {human_size(cur_offset)}]" if resume else "") +
                            (f" [压缩]" if compressed else ""))
                conn.sendall(b'OK')

                sha = hashlib.sha256()
                if cur_offset > 0 and dest_path.exists():
                    try:
                        with open(dest_path, 'rb', buffering=BUFFER_SIZE) as f_existing:
                            while True:
                                chunk = f_existing.read(BUFFER_SIZE)
                                if not chunk:
                                    break
                                sha.update(chunk)
                    except Exception as e:
                        self.gui.log(f"[接收] 续传哈希初始化失败，重传 {recv_rel_path}: {e}")
                        cur_offset = 0
                        dest_path.unlink()
                        sha = hashlib.sha256()

                # 保存接收方文件夹续传初始状态
                save_folder_resume_state(addr[0], folder_name, recv_rel_path, cur_offset, file_size, 0)

                # 修正：progress_cb 的 received 是「当前文件的累计接收量」（含续传起点 cur_offset），
                # 全局进度应 = 已完成文件总字节（received_global） + 当前文件本次实际接收量（received - cur_offset）。
                # 之前直接 += received 会导致重复累加（曾出现 908% 的 bug）。
                _file_off = cur_offset if resume else 0
                _base = received_global
                _tracker = SpeedTracker()   # 瞬时速度追踪器
                def make_progress_cb():
                    _rel_path = recv_rel_path  # 闭包捕获当前文件路径
                    _size = file_size
                    def progress_cb(received, total, start, _tracker=_tracker):
                        # 当前文件本次已收字节（去掉续传起点）
                        file_received = max(0, received - _file_off)
                        # 全局累计 = 已完成文件总字节 + 当前文件本次已收
                        total_received = _base + file_received
                        # 瞬时速度（用全局累计追踪，反映当前网络状况）
                        speed = _tracker.update(total_received)
                        speed_str = format_speed(speed)

                        # ---------- 接收进度：当前**文件**的进度 ----------
                        file_percent = min(100.0, (file_received / _size) * 100) if _size > 0 else 0.0
                        file_remain = (_size - file_received) / speed if speed > 0 else 0
                        self.gui.root.after(0, lambda p=file_percent, s=speed_str, r=format_time(file_remain):
                                            self.gui.update_recv_progress(p, s, r))

                        # ---------- 状态栏：全局字节进度 ----------
                        global_remain = (total_bytes - total_received) / speed if speed > 0 else 0
                        self.gui.root.after(0, lambda tr=total_received, s=speed_str, r=format_time(global_remain):
                                            self.gui.set_status(f"接收文件夹 {folder_name}: {human_size(tr)} / {human_size(total_bytes)}  {s}  剩余 {r}"))

                        # 实时保存每个文件的进度
                        save_folder_resume_state(addr[0], folder_name, _rel_path, received, total, 0)
                    return progress_cb

                success = False
                if compressed:
                    if cur_offset != 0:
                        self.gui.log("[警告] 压缩文件续传不支持，重新全量接收")
                        cur_offset = 0
                        if dest_path.exists():
                            dest_path.unlink()
                        sha = hashlib.sha256()
                    decompressor = zlib.decompressobj()
                    success, _ = self._recv_file_data_with_hash(
                        conn, str(dest_path), file_size, 0,
                        make_progress_cb(), sha, decompressor
                    )
                else:
                    if cur_offset == 0:
                        temp_path = dest_path.with_suffix('.tmp')
                    else:
                        temp_path = dest_path
                    success, _ = self._recv_file_data_with_hash(
                        conn, str(temp_path), file_size, cur_offset,
                        make_progress_cb(), sha, None
                    )
                    if success and cur_offset == 0:
                        os.rename(temp_path, dest_path)

                remote_hash = recv_exact(conn, 64).decode('utf-8')
                if success and sha.hexdigest() == remote_hash:
                    conn.sendall(b'MATCH     ')
                    self.gui.log(f"[接收]   ✓ {recv_rel_path}")
                    file_ok = True
                    # 删除该文件的续传状态
                    delete_folder_resume_state(addr[0], folder_name, recv_rel_path)
                    break  # 成功，跳出重试循环
                else:
                    conn.sendall(b'MISMATCH  ')
                    self.gui.log(f"[接收]   ✗ {recv_rel_path} (第{attempt+1}次)")
                    # 清理失败的文件，以便重试时重新接收
                    if dest_path.exists():
                        dest_path.unlink()

            if not file_ok:
                self.gui.log(f"[接收]   ✗ {rel_path} 重试{MAX_RETRIES}次均失败，放弃")
                failed += 1
            else:
                # 只有成功的文件才计入"已完成总量"（与发送端 sent_counter 对齐），
                # 否则失败文件会污染进度基准和最终平均速度统计。
                received_global += size

        self.gui.root.after(0, lambda: self.gui.update_recv_progress(0))
        elapsed = time.time() - start_time
        speed = received_global / elapsed if elapsed > 0 else 0
        if failed == 0:
            self.gui.log(f"[接收] 文件夹 {folder_name} 接收完成，全部文件校验通过 ✓  ({format_speed(speed)})")
            self.gui.root.after(0, lambda: messagebox.showinfo("接收完成", f"文件夹 {folder_name} 接收成功！"))
            self.gui.root.after(200, lambda: os.startfile(save_dir))
        else:
            self.gui.log(f"[接收] 文件夹 {folder_name} 接收完成，{failed} 个文件失败 ✗")
            self.gui.root.after(0, lambda: messagebox.showwarning("接收完成", f"文件夹 {folder_name} 有 {failed} 个文件失败"))
            self.gui.root.after(200, lambda: os.startfile(save_dir))
        self.gui.set_status("就绪")

    # ============== 发送端 ==============
    def _send_folder(self, sock, folder_path, target_ip):
        folder_name = os.path.basename(folder_path)
        manifest = self._get_folder_manifest(folder_path)  # [(rel, size, mtime), ...]
        file_count = len(manifest)
        total_bytes = sum(item[1] for item in manifest)

        self.gui.log(f"[发送] 文件夹: {folder_name} ({file_count} 个文件, {human_size(total_bytes)})")
        
        # 1. 发送文件夹头
        dname_enc = folder_name.encode('utf-8')
        header = struct.pack('!B', FLAG_FOLDER)
        header += struct.pack('!I', len(dname_enc)) + dname_enc
        header += struct.pack('!I', file_count) + struct.pack('!Q', total_bytes)
        sock.sendall(header)
        ack = recv_exact(sock, 2)
        if ack == b'NO':
            raise ConnectionError("对方拒绝接收文件夹")
        if ack != b'OK':
            raise ConnectionError("对方拒绝接收文件夹")

        # 2. 发送清单（路径、大小、修改时间）
        for rel_path, size, mtime in manifest:
            path_enc = rel_path.encode('utf-8')
            sock.sendall(struct.pack('!I', len(path_enc)) + path_enc)
            sock.sendall(struct.pack('!Q', size))
            sock.sendall(struct.pack('!d', mtime))
        # 等待接收端确认清单处理完毕
        ack = recv_exact(sock, 1)
        if ack != b'\x01':
            self.gui.log("[发送] 接收端清单处理失败")
            return

        # 3. 接收每个文件的续传指令（0=skip, 1=full, 2=resume，若是resume则后跟8字节偏移量）
        actions = []  # 每个元素为 (action, offset)，action为'skip','full','resume'
        for _ in range(file_count):
            act_byte = recv_exact(sock, 1)[0]
            if act_byte == 2:
                offset = struct.unpack('!Q', recv_exact(sock, 8))[0]
                actions.append(('resume', offset))
            elif act_byte == 1:
                actions.append(('full', 0))
            else:
                actions.append(('skip', 0))

        # 4. 预计算需要发送的文件的哈希（仅对 full 和 resume 的文件）
        self.gui.log("[发送] 预计算文件哈希...")
        file_hashes = {}
        with ThreadPoolExecutor(max_workers=min(8, file_count)) as executor:
            future_to_idx = {}
            for idx, (rel_path, size, mtime) in enumerate(manifest):
                if actions[idx][0] != 'skip':
                    full_path = os.path.join(folder_path, rel_path)
                    future = executor.submit(compute_sha256, full_path)
                    future_to_idx[future] = idx
            for future in as_completed(future_to_idx):
                idx = future_to_idx[future]
                try:
                    file_hashes[idx] = future.result()
                except Exception as e:
                    self.gui.log(f"[发送] 计算哈希失败 {manifest[idx][0]}: {e}")
                    file_hashes[idx] = None

        # 5. 逐个发送文件（与接收端配合：每个文件最多重试3次，每次重试重新发送文件头）
        self.gui.set_status(f"发送文件夹 {folder_name} ...")
        sent_global = 0
        start_time = time.time()
        MAX_RETRIES = 3
        failed_items = []
        # sent_counter[0] 表示「已成功完成的文件总字节数」。
        # 注意：当前文件的实时进度不累加到这里，而是每次回调时用
        # 「已完成文件总和 + 当前文件相对进度」现算 —— 因为 progress_cb
        # 的 sent 参数是**当前文件的累计发送量**（含续传起点），
        # 直接 += 会导致重复累加（曾出现 908% 的 bug）。
        sent_counter = [0]

        for idx in range(file_count):
            rel_path, size, mtime = manifest[idx]
            full_path = os.path.join(folder_path, rel_path)
            act, offset = actions[idx]
            if act == 'skip':
                self.gui.log(f"[发送]   ✓ {rel_path} (已存在，跳过)")
                # 删除续传状态（文件已完整存在）
                delete_folder_resume_state(target_ip, folder_name, rel_path)
                continue

            # 加载已有续传状态，取最大偏移量作为续传点
            folder_state = load_folder_resume_state(target_ip, folder_name, rel_path, None)
            if folder_state and folder_state['total_size'] == size and folder_state['offset'] > 0:
                state_offset = folder_state['offset']
            else:
                state_offset = 0
            # 如果接收方报告了更大的偏移量，用接收方的
            if act == 'resume' and offset > state_offset:
                effective_offset = offset
            else:
                effective_offset = state_offset
            if effective_offset > 0 and effective_offset < size:
                self.gui.log(f"[发送] 文件夹续传 {rel_path} 从 {human_size(effective_offset)} 处继续")
                act = 'resume'
                offset = effective_offset

            # 保存发送方文件夹续传状态
            save_folder_resume_state(target_ip, folder_name, rel_path, offset, size, mtime)

            # 进入本文件发送前的全局累计（已完成文件总字节）
            file_base = sent_counter[0]
            # 本文件的续传起点（若为 resume 则从 offset 处开始，否则 0）
            file_start_offset = offset if act == 'resume' else 0

            file_ok = False
            for attempt in range(MAX_RETRIES):
                if attempt > 0:
                    self.gui.log(f"[发送] 重试 ({attempt+1}/{MAX_RETRIES}): {rel_path}")

                self.gui.log(f"[发送]   ({idx+1}/{file_count}) {rel_path} ({human_size(size)})" +
                            (f" [续传 {human_size(offset)}]" if act=='resume' else ""))

                # 判断是否压缩（仅对全量发送的文件压缩，续传不支持压缩）
                compress = False
                if act == 'full':
                    compress = is_text_file(full_path)
                flags = FLAG_COMPRESS if compress else 0
                if compress:
                    try:
                        with open(full_path, 'rb') as f:
                            data = f.read()
                        compressed = zlib.compress(data, level=6)
                        send_size = len(compressed)
                        temp_data = compressed
                        self.gui.log(f"[发送] 压缩后大小: {human_size(send_size)}")
                    except Exception as e:
                        self.gui.log(f"[发送] 压缩失败: {e}")
                        compress = False
                        temp_data = None
                        send_size = size
                else:
                    send_size = size
                    temp_data = None

                # 发送单个文件头（重试时重新发送，接收端会等待）
                path_enc = rel_path.encode('utf-8')
                file_header = struct.pack('!I', len(path_enc)) + path_enc
                file_flags = flags
                if act == 'resume' and offset > 0:
                    file_flags |= FLAG_RESUME
                file_header += struct.pack('!B', file_flags) + struct.pack('!Q', send_size)
                if file_flags & FLAG_RESUME:
                    file_header += struct.pack('!Q', offset)
                sock.sendall(file_header)
                ack = recv_exact(sock, 2)
                if ack != b'OK':
                    self.gui.log("[发送] 对方拒绝接收文件")
                    return

                # 发送数据
                # progress_cb 的 sent 参数是「当前文件的累计发送量」（含续传起点 offset），
                # 因此：
                #   本地文件进度 = file_base + (sent - file_start_offset)
                #   全局字节增量 = 本次 sent 与上次上报值的差
                # 用闭包默认参数固化可变值，避免循环延迟绑定陷阱。
                _last_sent = [file_start_offset]   # 上次上报的 sent（用于算增量）
                _speed_tracker = SpeedTracker()    # 瞬时速度追踪器（滑动窗口 2 秒）

                def progress_cb(sent, total, start,
                                _base=file_base,
                                _file_off=file_start_offset,
                                _rel_path=rel_path,
                                _size=size,
                                _mtime=mtime,
                                _last=_last_sent,
                                _tracker=_speed_tracker):
                    # 当前文件本次已传字节（去掉续传起点）
                    file_sent = max(0, sent - _file_off)
                    # 全局累计 = 已完成文件总字节 + 当前文件本次已传
                    total_sent = _base + file_sent
                    # 瞬时速度（用全局累计追踪，反映当前网络状况）
                    speed = _tracker.update(total_sent)
                    speed_str = format_speed(speed)

                    # ---------- 当前发送：当前**文件**的进度 ----------
                    file_percent = min(100.0, (file_sent / _size) * 100) if _size > 0 else 0.0
                    file_remain = (_size - file_sent) / speed if speed > 0 else 0
                    self.gui.root.after(0, lambda p=file_percent, s=speed_str, r=format_time(file_remain):
                                        self.gui.update_send_progress(p, s, r))

                    # ---------- 状态栏：全局字节进度 ----------
                    global_remain = (total_bytes - total_sent) / speed if speed > 0 else 0
                    self.gui.root.after(0, lambda ts=total_sent, s=speed_str, r=format_time(global_remain):
                                        self.gui.set_status(f"发送文件夹 {folder_name}: {human_size(ts)} / {human_size(total_bytes)}  {s}  剩余 {r}"))

                    # 全局字节增量（线程安全）
                    delta = sent - _last[0]
                    if delta > 0:
                        _last[0] = sent
                        self.gui.add_global_sent_bytes(delta)
                    # 实时保存每个文件的进度
                    save_folder_resume_state(target_ip, folder_name, _rel_path, sent, _size, _mtime)

                try:
                    if compress and temp_data is not None:
                        sock.sendall(temp_data)
                        progress_cb(send_size, send_size, start_time)
                    else:
                        self._send_file_data_fast(sock, full_path, send_size, offset if act=='resume' else 0, progress_cb)

                    # 发送原始文件的哈希
                    file_hash = file_hashes.get(idx, '')
                    if file_hash is None:
                        file_hash = compute_sha256(full_path)
                    sock.sendall(file_hash.encode('utf-8'))
                    result = recv_exact(sock, 10).strip()
                    if result == b'MATCH':
                        self.gui.log(f"[发送]   ✓ {rel_path} 校验通过")
                        sent_global += size
                        sent_counter[0] += size   # 当前文件完成，累加到「已完成总量」
                        file_ok = True
                        # 删除该文件的续传状态
                        delete_folder_resume_state(target_ip, folder_name, rel_path)
                        break  # 成功，跳出重试循环
                    else:
                        self.gui.log(f"[发送]   ✗ {rel_path} 校验失败 (第{attempt+1}次)")
                except Exception as e:
                    self.gui.log(f"[发送]   ✗ {rel_path} 发送异常: {e}")

            if not file_ok:
                self.gui.log(f"[发送]   ✗ {rel_path} 重试{MAX_RETRIES}次均失败，放弃")
                failed_items.append(idx)

        elapsed = time.time() - start_time
        speed = sent_global / elapsed if elapsed > 0 else 0
        if len(failed_items) == 0:
            self.gui.log(f"[发送] 文件夹 {folder_name} 发送完成，全部文件校验通过 ✓  ({format_speed(speed)})")
            self.gui.root.after(0, lambda: messagebox.showinfo("发送完成", f"文件夹 {folder_name} 发送完成！"))
        else:
            self.gui.log(f"[发送] 文件夹 {folder_name} 发送完成，{len(failed_items)} 个文件失败 ✗")
            self.gui.root.after(0, lambda: messagebox.showwarning("发送完成", f"文件夹 {folder_name} 有 {len(failed_items)} 个文件失败"))
        self.gui.set_status("就绪")
        # 清理重试计数
        if hasattr(self, '_folder_retry_counts'):
            self._folder_retry_counts.clear()
        
    def _send_file_data_fast(self, sock, filepath, file_size, offset=0, progress_cb=None):
        # 使用sendfile零拷贝（最高性能）
        if SENDFILE_SUPPORT:
            try:
                start_time = time.time()
                with open(filepath, 'rb') as f:
                    if offset:
                        f.seek(offset)
                    sent = offset
                    while sent < file_size:
                        # 使用 offset=None 让内核维护文件偏移，减少一次系统调用
                        n = os.sendfile(sock.fileno(), f.fileno(), None, min(4*1024*1024, file_size - sent))
                        if n == 0:
                            break
                        sent += n
                        if progress_cb:
                            progress_cb(sent, file_size, start_time)
                    return
            except Exception as e:
                self.gui.log(f"[发送] sendfile 失败，回退到普通发送: {e}")
        # 普通发送：单缓冲 + 自适应块大小
        #
        # 注意（重要修复）：
        #   1. 之前这里有一个“预读下一块”的逻辑，会在循环末尾额外调用一次
        #      f.readinto(...) 推进文件指针，但下一轮循环又从新位置读，
        #      导致每次循环末尾的 next_read 字节被丢弃、从未发送，
        #      最终接收方 SHA-256 校验必然失败。此处已删除该预读。
        #   2. 之前 progress_cb 第 3 个参数传入的是 time.time()（当前时间），
        #      但回调约定该参数为“开始时间”，导致速度计算 elapsed≈0。
        #      此处改为在函数开头记录 start_time 并每次传入。
        sent = offset
        start_time = time.time()
        with open(filepath, 'rb', buffering=BUFFER_SIZE) as f:
            if offset:
                f.seek(offset)
            # 自适应块大小 + 速度采样
            adaptive_chunk_size = 256 * 1024  # 初始 256KB
            speed_samples = []
            sample_interval = 1.0
            last_sample_time = time.time()
            last_downloaded = sent
            # 低网络静默超时检测（仅当最近速度<512KB/s 时启用，避免高速网络误触发）
            low_network_activity_time = time.time()
            ACTIVITY_TIMEOUT = 30
            LOW_NETWORK_THRESHOLD = 512 * 1024
            buf = bytearray(BUFFER_SIZE)
            avg_speed = float('inf')  # 初始化为无穷，避免首次循环误触发超时
            while sent < file_size:
                # 每次使用当前自适应块大小与剩余大小的较小值
                chunk_size = min(adaptive_chunk_size, file_size - sent, BUFFER_SIZE)
                n = f.readinto(memoryview(buf)[:chunk_size])
                if not n:
                    break
                # 发送当前块
                sock.sendall(memoryview(buf)[:n])
                sent += n
                # 自适应调整块大小（借鉴下载器速度采样）
                now = time.time()
                elapsed = now - last_sample_time
                if elapsed >= sample_interval:
                    speed = (sent - last_downloaded) / elapsed
                    speed_samples.append(speed)
                    if len(speed_samples) > 5:
                        speed_samples.pop(0)
                    avg_speed = sum(speed_samples) / len(speed_samples)
                    # 仅低网络下启用静默超时检测（速度<512KB/s 且超过阈值）
                    if avg_speed < LOW_NETWORK_THRESHOLD:
                        if now - low_network_activity_time > ACTIVITY_TIMEOUT:
                            raise ConnectionError(f"发送静默超时 {ACTIVITY_TIMEOUT}秒，触发重试（低网络）")
                    else:
                        low_network_activity_time = now  # 高速网络下重置计时
                    if avg_speed < 128 * 1024:  # 极低网络：16KB块
                        adaptive_chunk_size = 16 * 1024
                    elif avg_speed < 512 * 1024:
                        adaptive_chunk_size = 64 * 1024
                    elif avg_speed < 1024 * 1024:
                        adaptive_chunk_size = 128 * 1024
                    elif avg_speed < 5 * 1024 * 1024:
                        adaptive_chunk_size = 256 * 1024
                    elif avg_speed < 20 * 1024 * 1024:
                        adaptive_chunk_size = 512 * 1024
                    elif avg_speed < 50 * 1024 * 1024:
                        adaptive_chunk_size = 1 * 1024 * 1024
                    else:
                        adaptive_chunk_size = 2 * 1024 * 1024
                    last_downloaded = sent
                    last_sample_time = now
                    low_network_activity_time = now  # 有数据发送时重置计时
                if progress_cb:
                    progress_cb(sent, file_size, start_time)

    def _send_single_file(self, sock, filepath, target_ip):
        total_size = os.path.getsize(filepath)
        filename = os.path.basename(filepath)
        compress = is_text_file(filepath)
        flags = FLAG_COMPRESS if compress else 0

        # 检查双方续传状态，取最小偏移量作为续传点
        sender_state = load_resume_state(target_ip, filepath, role='sender')
        offset = 0
        if sender_state and sender_state['total_size'] == total_size and sender_state['offset'] > 0 and sender_state['offset'] < total_size:
            sender_offset = sender_state['offset']
        else:
            sender_offset = 0
        # 接收方续传状态
        recv_state = load_resume_state(target_ip, filepath, role='recv')
        if recv_state and recv_state['total_size'] == total_size:
            recv_offset = recv_state['offset']
        else:
            recv_offset = 0
        # 取双方都确认的最小偏移量（发送方已发=接收方已收的公共部分）
        offset = min(sender_offset, recv_offset) if sender_offset > 0 and recv_offset > 0 else max(sender_offset, recv_offset)
        if offset > 0 and offset < total_size:
            flags |= FLAG_RESUME
            self.gui.log(f"[发送] 续传文件 {filename} 从 {human_size(offset)} 处继续（发送方记录={human_size(sender_offset)}，接收方记录={human_size(recv_offset)}）")

        if compress:
            # 压缩仅从头开始，不支持续传压缩（因为压缩数据不可分块续传）
            if offset > 0:
                self.gui.log(f"[发送] 压缩文件不支持续传，从头发送")
                offset = 0
                flags &= ~FLAG_RESUME
            self.gui.log(f"[发送] 文件: {filename} ({human_size(total_size)}) [将压缩]")
            try:
                with open(filepath, 'rb') as f:
                    data = f.read()
                compressed = zlib.compress(data, level=6)
                file_size = len(compressed)
                temp_data = compressed
                self.gui.log(f"[发送] 压缩后大小: {human_size(file_size)}")
            except Exception as e:
                self.gui.log(f"[发送] 压缩失败，发送原始文件: {e}")
                compress = False
                flags = 0
                temp_data = None
                file_size = total_size
        else:
            self.gui.log(f"[发送] 文件: {filename} ({human_size(total_size)}){' [续传]' if offset>0 else ''}")
            temp_data = None
            file_size = total_size

        # 保存发送方状态（初始或续传偏移量）
        mtime = os.path.getmtime(filepath)
        save_resume_state(target_ip, filepath, offset, total_size, mtime, role='sender')

        # ====== 在线协商续传偏移量：向对方查询该文件已收到多少字节 ======
        if not compress:  # 压缩文件不支持续传，跳过协商
            try:
                # 发送 QUERY_OFFSET 请求（标志0x02 + 总大小 + 文件名）
                fname_enc_query = filename.encode('utf-8')
                sock.sendall(struct.pack('!B', 0x02))  # FLAG_QUERY_OFFSET
                sock.sendall(struct.pack('!Q', total_size))
                sock.sendall(struct.pack('!I', len(fname_enc_query)) + fname_enc_query)
                # 接收对方回复的偏移量
                sock.settimeout(5.0)
                resp = recv_exact(sock, 8)
                sock.settimeout(None)
                remote_offset = struct.unpack('!Q', resp)[0]
                if remote_offset > 0 and remote_offset < total_size:
                    # 取本地状态和远程偏移量的较大值（双方都确认的部分）
                    if remote_offset > offset:
                        offset = remote_offset
                        flags |= FLAG_RESUME
                        self.gui.log(f"[发送] 协商续传: 对方已收到 {human_size(remote_offset)}，从该位置继续")
                    else:
                        self.gui.log(f"[发送] 协商续传: 本地已有更大偏移量 {human_size(offset)}，继续本地续传")
                elif remote_offset == 0:
                    self.gui.log(f"[发送] 协商续传: 对方没有该文件记录，从头发送")
                    # 关键修复：对方无该文件，必须清零 offset 并清除 FLAG_RESUME，
                    # 否则会从本地的旧 offset 位置发送数据，接收方从 0 写入，
                    # 导致最终 SHA-256 校验必然失败
                    offset = 0
                    flags &= ~FLAG_RESUME
                else:
                    self.gui.log(f"[发送] 协商续传: 对方文件不一致，从头发送")
                    offset = 0
                    flags &= ~FLAG_RESUME
                # 更新发送方状态
                save_resume_state(target_ip, filepath, offset, total_size, mtime, role='sender')
            except Exception as e:
                self.gui.log(f"[发送] 协商续传偏移量失败: {e}，使用本地续传状态")
                sock.settimeout(None)

        fname_enc = filename.encode('utf-8')
        header = struct.pack('!B', FLAG_FILE)
        header += struct.pack('!I', len(fname_enc)) + fname_enc
        header += struct.pack('!B', flags)
        header += struct.pack('!Q', file_size)
        if flags & FLAG_RESUME:
            header += struct.pack('!Q', offset)
        sock.sendall(header)
        ack = recv_exact(sock, 2)
        if ack == b'NO':
            raise ConnectionError("对方拒绝接收文件")
        if ack != b'OK':
            raise ConnectionError("对方拒绝接收文件")

        self.gui.set_status(f"发送 {filename} ...")
        start_time = time.time()
        last_progress_save = [0.0]  # 上次实时保存进度的时间
        _last_sent = [offset]        # 上次上报的 sent（用于算全局增量）
        _tracker = SpeedTracker()    # 瞬时速度追踪器
        def progress_cb(sent, total, start, _last=_last_sent, _tracker=_tracker):
            # 瞬时速度（滑动窗口）；用 sent 直接追踪（含续传起点也 OK）
            speed = _tracker.update(sent)
            remaining = (total - sent) / speed if speed > 0 else 0
            percent = (sent / total) * 100
            speed_str = format_speed(speed)
            remain_str = format_time(remaining)
            self.gui.root.after(0, lambda p=percent, s=speed_str, r=remain_str: self.gui.update_send_progress(p, s, r))
            self.gui.root.after(0, lambda: self.gui.set_status(f"发送 {filename}: {human_size(sent)} / {human_size(total)}  {speed_str}  剩余 {remain_str}"))
            # 全局字节增量（线程安全）
            delta = sent - _last[0]
            if delta > 0:
                _last[0] = sent
                self.gui.add_global_sent_bytes(delta)
            # 每5秒或每10%实时保存进度
            now = time.time()
            if now - last_progress_save[0] >= 5 or (sent - offset) % max(1, total // 10) < 1024*1024:
                save_resume_state(target_ip, filepath, sent, total_size, mtime, role='sender')
                last_progress_save[0] = now

        if compress and temp_data is not None:
            sock.sendall(temp_data)
            progress_cb(file_size, file_size, start_time)
        else:
            self._send_file_data_fast(sock, filepath, file_size, offset, progress_cb)

        # 计算原始文件的哈希（压缩时也需原始哈希）
        file_hash = compute_sha256(filepath)
        sock.sendall(file_hash.encode('utf-8'))
        result = recv_exact(sock, 10).strip()
        elapsed = time.time() - start_time
        speed = file_size / elapsed if elapsed > 0 else 0
        if result == b'MATCH':
            self.gui.log(f"[发送] 文件 {filename} 对方校验通过 ✓  ({format_speed(speed)})")
            self.gui.root.after(0, lambda: messagebox.showinfo("发送成功", f"{filename} 发送成功！"))
            # 删除双方续传记录
            delete_resume_state(target_ip, filepath, role='sender')
            delete_resume_state(target_ip, filepath, role='recv')
        else:
            self.gui.log(f"[发送] 文件 {filename} 对方校验失败 ✗")
            self.gui.root.after(0, lambda: messagebox.showwarning("发送警告", f"{filename} 对方校验失败"))
        self.gui.set_status("就绪")

    # ---------- 发送端入口（支持多文件/文件夹串行发送，带回调） ----------
    def send_files(self, target_ip, items, max_retries=3, callback=None):
        """
        向目标IP发送多个项目（文件或文件夹），每个项目独立连接，串行执行。
        items: list of (path, is_folder)
        callback: 每完成一个项目后调用 callback(path, success)
        """
        if not items:
            return
        self.pausing_network = True  # 传输开始，暂停广播/扫描/心跳
        self.gui.log(f"[发送] 向 {target_ip} 串行发送 {len(items)} 个项目")
        for idx, (path, is_folder) in enumerate(items):
            try:
                success = self._send_item_with_retry(target_ip, path, is_folder, max_retries)
                if callback:
                    callback(path, success)
            except Exception as e:
                self.gui.log(f"[发送] 项目 {path} 发送失败: {e}")
                if callback:
                    callback(path, False)
        self.pausing_network = False  # 传输结束，恢复广播/扫描/心跳

    def _send_item_with_retry(self, target_ip, path, is_folder, max_retries):
        """带重试的单个项目发送，返回成功标志。max_retries<=0 时无限重试"""
        retry = 0
        infinite_retry = (max_retries <= 0)
        while infinite_retry or retry < max_retries:
            sock = None
            try:
                # 检查对方是否离线（心跳检测已移除的节点不再重试）
                with self.lock:
                    if target_ip not in self.nodes and infinite_retry:
                        self.gui.log(f"[发送] 设备 {target_ip} 已离线，停止重试")
                        return False
                family = socket.AF_INET6 if ':' in target_ip else socket.AF_INET
                sock = socket.socket(family, socket.SOCK_STREAM)
                # 低网络下连接超时适当延长，但不要过长
                sock.settimeout(30)  # 连接超时30秒（低网络适应性）
                sock.connect((target_ip, TCP_PORT))
                sock.settimeout(None)  # 连接成功之后，大文件传输不设置超时
                self._optimize_socket(sock)
                if is_folder:
                    self._send_folder(sock, path, target_ip)
                else:
                    self._send_single_file(sock, path, target_ip)
                self.gui.log(f"[发送] 项目 {path} 发送成功")
                return True
            except Exception as e:
                retry += 1
                if infinite_retry:
                    self.gui.log(f"[发送] 项目 {path} 发送失败 (第{retry}次尝试，持续重试中): {e}")
                else:
                    self.gui.log(f"[发送] 项目 {path} 发送失败 (尝试 {retry}/{max_retries}): {e}")
                if not infinite_retry and retry >= max_retries:
                    self.gui.log(f"[发送] 项目 {path} 最终失败，放弃")
                    return False
                # 指数退避：2^retry 秒，最长 64 秒
                backoff = min(2 ** retry, 64)
                self.gui.log(f"[发送] 等待 {backoff} 秒后重试...")
                time.sleep(backoff)
            finally:
                if sock:
                    try:
                        sock.close()
                    except:
                        pass
        return False

# ================= 图形界面 =================
class P2PApp:
    def __init__(self):
        # 优先使用带拖拽支持的 Tk 子类；未装 tkinterdnd2 时回退到普通 Tk
        if DND_AVAILABLE:
            try:
                self.root = TkinterDnD.Tk()
            except Exception:
                self.root = tk.Tk()
        else:
            self.root = tk.Tk()
        self.root.title("文件互传 V15.2 - 支持拖拽发送")
        self.root.geometry("1200x700")
        self.root.resizable(True, True)

        self.send_items = []
        self.selected_ips = set()
        self.total_tasks = 0
        self.finished_tasks = 0
        self._items_lock = threading.Lock()
        # 用于记录 Tk after() 返回的定时器 ID，退出时批量取消
        self._after_ids = []
        # 发送进行中标志（防止重复点"极速发送"导致文件发多遍）
        self._sending = False

        # ===== 全局进度（字节级） =====
        # global_total_bytes: 本次发送的总字节数 = 单 IP 项目总字节 × 目标 IP 数
        # global_sent_bytes:  所有 IP 线程已发送字节之和（受 _global_progress_lock 保护）
        # 语义：跨设备、跨文件的"整体完成度"，与"当前发送"（当前文件）互补。
        self.global_total_bytes = 0
        self.global_sent_bytes = 0
        self._global_progress_lock = threading.Lock()
        # UI 更新节流：上次刷新全局进度条的时间戳
        self._last_global_ui_update = 0.0
        # IPC 服务器运行标记（on_close 时置 False，让监听线程退出）
        self._ipc_running = True

        self._build_ui()

        # 加载图标（多重尝试，兼容 exe 打包 / 脚本运行 / 多网卡场景）
        self._apply_icon()

    def _apply_icon(self):
        r"""为窗口设置图标。

        关键：**把 ico 复制到纯 ASCII 路径后再设置**。
        Windows 下 Tk 的 iconbitmap() 底层调用 Win32 LoadImage，
        它把 Python str 用**系统 ANSI 代码页**编码为字节传给 API。
        当路径含中文（如 C:Users...文件互传电脑端icon.ico）时，
        ANSI 编码可能损坏路径 -> LoadImage 找不到文件 -> 静默失败（返回默认图标）。
        Tk 不会抛异常，所以现象是"代码执行了但图标还是羽毛"。

        解决：把 ico 复制到 %TEMP%p2p_file_transfer.ico（纯 ASCII），
        用这个临时副本设置图标。TEMP 通常都是纯 ASCII 路径。
        """
        import shutil
        import tempfile

        # 1) 收集候选路径
        candidates = []
        meipass = getattr(sys, '_MEIPASS', None)
        if meipass:
            candidates.append(os.path.join(meipass, 'icon.ico'))
        if getattr(sys, 'frozen', False):
            candidates.append(os.path.join(os.path.dirname(sys.executable), 'icon.ico'))
        else:
            candidates.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'icon.ico'))
        candidates.append(os.path.join(os.getcwd(), 'icon.ico'))

        src = None
        for p in candidates:
            try:
                if p and os.path.isfile(p):
                    src = p
                    break
            except Exception:
                continue

        if not src:
            return

        # 2) 复制到纯 ASCII 临时路径
        target = os.path.join(tempfile.gettempdir(), "p2p_file_transfer.ico")
        try:
            if os.path.abspath(src).lower() != os.path.abspath(target).lower():
                shutil.copyfile(src, target)
            chosen = target
        except Exception:
            chosen = src

        # 3) 更新 idle 任务，确保窗口已创建
        try:
            self.root.update_idletasks()
        except Exception:
            pass

        # 4) 多重方式尝试设置
        ok = False
        try:
            self.root.iconbitmap(default=chosen)
            ok = True
        except Exception:
            pass
        if not ok:
            try:
                self.root.iconbitmap(chosen)
                ok = True
            except Exception:
                pass
        if not ok:
            try:
                self.root.wm_iconbitmap(chosen)
                ok = True
            except Exception:
                pass
        if not ok:
            try:
                self.root.tk.call('wm', 'iconbitmap', self.root._w, chosen)
                ok = True
            except Exception:
                pass

        # 静默处理：图标加载成功与否不影响功能，不记录日志

        self.load_config()
        self.node = Node(self)
        self.node.start()

        self.node.start_auto_scan()
        self.auto_scan_btn.config(text="后台扫描: 开")

        self.log(f"本机 IPv4: {', '.join(self.node.my_ips)}，设备名: {self.node.hostname}")
        if self.node.my_ips_v6:
            self.log(f"本机 IPv6: {', '.join(self.node.my_ips_v6)}")
        self.log("提示: 点击『自动搜索』快速发现同网段设备，可开启『后台扫描』保持更新")
        self.log("新功能: 断点续传(单文件) | IPv6支持 | 动态缓冲区 | zlib压缩")
        if DND_AVAILABLE:
            self.log("提示: 可将文件/文件夹直接拖到『待发送项目』列表")
        else:
            self.log("提示: 安装 tkinterdnd2 可启用拖拽功能（pip install tkinterdnd2）")

    def _build_ui(self):
        # 显示当前保存路径（置于最上方）
        self.save_dir_label = ttk.Label(self.root, text=f"保存位置: {SAVE_DIR}", font=('', 9))
        self.save_dir_label.pack(anchor='w', padx=10, pady=(10, 5))

        # ---- 左右并排的主容器 ----
        main_h = ttk.Frame(self.root)
        main_h.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        # ===== 左侧：设备列表 =====
        left_frame = ttk.LabelFrame(main_h, text="在线设备（可多选）", padding=5)
        left_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 5))

        # 设备操作按钮行
        btn_frame_left = ttk.Frame(left_frame)
        btn_frame_left.pack(fill=tk.X, pady=(0, 5))
        ttk.Button(btn_frame_left, text="自动搜索", command=self.auto_search).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frame_left, text="手动添加", command=self.manual_add_ip).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frame_left, text="刷新在线", command=self.rescan).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frame_left, text="设置设备名", command=self.choose_device_name).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frame_left, text="设置保存路径", command=self.choose_save_dir).pack(side=tk.LEFT, padx=2)
        self.auto_scan_btn = ttk.Button(btn_frame_left, text="后台扫描: 关", command=self.toggle_auto_scan)
        self.auto_scan_btn.pack(side=tk.LEFT, padx=2)

        # 设备列表（带滚动条）
        list_frame = ttk.Frame(left_frame)
        list_frame.pack(fill=tk.BOTH, expand=True)
        scrollbar = ttk.Scrollbar(list_frame)
        self.node_listbox = tk.Listbox(list_frame, height=6, selectmode=tk.MULTIPLE,
                                    exportselection=False, yscrollcommand=scrollbar.set)
        scrollbar.config(command=self.node_listbox.yview)
        self.node_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.node_listbox.bind('<<ListboxSelect>>', self.on_node_select)

        # ===== 右侧：发送列表 =====
        right_frame = ttk.LabelFrame(main_h, text="待发送项目（文件/文件夹）", padding=5)
        right_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=(5, 0))

        # 发送列表（带滚动条）
        list_frame2 = ttk.Frame(right_frame)
        list_frame2.pack(fill=tk.BOTH, expand=True)
        self.item_listbox = tk.Listbox(list_frame2, height=6, selectmode=tk.SINGLE, exportselection=False)
        self.item_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        item_scroll = ttk.Scrollbar(list_frame2, command=self.item_listbox.yview)
        item_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.item_listbox.config(yscrollcommand=item_scroll.set)

        # 注册拖拽目标：把文件/文件夹从资源管理器拖到列表区域即可加入待发送。
        if DND_AVAILABLE:
            try:
                for widget in (self.item_listbox, list_frame2, right_frame):
                    widget.drop_target_register(DND_FILES)
                    widget.dnd_bind('<<Drop>>', self._on_drop_files)
            except Exception as e:
                self.log(f"[拖拽] 注册失败: {e}")

        # 发送操作按钮行
        btn_frame = ttk.Frame(right_frame)
        btn_frame.pack(side=tk.BOTTOM, fill=tk.X, pady=(5, 0))
        ttk.Button(btn_frame, text="添加文件", command=self.add_files).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="添加文件夹", command=self.add_folder).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="上移", command=self.move_up).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="下移", command=self.move_down).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="清空列表", command=self.clear_items).pack(side=tk.LEFT, padx=5)
        self.send_btn = ttk.Button(btn_frame, text="极速发送", command=self.send_items_action)
        self.send_btn.pack(side=tk.RIGHT, padx=5)

        # ---- 进度条（位于主框架下方） ----
        prog_frame = ttk.LabelFrame(self.root, text="传输进度", padding=10)
        prog_frame.pack(fill=tk.X, padx=10, pady=5)
        ttk.Label(prog_frame, text="当前发送:").grid(row=0, column=0, sticky='w')
        self.send_progress = ttk.Progressbar(prog_frame, length=300, mode='determinate')
        self.send_progress.grid(row=0, column=1, padx=5, pady=2, sticky='ew')
        self.send_info_label = ttk.Label(prog_frame, text="0.0%")
        self.send_info_label.grid(row=0, column=2, padx=5, sticky='w')

        ttk.Label(prog_frame, text="接收进度:").grid(row=1, column=0, sticky='w')
        self.recv_progress = ttk.Progressbar(prog_frame, length=300, mode='determinate')
        self.recv_progress.grid(row=1, column=1, padx=5, pady=2, sticky='ew')
        self.recv_info_label = ttk.Label(prog_frame, text="0.0%")
        self.recv_info_label.grid(row=1, column=2, padx=5, sticky='w')

        ttk.Label(prog_frame, text="全局任务:").grid(row=2, column=0, sticky='w')
        self.global_progress = ttk.Progressbar(prog_frame, length=300, mode='determinate')
        self.global_progress.grid(row=2, column=1, padx=5, pady=2, sticky='ew')
        self.global_info_label = ttk.Label(prog_frame, text="0.0%")
        self.global_info_label.grid(row=2, column=2, padx=5, sticky='w')
        prog_frame.columnconfigure(1, weight=1)

        # ---- 日志 ----
        log_frame = ttk.LabelFrame(self.root, text="事件日志", padding=10)
        log_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        self.log_text = tk.Text(log_frame, height=8, state='disabled', wrap='word')
        log_scroll = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        self.log_text.config(yscrollcommand=log_scroll.set)
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        log_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        # ---- 状态栏 ----
        self.status = ttk.Label(self.root, text="就绪", relief=tk.SUNKEN, anchor='w')
        self.status.pack(fill=tk.X, side=tk.BOTTOM)

    # ---------- 队列管理 ----------
    def move_up(self):
        with self._items_lock:
            sel = self.item_listbox.curselection()
            if not sel or sel[0] == 0:
                return
            idx = sel[0]
            self.send_items[idx], self.send_items[idx-1] = self.send_items[idx-1], self.send_items[idx]
            self._refresh_item_listbox()
            self.item_listbox.selection_set(idx-1)

    def move_down(self):
        with self._items_lock:
            sel = self.item_listbox.curselection()
            if not sel or sel[0] >= len(self.send_items)-1:
                return
            idx = sel[0]
            self.send_items[idx], self.send_items[idx+1] = self.send_items[idx+1], self.send_items[idx]
            self._refresh_item_listbox()
            self.item_listbox.selection_set(idx+1)

    def _refresh_item_listbox(self):
        with self._items_lock:
            items_copy = list(self.send_items)
        self.item_listbox.delete(0, tk.END)
        for path, is_folder in items_copy:
            label = f"[文件夹] {os.path.basename(path)}" if is_folder else f"[文件] {os.path.basename(path)}"
            self.item_listbox.insert(tk.END, label)

    def _on_drop_files(self, event):
        """处理从资源管理器拖拽到待发送列表的文件/文件夹。

        tkinterdnd2 的 event.data 是 Tcl 列表格式，含空格的路径会用 { } 包裹，
        因此用 root.tk.splitlist 解析（不要自己 split）。
        """
        try:
            raw_paths = self.root.tk.splitlist(event.data)
        except Exception:
            raw_paths = [event.data]

        added = 0
        skipped = 0
        for p in raw_paths:
            if not p:
                continue
            # 去掉可能的 {} 包裹与首尾空白
            p = str(p).strip()
            if p.startswith('{') and p.endswith('}'):
                p = p[1:-1]
            p = os.path.normpath(p)
            if os.path.isdir(p):
                with self._items_lock:
                    self.send_items.append((p, True))
                added += 1
            elif os.path.isfile(p):
                with self._items_lock:
                    self.send_items.append((p, False))
                added += 1
            else:
                skipped += 1

        if added > 0:
            self._refresh_item_listbox()
            msg = f"[拖拽] 已添加 {added} 个项目到待发送列表"
            if skipped:
                msg += f"（{skipped} 个路径不存在，已忽略）"
            self.log(msg)
        elif skipped:
            self.log(f"[拖拽] 未添加任何项目（{skipped} 个路径无效）")

    # ---------- 搜索相关 ----------
    def auto_search(self):
        """广播搜索：直接发广播包，等待设备回复，比逐IP扫描快得多"""
        self.log("[搜索] 发送广播搜索...")
        threading.Thread(target=self.node.broadcast_discovery, daemon=True).start()

    def toggle_auto_scan(self):
        if self.node.auto_scan_enabled:
            self.node.stop_auto_scan()
            self.auto_scan_btn.config(text="后台扫描: 关")
        else:
            self.node.start_auto_scan()
            self.auto_scan_btn.config(text="后台扫描: 开")



    def scan_network_dialog(self):
        cidr = simpledialog.askstring("扫描网段", "输入网段（如 192.168.1.0/24 或 10.0.0.0/16）：\n留空则自动扫描所有子网")
        if cidr is None:
            return
        if cidr.strip() == "":
            self.auto_search()
        else:
            self.log(f"[用户] 启动网段扫描: {cidr}")
            threading.Thread(target=self.node.scan_subnet, args=(cidr,), daemon=True).start()

    def on_node_select(self, event):
        selected_indices = self.node_listbox.curselection()
        new_selection = set()
        for i in selected_indices:
            text = self.node_listbox.get(i)
            match = re.search(r'\(([^)]+)\)\s*$', text)
            if match:
                new_selection.add(match.group(1))
        self.selected_ips = new_selection
        self.log(f"已选择设备: {', '.join(self.selected_ips) if self.selected_ips else '无'}")

    def refresh_nodes(self):
        previous_ips = self.selected_ips.copy()
        self.node_listbox.delete(0, tk.END)
        with self.node.lock:
            items = sorted(self.node.nodes.items(), key=lambda x: x[1]['last_seen'], reverse=True)
        for ip, node in items:
            display = f"{node['hostname']} ({ip})"
            self.node_listbox.insert(tk.END, display)
        new_selection = set()
        for i in range(self.node_listbox.size()):
            text = self.node_listbox.get(i)
            match = re.search(r'\(([^)]+)\)\s*$', text)
            if match and match.group(1) in previous_ips:
                self.node_listbox.selection_set(i)
                new_selection.add(match.group(1))
        self.selected_ips = new_selection

    def manual_add_ip(self):
        ip = simpledialog.askstring("手动添加设备", "请输入对方 IP 地址（支持 IPv4 或 IPv6）：\n程序将向该IP发送探测包，对方收到后会自动添加本机")
        if ip:
            ip = ip.strip()
            # 简单验证
            try:
                socket.inet_pton(socket.AF_INET, ip)
            except:
                try:
                    socket.inet_pton(socket.AF_INET6, ip)
                except:
                    messagebox.showerror("错误", "IP 地址格式不正确")
                    return
            self.node.add_manual_node(ip, source='manual', hostname=ip)
            self.log(f"手动添加设备: {ip}，已发送双向探测包")

    def rescan(self):
        self.log("[扫描] 正在检查现有设备的在线状态...")
        threading.Thread(target=self.node.remove_offline_nodes, daemon=True).start()

    def log(self, msg):
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_text.config(state='normal')
        self.log_text.insert(tk.END, f"[{timestamp}] {msg}\n")
        self.log_text.see(tk.END)
        self.log_text.config(state='disabled')
        logging.info(msg)

    def set_status(self, msg):
        self.status.config(text=msg)

    # ---------- 进度更新 ----------
    def update_send_progress(self, percent, speed_str=None, remain_str=None):
        # 防御性夹紧：任何调用方传越界值都夹到 0~100，避免出现 908% 这类显示
        try:
            percent = max(0.0, min(100.0, float(percent)))
        except (TypeError, ValueError):
            percent = 0.0
        self.send_progress['value'] = percent
        if speed_str is not None and remain_str is not None:
            self.send_info_label.config(text=f"{percent:.1f}% ({speed_str}, {remain_str})")
        else:
            self.send_info_label.config(text=f"{percent:.1f}%")

    def update_recv_progress(self, percent, speed_str=None, remain_str=None):
        try:
            percent = max(0.0, min(100.0, float(percent)))
        except (TypeError, ValueError):
            percent = 0.0
        self.recv_progress['value'] = percent
        if speed_str is not None and remain_str is not None:
            self.recv_info_label.config(text=f"{percent:.1f}% ({speed_str}, {remain_str})")
        else:
            self.recv_info_label.config(text=f"{percent:.1f}%")

    def increment_global_progress(self):
        """（旧接口）按"完成项目数"推进全局进度。保留以兼容，仅用于项目结束时的兜底。"""
        self.finished_tasks += 1
        if self.total_tasks > 0:
            percent = (self.finished_tasks / self.total_tasks) * 100
            self.global_progress['value'] = percent
            self.global_info_label.config(text=f"{percent:.1f}%")
        else:
            self.global_info_label.config(text="0.0%")

    # ---------- 全局字节进度 ----------

    def add_global_sent_bytes(self, delta):
        """线程安全地累加"已发送字节"，并按需刷新全局进度条。

        调用点：所有发送线程的 progress_cb 里，传入"本次新增的字节数"（增量）。
        注意 delta 必须是**增量**而不是累计值，否则会重复累加（曾出现 908% bug）。
        """
        if delta <= 0:
            return
        with self._global_progress_lock:
            self.global_sent_bytes += delta
            sent = self.global_sent_bytes
            total = self.global_total_bytes

        # UI 节流：最快每 100ms 刷一次（避免每个 chunk 都刷导致卡顿）
        now = time.time()
        if now - self._last_global_ui_update < 0.1 and sent < total:
            return
        self._last_global_ui_update = now

        percent = min(100.0, (sent / total) * 100) if total > 0 else 0.0
        self.root.after(0, lambda p=percent, s=sent, t=total: self._update_global_progress_ui(p, s, t))

    def _update_global_progress_ui(self, percent, sent, total):
        try:
            percent = max(0.0, min(100.0, float(percent)))
        except (TypeError, ValueError):
            percent = 0.0
        try:
            self.global_progress['value'] = percent
            if total > 0:
                self.global_info_label.config(
                    text=f"{percent:.1f}% ({human_size(sent)} / {human_size(total)})"
                )
            else:
                self.global_info_label.config(text=f"{percent:.1f}%")
            # 每 5% 变化记一次日志（便于诊断进度是否在动）
            bucket = int(percent // 5)
            last_bucket = getattr(self, '_global_log_bucket', -1)
            if bucket != last_bucket:
                self._global_log_bucket = bucket
                if bucket % 2 == 0:  # 每 10% 记一次，避免刷屏
                    self.log(f"[进度] 全局: {percent:.1f}%  ({human_size(sent)} / {human_size(total)})")
        except Exception:
            pass

    def _reset_global_progress(self, total_bytes):
        """开始新一轮发送前重置全局进度状态。"""
        with self._global_progress_lock:
            self.global_total_bytes = total_bytes
            self.global_sent_bytes = 0
        self._last_global_ui_update = 0.0
        self.root.after(0, lambda: self._update_global_progress_ui(0.0, 0, total_bytes))

    # ---------- 文件选择 ----------
    def add_files(self):
        files = filedialog.askopenfilenames(title="选择文件")
        with self._items_lock:
            for f in files:
                self.send_items.append((f, False))
        self._refresh_item_listbox()

    def add_folder(self):
        folder = filedialog.askdirectory(title="选择文件夹")
        if folder:
            with self._items_lock:
                self.send_items.append((folder, True))
            self._refresh_item_listbox()

    def clear_items(self):
        with self._items_lock:
            self.send_items.clear()
        self._refresh_item_listbox()

    def send_items_action(self):
        # 防重：正在发送中则忽略（避免用户连点导致同一批文件发多遍）
        if self._sending:
            self.log("[发送] 正在发送中，请等待当前任务完成")
            return
        if not self.selected_ips:
            messagebox.showwarning("未选择设备", "请先在设备列表中选中至少一台设备")
            return
        with self._items_lock:
            has_items = bool(self.send_items)
            items = list(self.send_items)
        if not has_items:
            messagebox.showwarning("列表为空", "请添加要发送的文件或文件夹")
            return

        # 去重 IP（防御性）
        target_ips = list(set(self.selected_ips))
        self.total_tasks = len(target_ips) * len(items)
        self.finished_tasks = 0
        self._send_lock = threading.Lock()
        self._sending = True
        # 禁用发送按钮（视觉上明确"发送中"）
        try:
            self.send_btn.config(text="发送中...", state='disabled')
        except Exception:
            pass

        # 全局进度的初始状态：先置 0%，异步计算总字节后再更新（大文件夹遍历较慢）
        self.root.after(0, lambda: self._update_global_progress_ui(0.0, 0, 0))
        self.global_info_label.config(text="计算中...")

        # 在后台线程中执行发送，避免阻塞UI
        def do_send():
            # 1) 预计算单 IP 视角的总字节（文件大小 + 文件夹递归）
            single_ip_bytes = 0
            for path, is_folder in items:
                if is_folder:
                    try:
                        for root, _dirs, files in os.walk(path):
                            for f in files:
                                try:
                                    single_ip_bytes += os.path.getsize(os.path.join(root, f))
                                except Exception:
                                    pass
                    except Exception:
                        pass
                else:
                    try:
                        single_ip_bytes += os.path.getsize(path)
                    except Exception:
                        pass
            # 全局总字节 = 单 IP 字节 × 目标设备数（每台设备都要完整传一遍）
            total_bytes = single_ip_bytes * len(target_ips)
            self._reset_global_progress(total_bytes)
            self.root.after(0, lambda: self.log(
                f"[发送] 全局进度基准: {human_size(single_ip_bytes)} × {len(target_ips)} 台设备 = {human_size(total_bytes)}"
            ))

            # 2) 每台设备一个线程，串行发送该项目列表
            threads = []
            for ip in target_ips:
                t = threading.Thread(target=self._send_to_one_ip, args=(ip, items), daemon=True)
                t.start()
                threads.append(t)
            for t in threads:
                t.join()

            # 3) 全部完成：强制置 100%（避免浮点误差或跳过文件的残留偏差）
            with self._global_progress_lock:
                self.global_sent_bytes = self.global_total_bytes
            self.root.after(0, lambda: self._update_global_progress_ui(100.0, self.global_total_bytes, self.global_total_bytes))
            self.root.after(0, self.clear_items)
            self.root.after(0, lambda: self.log("[发送] 所有项目发送完成"))
            # 解除"发送中"状态（无论成功/失败）
            self._sending = False
            # 恢复按钮（在主线程执行）
            try:
                self.root.after(0, lambda: self.send_btn.config(text="极速发送", state='normal'))
            except Exception:
                pass

        threading.Thread(target=do_send, daemon=True).start()

    def _send_to_one_ip(self, ip, items):
        """向单个IP发送所有项目，独立线程执行"""
        self.log(f"[发送] 开始向 {ip} 发送 {len(items)} 个项目")
        def on_item_done(path, success):
            with self._send_lock:
                self.finished_tasks += 1
                # 日志：每个项目完成后的结果
                self.root.after(0, lambda s=success, pt=path: self.log(f"[发送] {'✓' if s else '✗'} {pt}"))
        self.node.send_files(ip, items, callback=on_item_done)

    def run(self):
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        # 启动 IPC 服务器：把后续拖到 .py/.exe 图标上的文件路径接收进来
        self.start_ipc_server()
        self.root.mainloop()

    def _broadcast_bye(self):
        """发送"正常下线"通知，让其他设备立即从列表中移除本机。

        双重投递策略：
          1. 单播 —— 向已知节点列表里的每个 IP:UDP_PORT 各发一份
             （最可靠，可穿透路由器/跨网段，不依赖广播能力）
          2. 广播 —— 向本机所有网卡的广播地址 + 255.255.255.255 各发一份
             （覆盖那些尚未进入 nodes 列表或刚上线的设备）

        必须在关闭 socket 之前调用（on_close 第 0 步）。
        尽力而为：单次发送 0.3s 超时，任何失败都不阻塞退出。
        """
        try:
            hostname = getattr(self.node, 'hostname', 'unknown')
        except Exception:
            hostname = 'unknown'
        bye = json.dumps({'hostname': hostname, 'bye': True}).encode('utf-8')

        # ---------- 1) 单播：向已知节点 ----------
        # 加锁快照，避免遍历时其它线程修改 dict
        try:
            with self.node.lock:
                peer_ips = list(self.node.nodes.keys())
        except Exception:
            peer_ips = []

        unicast_v4 = [ip for ip in peer_ips if ':' not in ip]
        unicast_v6 = [ip for ip in peer_ips if ':' in ip]
        unicast_ok = 0
        unicast_fail = 0

        # IPv4 单播（一个 socket 复用，减少创建开销）
        if unicast_v4:
            try:
                s4 = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s4.settimeout(0.3)
                for ip in unicast_v4:
                    try:
                        s4.sendto(bye, (ip, UDP_PORT))
                        unicast_ok += 1
                    except Exception:
                        unicast_fail += 1
                try:
                    s4.close()
                except Exception:
                    pass
            except Exception:
                pass

        # IPv6 单播
        if unicast_v6:
            try:
                s6 = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM)
                s6.settimeout(0.3)
                for ip in unicast_v6:
                    try:
                        s6.sendto(bye, (ip, UDP_PORT))
                        unicast_ok += 1
                    except Exception:
                        unicast_fail += 1
                try:
                    s6.close()
                except Exception:
                    pass
            except Exception:
                pass

        # ---------- 2) 广播：兜底 ----------
        targets = list(getattr(self.node, 'broadcast_addrs', []) or [])
        if '255.255.255.255' not in targets:
            targets.append('255.255.255.255')

        bcast_ok = 0
        for bcast in targets:
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                s.settimeout(0.3)
                s.sendto(bye, (bcast, UDP_PORT))
                s.close()
                bcast_ok += 1
            except Exception:
                try:
                    s.close()
                except Exception:
                    pass

        # ---------- 日志 ----------
        try:
            msg = f"[退出] 已发送下线通知：单播 {unicast_ok} 台设备"
            if unicast_fail:
                msg += f"（{unicast_fail} 台失败）"
            msg += f"，广播 {bcast_ok} 个地址"
            self.log(msg)
        except Exception:
            pass

    def on_close(self):
        """干净退出：
        0. 广播"下线通知"，让其他设备立即从列表移除本机
        1. 停止所有网络线程（设置 running=False，监听 socket 有 1s 超时会让循环自然退出）
        2. 关闭所有监听/广播 socket（辅助唤醒阻塞的 recvfrom/accept）
        3. 取消 Tk 的 after 定时任务
        4. 销毁窗口
        5. 1 秒后仍存活则强制 os._exit（兜底）
        """
        # 0) 先广播下线通知（必须在关闭 socket 之前）
        try:
            self._broadcast_bye()
        except Exception:
            pass

        try:
            self.node.running = False
        except Exception:
            pass
        # 停止 IPC 服务器（daemon 线程，无需 join）
        self._ipc_running = False

        # 关闭所有 socket
        for s in list(getattr(self.node, 'listen_sockets', [])) + \
                 list(getattr(self.node, 'broadcast_sockets', [])):
            try:
                s.close()
            except Exception:
                pass

        # 取消所有已注册的 after 定时器
        try:
            for aid in list(getattr(self, '_after_ids', [])):
                try:
                    self.root.after_cancel(aid)
                except Exception:
                    pass
        except Exception:
            pass

        # 销毁窗口
        try:
            self.root.quit()
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass

        # 兜底：1 秒后若还有卡住的线程，直接强杀进程
        def _force_exit():
            time.sleep(1.0)
            try:
                os._exit(0)
            except Exception:
                pass

        threading.Thread(target=_force_exit, daemon=True).start()

    def load_config(self):
        """加载配置文件，恢复保存路径与设备名。

        注意：本方法在 self.node 创建**之前**被调用，因此这里只把配置读入
        self._device_name（临时备份）。真正的运行时设备名在 Node 内部维护，
        通过 P2PApp.device_name 属性读取。
        """
        global SAVE_DIR
        # 默认设备名 = 系统主机名（临时备份，Node 创建时从这里读）
        self._device_name = socket.gethostname()
        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE, 'r') as f:
                    cfg = json.load(f)
                    path = cfg.get('save_dir')
                    if path:
                        SAVE_DIR = Path(path)
                    dev = cfg.get('device_name')
                    if dev:
                        self._device_name = dev
            except:
                pass
        SAVE_DIR.mkdir(parents=True, exist_ok=True)
        self.update_save_dir_label()

    @property
    def device_name(self):
        """设备名（只读）。

        单一来源：Node.hostname（运行时真源）。
        Node 未创建时（load_config 阶段）回退到 _device_name。
        """
        if hasattr(self, 'node') and self.node is not None:
            return self.node.hostname
        return getattr(self, '_device_name', socket.gethostname())

    def save_config(self):
        """保存当前配置到文件"""
        cfg = {
            'save_dir': str(SAVE_DIR),
            'device_name': self.device_name,
        }
        with open(CONFIG_FILE, 'w') as f:
            json.dump(cfg, f)

    def choose_save_dir(self):
        """弹出文件夹选择对话框，更改保存路径"""
        global SAVE_DIR
        new_dir = filedialog.askdirectory(title="选择文件接收保存位置", initialdir=str(SAVE_DIR))
        if new_dir:
            SAVE_DIR = Path(new_dir)
            SAVE_DIR.mkdir(parents=True, exist_ok=True)
            self.save_config()
            self.update_save_dir_label()
            self.log(f"保存路径已更改为: {SAVE_DIR}")

    def choose_device_name(self):
        """弹出对话框修改本机设备名。

        单一来源：Node.hostname 是运行时真源，这里只写它。
        P2PApp.device_name 是只读属性，会自动跟随 Node.hostname。
        """
        current = self.device_name
        new_name = simpledialog.askstring(
            "设置设备名",
            f"当前设备名：{current}\n\n其他设备在搜索时会看到这个名字：",
            initialvalue=current,
            parent=self.root,
        )
        if new_name is None:
            return
        new_name = new_name.strip()
        if not new_name:
            messagebox.showwarning("无效名称", "设备名不能为空")
            return
        if not (hasattr(self, 'node') and self.node):
            messagebox.showwarning("尚未就绪", "网络组件尚未启动，请稍后再试")
            return
        # 唯一写入点：直接改 Node.hostname
        self.node.hostname = new_name
        self.save_config()
        self.log(f"设备名已改为: {new_name}（下次广播/回复生效）")

    def update_save_dir_label(self):
        """更新GUI中显示保存路径的标签"""
        if hasattr(self, 'save_dir_label'):
            self.save_dir_label.config(text=f"保存位置: {SAVE_DIR}")

    # ---------- 单实例 IPC（外部拖到图标上的文件转发） ----------

    def _bring_to_front(self):
        """把窗口激活到前台（IPC 收到转发时调用）。"""
        try:
            self.root.deiconify()
            self.root.lift()
            self.root.attributes('-topmost', True)
            self.root.after(200, lambda: self.root.attributes('-topmost', False))
        except Exception:
            pass

    def _add_paths_from_ipc(self, paths):
        """把从 IPC 收到的路径加入待发送列表（主线程调用）。"""
        added = 0
        skipped = 0
        for p in paths:
            try:
                p = os.path.normpath(str(p).strip())
            except Exception:
                skipped += 1
                continue
            if p.startswith('{') and p.endswith('}'):
                p = p[1:-1]
            if os.path.isdir(p):
                with self._items_lock:
                    self.send_items.append((p, True))
                added += 1
            elif os.path.isfile(p):
                with self._items_lock:
                    self.send_items.append((p, False))
                added += 1
            else:
                skipped += 1
        if added > 0:
            self._refresh_item_listbox()
            msg = f"[拖拽] 从外部添加 {added} 个项目到待发送列表"
            if skipped:
                msg += f"（{skipped} 个路径无效，已忽略）"
            self.log(msg)
        elif skipped:
            self.log(f"[拖拽] 收到 {skipped} 个无效路径")
        self._bring_to_front()

    def start_ipc_server(self):
        """启动本地 IPC 服务器（后台线程，监听 127.0.0.1:IPC_PORT）。

        外部进程（拖到 .py/.exe 图标上的新实例）会把路径以 UTF-8 JSON
        `{"paths": [...]}` 通过这条回环 TCP 连接发过来。
        """
        def server():
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(('127.0.0.1', IPC_PORT))
                sock.listen(5)
                self.log(f"[IPC] 单实例监听 127.0.0.1:{IPC_PORT}")
            except OSError as e:
                self.log(f"[IPC] 端口 {IPC_PORT} 绑定失败（可能另一实例在运行）: {e}")
                return
            sock.settimeout(1.0)
            while self._ipc_running:
                try:
                    conn, _ = sock.accept()
                except socket.timeout:
                    continue
                except OSError:
                    break
                try:
                    conn.settimeout(2.0)
                    buf = b''
                    while True:
                        try:
                            chunk = conn.recv(65536)
                        except socket.timeout:
                            break
                        if not chunk:
                            break
                        buf += chunk
                        if len(buf) > 4 * 1024 * 1024:
                            break  # 防御性：单条消息 ≤ 4MB
                    if buf:
                        try:
                            msg = json.loads(buf.decode('utf-8'))
                            paths = msg.get('paths', []) if isinstance(msg, dict) else []
                            if paths:
                                self.root.after(0, lambda ps=paths: self._add_paths_from_ipc(ps))
                            else:
                                # 无路径：仅把已有窗口前置
                                self.root.after(0, self._bring_to_front)
                        except Exception as e:
                            self.log(f"[IPC] 解析消息失败: {e}")
                except Exception:
                    pass
                finally:
                    try:
                        conn.close()
                    except Exception:
                        pass
            try:
                sock.close()
            except Exception:
                pass

        threading.Thread(target=server, daemon=True).start()


def try_send_to_running(files):
    """尝试把文件路径转发给已运行的实例。

    成功返回 True（说明已有实例接住了消息）；无实例或失败返回 False。

    注意：即使 files 为空也做一次连接尝试 —— 这样用户"双击图标启动第二份"时
    不会真的开出第二个进程，而是激活已有窗口。
    """
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.8)
        s.connect(('127.0.0.1', IPC_PORT))
        payload = json.dumps({"paths": list(files)}).encode('utf-8')
        s.sendall(payload)
        try:
            s.shutdown(socket.SHUT_WR)
        except Exception:
            pass
        s.close()
        return True
    except Exception:
        return False


if __name__ == '__main__':
    # 收集命令行参数里的文件/文件夹（Windows 把拖到图标上的路径作为 argv 传入，
    # 含空格的路径系统会自动加引号，所以这里拿到的已是完整路径）
    files = [a for a in sys.argv[1:] if a and not a.startswith('-')]

    # 先尝试把参数转发给已运行的实例；成功则本进程直接退出
    if try_send_to_running(files):
        sys.exit(0)

    # 没有已运行实例：本进程作为"主实例"启动
    app = P2PApp()
    if files:
        # 冷启动时把命令行参数里的文件/文件夹加入待发送列表（等 GUI 就绪）
        app.root.after(300, lambda ps=files: app._add_paths_from_ipc(ps))
    app.run()
