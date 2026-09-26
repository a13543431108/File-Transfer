import socket
import struct
import threading
import hashlib
import os
import sys
import time
import json
import select
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

# ================= 内嵌图标 =================
# 别TM删除这些看上去没有用的辅助方法，删除了会导致线程崩坏，这个不是我的代码的问题，我tm排除到22：40分才发现的问题。是TM sb pyinstaller的线程安全点死锁的问题。
# 打包时若未把 icon.ico 当数据文件，窗口图标会丢失。这里内嵌一份，
# 运行时解码到临时文件使用；外部 icon.ico 仍作为回退。
_EMBEDDED_ICON_B64 = (
    "AAABAAEAAAAAAAAAIAC3EwAAFgAAAIlQTkcNChoKAAAADUlIRFIAAAEAAAABAAgGAAAAXHKoZgAAE35JREFUeJztnV2sJEd1x/9z7117vax3YzaABLJ4IpKdRSsjBLJMFnudOBISkv3gKDF5MBLiJS+8QawYCzB28sAz4SMPkZAIH5L96gC25YjIRlYSiZUtlBeUZQ2sd/2xuza7d+9MozLnOOX29HT3TPd0TdfvJ5Xm3pnu+aiPf506dapKAgAAAAAAAAAAAAAAAAAAAACAjWdS8/+6KAb6XACQtGUp188HyJKJNbyZ/X9wgIYYPvui/R1/FwDoGTf5b5T0gKRbJe3Xerki6UeSvirppKRtSdM1fweAbDkq6UUbhw+Zzkk6Yd9pZ+A8AciGf7cGuGvm9xBp177Dq4gAwHrxhj+0BRDMfkQAYM0M3fARAYABqWuUfZr+iADAwAzZ4yMCAANPA4aGVkVhc/RFD595KPqMeRGIM4sLOC/pLkmPmwjsdfhdALKmSgC8UV6QdIM91olFUzzY52ZJP5B0bdTYyyACAD2zyDQPJviBHj/7hDXu2OxvOhyYjDgBJCMAh6OQ4S4r+c6KIjBmWBsBSQwBQsO83h67GgLEuDkfGvWjLYYDd0t6eqRrB1gbAdkIQFsRiB2GfX6nIbkk6UlJX7G1EWP8jbBBQwD31vc5Nm0zHEghanEd6aw5YAMMB2DUArCMCIw5Xbbf+UPLEwQARi8AbUVgzMmF4Ew0C8PsAHROaj3LnolAmO+/0+IPcnWETWxvhn1DfxEYL6kJwCIRuJJAz9xHWoS/TtwAZDEEqBoOvJThEKBNIBZxA7AUKS+w2bPtwYIlcFzSfZJuH2DLsj6Z2D6MVeIanr8uinycZzEQNwAbHQdQx9CblvaB52WIeXjeHj3PY5osxiJuAEY5BMjBxD1geRzn+bKJuAFozaZUFJ8Wm4wk+bqKJh7+Jo0/bOt2xKwAgFH4AOYxJtO2ySxAU8trn73XcbMqXmcoAGOyAKAe4gZg9BZAjhQtLIJy3EDT94RMQQDSp63zNQ6YqrP8mDLMHAQgbcrTgMQNQKcgAGniMQEXoz0ZtSBuwB8P2uvEDcCo4gDGhufloYo4gKpQYOIGoFMo+PTZFzn1iBuATkEA0qfceOtoEohUjhvwICvIDAQgX4gbAJyAI6Rt3AAOwIxBAMYHpjw0BgHIO24AMgcByDNuAOANEIBxERr5y7Ya0HdVYowPlSAA440b8L8BKkEAxkfs2af3h4UQBwCQMQgAQMYgAAAZgwAAZAwCAJAxCABAxiAAABmDAABkDAIAkDEIAHRJ3XkEkBiEAkMXlM8Z4NyBDQEBgK6Pb59Ey5E5dyBxGALAKmxbAz8q6fuSfmkp/H2MzUY3A84FSPtcAM//vq+fLGk9npB0bk4dOmfCEKCjSRQKBpZhxzYbCY3/EUnvLJ1JuGvPfXnoLwqLQQBg1cZ/yCyK8gEmQQg+bn4BhgKJggBAF42/qh6xIUniMAsAfTT+qV3/hKTXImdhqlZAoUxBAKDrxl/Y9cEJeJ/9HwQhZbbsMbspSwQAum78hcUBfEbSKUl/ZAKQau8/s+3Us4xbQABgEVvW+G9rOOZ3J2A4cPQb0cGjKXNJ0pN2UvJJ+/5ZDQmIA1g/mxAH4I38g5JeiUz5YqTprB2qEv/20ZPND4WleVDSYbME2tSXYoPSrqQjZgVkBUMAmMfErIQwh3+LNZLtJd5jU9hnv/G4DVtez2UogAUAY2rIq/7O/bmdpoQAwDwKqxsXzUE2WWIqr0g0NfnO2YAAQB0P2LTeTkuP/iTRBBH4AKAKn+4LU2N3SnrUjhlfNA3oXLFTilObBpxEexYAAgA1zMz593hDEXATOjjRPivpMRtXDx0I5A698N2ft8cCIfgD88ZJxAH0yybEAVSt/T9fExMwi+bV/0RpcaDH/RE2EnwA0IQ9EwG3BC4sCJud2PVHLIZAZkUMOe7fipYpQwQCAH2IwLb1qLdKekc0gzD0DEBWHv4mIADQlwjABoAAQFciUN4SbGLXvGav0/smCAIAXYjAy6Utwa6WdNpiCCBhEABYRQR8ijDE0H9X0hlJL0j6nqS/kPSLaF0BJAhxALAK0yhY6J5oA9AQBxDAP5A4CACsShwUFO+s469BwiAA0AWzUvAMDX9DQACgS/D0bxg4AQEyBgEAyBgEACBjEACAjEEAADKGWYD0ibeyymKNOqwPBCB9fJGN/w3QGQhA2oQe/7qonMJWVlgB0BkIQJp4Iz9oe9gVpU0t42sAlgYBSJuJ9foAvYAAbF54LT0/dAYCkD5tGjyHX0AriAMYl6UQzrpnpgAagwCkT5Odbq9Yz/+UbcbR9iRfyBSGAOnTxKS/StI5Sfev4ftARgLAXurDUtiuu4sIZv9PJD1kU4bxHnyUH6wkADt2sENdJYRu8XPrfifpw5J+G51vV2ZW2oprFlkNoeyw8qCSUDlelPSuitevkfTemgoIy3PZTPf4PL+Yq60Mwnl8iyjvwedCcL3dX8VZ+w6QKaGi/GrOPm6TaMfXW6zh4zDsDhfSXds/vyr/t+14LZX23S+n2Zz7w2fcbOVWPp3Xrz0VCQDiniGhcvxPxUaOXmE+bZWP45S7xc/P+3lN/t9r+V8e11edd+eNf5/dG7+X46fhPhd9F8hUAP6johK4GXmTpL+zXiQMGRCBbvB8/M8O839i10ztnptK23ardFqvfzZlmjHvsbFg+cx0/3/PPM2fKFUyP3KZtNqR1SH/X1kx/7dK4vBJM+33Kt6zsDJ/VyYC4L8v+FpenZPXM3t8NfLHjD1P3sI3LAP25piXnjlhvPo5zMVe+HpH+b9t11yuEJT4M74Z3TN2EIAK/EfeKOm/o16k/OPj8f/PJH1H0k/tHDg/Bgra42Z+yP8nJO2vuK4q/0/ba+83h9+nJH1kzj3x+7gIfMh8AHHcwFhxv8ghc3weKuVPYX+ft5mT87nMek2sBwhjxn+U9Pno5FdVVB4fT07NNA2PsBohDw/XzLRU5b9sqi+eCpwn4orKNpT130dlP3YQgAX4GPKApGej2PKquPNphalKWk+qyv89e63qPi/TZ62s3YeQAwwBKtiJ1C+Y8n9jpuj7FlgC3tNkoY5rpGmFq8r/RWN5L8vTVsav2/tQhvC2CnTUzCTvNeY5kkibkWZRz3/KyjYu61zAAqggHnN65Fk46/0OSc9ETkE3OWEzKKzMfMrwGSvTkxmN+2FJtiPH0sPmaHKlnFqPMrXkIaikYZP7BbxsvLwuWRlek2nP72ABrGAZHJP0bUkvJWDSkpqlF22e/1hFmeYGAlBBXVipLySRRawFM/KjVrGCo/DIgvthfZy1Mf5JM/cfk3Qm6vW9gucK04AVNFG58lJTZ7/tRAPDsxvFBJQXG409yKcJCEAFTTaLmJWChtwXcGlOpYNh8UU+7qMBWEib3WIKczRlMz7aQGj00Iplt4savWkEo6QqRLrp66MjZ88w5McV22dxXgdW2GtZnauQuwBkp/g1DLU3Qt8UVtdDCPST9pm70TTgrj0XXiNMOgO2SuJX/j83hv796/h8f/8bog1wiiidtdfia0fPJOM1+IqO2i5vq51zfqy78s/WmP8+tRfWRNwn6c/t+R/buQonc5n+y1UA4grwD5JO2POPS3owwwoQ58f9tgNx1aYkfRHG3D+S9NU1rVWgA8iUJibgjaVrc86PdadzkSD3fZgJQ8AM8QL+gVW4y9FiGl/w9G+la8eM/8YfzsmPdafdKBZ/XSIQwAmcCV7IYVXcr6OKFy8Gmdkeh75ybswVw3/bAVszUM6PIdJ0IBGADPAKf9jivKtWg523a+J7cswPRCATcjB1Y7yiV5HbqjlveE2u6SOVcSdcWKzziIlA1dZ00AG5CYAahILCW+kzAAgRGBgyFRZR2BRZ0dPSXP97UiMCd9lU7U60IA06AAGAeXijvGjThBc6jI/wxn2zzchca/+XrVFEADqDLaGWz48wU9AXJyIn5LSlY3CodQuTEayNyA4EYPn8OGzXd30Y7M6KIjBmttbln2MIAHXEHvsufQHu2Atm/Z2SHm0xHLhb0tMjDd2drTM0GQGAIWkrAu48fGzE+/ZdsmXJX8lwbUpvMARIOz/aDAdSCFgq1pDWsjw5xzgASN8SuLDA/PUeccxp17bcD1ZAryAAsKkiMOa0z4TguM3CzPqywBAA2FQRGDsT25shiEFvIACwKSLgpxyPLS3CX+8tbgABgE0QgZetJ5yMMNVxpYHzc+m4AaYBIWUR2DYROG57+N0+wJZlfTKxbcmqhCA8f10U+Vh0HTeAAEDKTK1Sh/nwewbatLQPvDGHmIfn7bGIhMAfD9rri4YKxA00ILV576HZtPwY6559Byryf21xA2PMVBgfvmXZZCRpK5ruq6PXuAGGALBJjMm0LRr+niaWVzlu4PWmQwEsAICM4wawAADSpmhhEZTjBmrfEwEASJu2ztc4YKrO8p8hAADpUt6TsfO4AQQAID3m7cmojuMGviTpOQQAIF0KC4MOXn2PjlzUuCcmDosIcR1/ZXsv3IYAAKSNr4Hwv+toMrUYhOSPJX0NAQBIm9ih13XcwJ8RBwCQJ0EorsECAMg4bgABAMg3bmCCAADkGzdAJCBAhnEDb4IAAOQbN4AAAOQcN4AAAGQcN0AcAEDGIACbRdOtpAEawRBgM3hz/XbF/wBLgQCkT7zXu0/nrO38eBg3DAHSZmIN/Jik70v6paXvSjpqr4XDMwCWAgsgXbx3/6CdjvPO6LW/lnSHpLvttR2b8wVoBRZA+jxojX83muK5Ys89Yhs7+Fl6AK1AANI2/UMc98es0ceHY+6z18PuLogALA0CkD5FzRABEYClQQDeztDHRvk8f3DuvSbpCXsuHJRZBhGAlUAAljuPfR1pao/hWOxz1qjnWQOIACwNlaT9vurrojAr4JSkz0j6V/MJ+PdcJAJ3MTsATUAA2u+rvm62bHnngRqLrSwCd9rwgWAhqAQBaL+v+hB4z19HWQTCDMJJRACqwAfwdopEU5syDWb/YUlf7jGfYATkZgE0WU03htV22yYaHzfr4WICPg1IkK0MPfzhbDQaAkBGAlBEzrSnrDeMj1HehNSGqf3GJ633D78d0YNsBSDmfptXvyqBgJ+2wUFNmNnQLuwM+0CP+QgjICcfwMwaUpjmu9UCbG6XtF9p45bLdQ02eZzZ9RdsGpAZAFhITgIQDwVCw7jHHGSpWkEeCBR8Fn9pgUDXLrAIyo3/cbt/XggxQJYCEDcURTvrpMwHJP2LTesVLRo/UYBQS44CoMgkTnnKz033hyQdWRDfT+OHpclVAJxUPeO++i8MUW6LhgNlaPywEqmOf+H/qbJSaPywMghA2s7K4KP4sYlAeUswGj+sDAKQPl+U9BtJV5e2BAuHQNL4YSUQgPTjFn5h8Qrfk/SCpDO2LfjxaKqPxg9LkbsTcFOGAs/ZVuC+J0B8MAjz/LA0CMBmxS34ue8cDQadgABsZtwCDR86AQHYLFKNW4ANBScgQMYgAAAZgwAAZAwCAJAxCABAxiAAABmDAABkDAIAkDEIAEDGIAAAGYMAAGQMAgCQMQgAQMawGhAgbeKDYDrfxh4BAEgbP8TW/+4UBAAgXSZ2JqS3Uz8arjMQAID08EZ+0A6zLaLnD5auWQkEACBdJtbr9wYCALBZ28AxBADIiEnLa1sJBHEAAOOxFC61nSlAAADSpmiQrljP/5SdHTHvJOm5MAQASJsmJv1Vks5Jur/tmyMAsAjvYWAYCjsBehHB7P+JpIdsynASHRxTW34IANTVj3c0qITQLYU15N9J+rCk39r/8xrzrHRWpB8qKyu7hW0cAciby2Y6Hqp4/RpJ762pgNBf/l9tZXC+5n3KZ0W6EFxv91dxBidgnnhD3pV0es55gxM7dTjUj1uiU4phvfkfnHm32nP7omm+cprNuT98xs3RCdKxL8Gv/T8KNV+2rZL8vOLAUa8wn7bK52YprDf/77X8L4/r41S+r7B77i29l+Pv5Z8NGeLDv7+1CjOdU7H8uc+VeiFIM/8ndo3snqr3ndljEHfIFK9I75b0cqlixBVlzzzNn4juC5V3a4FJSlJt8vx7j6RXVsz/LXvOy/ST5l/Yq3jPwvw6oewhYzxg5FtWKfYW9Ba71qs0DjKBxny9o/zftmsuVwhK/Bn/HG7AnMsb9xb/qaT/inqRcr2Ix/8/k/QdST+V9IJFnsFq+X+jpCck7a+4rir/T9tr7zeH36ckfWTOPfH7uAjcFOIGEADYtnHiw5K+YJVj3vSwVx53HE/NNA2PsBohDw/XzLRU5b9sqi+eCqxaFORl+09W1lhz8OYY8oCkZ6PY8qq482mFqUrSWlJV/u9VOPyKUpk+a2X9hg8BCwBiU/QDZoq+b4El4ITKBN3Rti0WLe73sgxDhtsk/W9U5gBv4ObgUUmnol5jniOJpI1Is6jnP2VlG5c1wFvwinGDpKdL5iNCoI1s+IWV5Q2lMgaYy3bkWHrYHE1ekaZWsaaWPASVpEGT+wW8bLy8LlkZ+noAGj80IvZGH5P0bUkvJdCzkdQovSjpm1Z288r0TXACQt3sgE/zhYi1OyR91CpWcBQeGfg7wh84a2P8k5KekfRYWOkX9fo+fHsbCACo5VJTZ7/tRAPDsxvFBJQXG+Hph07wGHTGkemyXVoTUAsWACwD9SZN5pr5AAAAAAAAAAAAAAAAAAAAAACgcfN7tOQC7LBV4qgAAAAASUVORK5CYII="
)


def _materialize_embedded_icon():
    """把内嵌图标写到临时文件并返回路径；失败返回 None。"""
    if not _EMBEDDED_ICON_B64:
        return None
    try:
        import base64 as _b64
        import tempfile as _tf
        data = _b64.b64decode(_EMBEDDED_ICON_B64)
        p = os.path.join(_tf.gettempdir(), "p2p_file_transfer_icon.ico")
        with open(p, "wb") as f:
            f.write(data)
        return p
    except Exception:
        return None


# ================================================================================
# 房间模式（公网信令 + TCP 打洞）—— 内联实现
# 仅当用户输入"房间号"并点击加入时才启用；不输入则维持原有局域网模式。
# ================================================================================

# ---------- 应用版本（单一来源，改这里全局生效） ----------
APP_VERSION = "17"

# ---------- 房间模块配置（单一来源，改这里全局生效） ----------
RM_VER = 2                       # 协议版本（2 = 支持房间密码）
# 消息类型：客户端 -> 服务器
RM_T_JOIN = "join"
RM_T_HB = "hb"
RM_T_PUNCH_REQ = "punch_req"
RM_T_BYE = "bye"
RM_T_NAT_PROBE = "nat_probe"              # 客户端 -> 服务器：请求回复观察到的公网地址
RM_T_TIME_REQ = "time_req"                # 客户端 -> 服务器：NTP 式时间同步请求
RM_T_TIME_REPLY = "time_reply"            # 服务器 -> 客户端：回显 t1 + 服务器时刻 t2
RM_T_UDP_HELLO = "udp_hello"              # 客户端 -> 服务器：登记 UDP 打洞 socket 公网映射
# 消息类型：服务器 -> 客户端
RM_T_JOINED = "joined"
RM_T_NAT_PROBE_REPLY = "nat_probe_reply"  # 服务器 -> 客户端：观察到的公网地址
RM_T_MEMBER_JOIN = "member_join"
RM_T_MEMBER_LEAVE = "member_leave"
RM_T_MEMBER_UPDATE = "member_update"   # 成员信息更新（如映射登记完成）
RM_T_PUNCH_GO = "punch_go"
RM_T_ERROR = "error"
# 信令连接
# 首次使用（无历史）时预填的参考服务器地址，方便新用户
DEFAULT_ROOM_SERVER = "42.194.133.132"
RM_DEFAULT_SERVER_PORT = 3336       # 信令服务器默认 UDP 端口
RM_DEFAULT_SERVER_TCP_PORT = 3337   # 信令服务器 TCP 映射观测端口
RM_NAT_PROBE_PORT = 3338            # 信令服务器 NAT 探测端口（对称 NAT 检测用）
RM_HEARTBEAT_INTERVAL = 20          # 心跳间隔（秒）
RM_RECV_TIMEOUT = 1.0               # 信令 socket 接收超时（秒）
# 打洞
RM_PUNCH_CONCURRENCY = 6            # 同时打洞的最大任务数
# 方向 A：单次打洞中【并发 connect 的候选数】上限。
# 9998 端口上并存的打洞 socket 越多，SO_REUSEPORT 的入站 SYN 匹配
# 越容易分错，TCP 打洞成功率越低。限制为 2 个并发，在保留少量并行
# （P2 #3）的同时显著降低 9998 端口竞争。
RM_PUNCH_CANDIDATE_CONCURRENCY = 2
# 单次 connect 超时（秒）。风暴模式下每次尝试超时设短，让重连更密：
# 端口受限锥形 NAT 要求"先出后进"，两端 SYN 时序错开时一侧收不到回包。
# 缩短单次超时 + 高频重连，能显著提高两端 SYN 交叉命中的概率。
RM_PUNCH_CONNECT_TIMEOUT = 2.5
RM_PUNCH_RETRY = 3                  # 外层重试轮次
RM_PUNCH_RETRY_BACKOFF = 1          # 重试退避基数（秒）
RM_PUNCH_STORM_DURATION = 6.0       # 每轮"重连风暴"持续时长（秒）
                                    # 在此窗口内不停地做并行 connect 轮次，
                                    # 直到成功（含握手确认）或超时。
RM_PUNCH_HANDSHAKE_TIMEOUT = 2.0    # TCP 打洞握手确认超时（秒）
# TCP_READY 收到后，等待 SYNC 协调的窗口（秒）。
# 有 UDP-RTP 时优先走 SYNC 精确对齐（同 tGo 同时打洞）；
# 超过此窗口 SYNC 仍未完成 → 回退到"直接打洞"。
# SYNC 理论耗时：4 次采样(~0.1s) + COMMIT 延迟 0.5s ≈ 0.7s，取 1.5s 留余量。
RM_TCP_READY_SYNC_WAIT = 1.5
# SYNC 超时回退打洞时，收到 TCP_READY 后再等多久才发 SYN（秒）。
# 给对端足够时间也收到本端 TCP_READY 并进入等待，使两端发出时刻
# 误差 ≈ RTT/2，优于"立即打"的随机错开。
RM_TCP_READY_FALLBACK_DELAY = 0.3
RM_PUNCH_HANDSHAKE_MAGIC = b"P2PH"  # 握手魔数（两端一致，4 字节）
                                    # 作用：区分"真通"与"半开连接"。
                                    # connect 成功仅代表本端三次握手完成，
                                    # 半开连接（对端未收到本端 SYN）本端也会
                                    # connect 成功但发不出数据。发魔数收魔数
                                    # 才能确认双向可达。
# 连接保活
RM_NAT_KEEPALIVE_INTERVAL = 15      # 维持 NAT 映射的间隔（秒）
# B：保活健壮性 —— 新建连接宽限期内不判死；连续失败 N 次才判死（抗抖动）
RM_TCP_KEEPALIVE_GRACE = 10.0        # 新建 TCP 连接宽限期（秒）
RM_TCP_KEEPALIVE_FAIL_THRESHOLD = 2  # 连续探测失败次数阈值
# TCP 心跳 RTT 探测：仅在空闲时才发（避免干扰传输），间隔与 UDP-RTP 一致。
RM_TCP_PING_IDLE = 5.0                # 空闲超过 5 秒才算"可探测"
RM_TCP_PING_INTERVAL = 15.0           # PING 间隔（与 UDP-RTP 心跳一致）
# 「等待精准打洞」门闩超时（秒）：UDP-RTP 就绪后若此时间内未等到
# SYNC 结果（COMMIT / 精准打洞完成），自动放行兜底风暴，避免永久阻塞。
# 门闩有效期：必须覆盖"UDP-RTP就绪 → SYNC采样 → COMMIT → 到点打洞"全程。
# 实测 SYNC 全程约 1~2 秒；取 6 秒留足余量。过短会导致门闩在 SYNC 完成前
# 失效，responder 的 _punch_task 抢先打洞，两端用不同打洞任务 →
# 一端成功一端失败（方向固定的"不一致"半开）。
RM_SYNC_AWAIT_TIMEOUT = 6.0
# 房间模式专用 TCP 端口
# 必须与局域网 TCP_PORT(9999) 分离：Android 的 SO_REUSEADDR 比 Windows 严格，
# 若映射观测连接与文件接收监听绑同一端口会 EADDRINUSE，导致无法登记公网映射、
# pub_tcp 为空、打洞必然失败。
# 房间模式 TCP 端口：映射观测 + 打洞共用（须与映射端口一致）。
# 从 9998 改到 9995：实测 9998 在部分环境被占用/冲突，换到空闲的 9995。
RM_TCP_PORT = 9995
# 房间模式专用 UDP 打洞端口（与局域网发现端口 UDP 9998 冲突，独立为 9996）
RM_UDP_PORT = 9996
RM_UDP_PROBE_INTERVAL = 0.1         # 探测发送间隔（秒）
RM_UDP_PROBE_DURATION = 5.0         # 探测持续时间（秒）

ROOM_AVAILABLE = True

RM_STATE_CONNECTING = "connecting"
RM_STATE_CONNECTED = "connected"
RM_STATE_FAILED = "failed"


def rm_fmt_host_port(ip, port):
    """格式化 'ip:port'；IPv6 用 '[ip]:port' 以便区分端口。"""
    if ":" in ip:
        return "[%s]:%d" % (ip, port)
    return "%s:%d" % (ip, port)


def rm_parse_host_port(s):
    """解析 'ip:port' 或 '[ipv6]:port'，返回 (ip, port) 或 None。"""
    if not s:
        return None
    s = s.strip()
    if s.startswith("["):
        # [ipv6]:port
        close = s.find("]")
        if close < 0:
            return None
        ip = s[1:close]
        rest = s[close + 1:]
        if not rest.startswith(":"):
            return None
        try:
            return (ip, int(rest[1:]))
        except ValueError:
            return None
    # ipv4:port（无方括号）
    if ":" not in s:
        return None
    ip, port = s.rsplit(":", 1)
    # 若 ip 部分仍含冒号，说明是未加方括号的 IPv6（非法格式），拒绝
    if ":" in ip:
        return None
    try:
        return (ip, int(port))
    except ValueError:
        return None


def rm_detect_nat_type(server_ip, server_port=RM_DEFAULT_SERVER_PORT,
                       probe_port=RM_NAT_PROBE_PORT, timeout=1.5, log=None):
    """通过向服务器两个不同的 UDP 端口发探测包，对比服务器观测到的公网映射。

    原理：
      · 锥形 NAT（Cone）：同一本地端口 -> 任意目标的映射相同
      · 对称 NAT（Symmetric）：不同目标的映射不同（NAT 按目标分配端口）
      · 无响应：UDP 被完全封堵，无法判定

    返回 dict：
      {"type": "cone"|"symmetric"|"unknown"|"no_udp",
       "primary": "ip:port"|None, "alt": "ip:port"|None}

    只做探测+日志，不影响后续流程。
    """
    _log = log or (lambda m: None)
    fam = socket.AF_INET6 if ":" in server_ip else socket.AF_INET
    s = None
    try:
        s = socket.socket(fam, socket.SOCK_DGRAM)
        s.settimeout(timeout)
    except Exception as e:
        _log("[NAT探测] 创建 UDP socket 失败: %s" % e)
        return {"type": "unknown", "primary": None, "alt": None}

    def _probe(target_port, tag):
        try:
            payload = json.dumps({"type": RM_T_NAT_PROBE, "ver": RM_VER,
                                  "probe": tag}).encode("utf-8")
            s.sendto(payload, (server_ip, target_port))
            return True
        except Exception as e:
            _log("[NAT探测] 向 %s:%d 发包失败: %s" % (server_ip, target_port, e))
            return False

    replies = {}
    try:
        # 用同一本地 socket 依次探测两个不同服务器端口
        if not _probe(server_port, "primary"):
            return {"type": "no_udp", "primary": None, "alt": None}
        deadline = time.time() + timeout
        while time.time() < deadline and "primary" not in replies:
            try:
                data, _ = s.recvfrom(2048)
            except socket.timeout:
                break
            try:
                msg = json.loads(data.decode("utf-8"))
            except Exception:
                continue
            if msg.get("type") == RM_T_NAT_PROBE_REPLY:
                replies[msg.get("probe", "")] = msg.get("pub", "")

        if not _probe(probe_port, "alt"):
            return {"type": "unknown",
                    "primary": replies.get("primary"), "alt": None}
        deadline = time.time() + timeout
        while time.time() < deadline and "alt" not in replies:
            try:
                data, _ = s.recvfrom(2048)
            except socket.timeout:
                break
            try:
                msg = json.loads(data.decode("utf-8"))
            except Exception:
                continue
            if msg.get("type") == RM_T_NAT_PROBE_REPLY:
                replies[msg.get("probe", "")] = msg.get("pub", "")
    finally:
        try: s.close()
        except Exception: pass

    primary = replies.get("primary") or None
    alt = replies.get("alt") or None
    if not primary:
        nat_type = "no_udp"
    elif not alt:
        nat_type = "unknown"
    elif primary == alt:
        nat_type = "cone"
    else:
        nat_type = "symmetric"
    return {"type": nat_type, "primary": primary, "alt": alt}


# 服务器时间 - 本地时间（毫秒），由 RmSignalingClient._sync_time 更新，
# 供 RmHolePuncher._wait_until 读取。用模块级变量避免跨类传引用。
_RM_CLOCK_OFFSET_MS = 0


def rm_punch_handshake(sock, timeout=RM_PUNCH_HANDSHAKE_TIMEOUT):
    """TCP 打洞握手确认：双方各发魔数、各收魔数。

    返回 True 表示双向可达（收到对端魔数）；False 表示半开或失败。
    成功时会把 socket 超时恢复为 None（阻塞模式）。

    原理：connect 成功只能证明本端三次握手完成。在半开场景下
    （本端 SYN 到了对端、对端 SYN 未到本端），本端 connect 也会
    成功，但本端发出的数据对端收不到、对端也无对应 socket 接收。
    因此必须做一次应用层往返确认。
    """
    try:
        sock.settimeout(timeout)
        sock.sendall(RM_PUNCH_HANDSHAKE_MAGIC)
        buf = bytearray()
        magic = RM_PUNCH_HANDSHAKE_MAGIC
        while len(buf) < len(magic):
            chunk = sock.recv(len(magic) - len(buf))
            if not chunk:
                return False
            buf.extend(chunk)
        ok = bytes(buf) == magic
        try:
            sock.settimeout(None)
        except Exception:
            pass
        return ok
    except Exception:
        return False


class RmPunchResult:
    """打洞成功的结果：一条保持打开的已连接 socket。"""

    def __init__(self, ip, port, sock):
        self.ip = ip
        self.port = port
        self.sock = sock


class RmHolePuncher:
    """对单个对端执行 TCP 打洞（TCP 同时打开）。"""

    def __init__(self, local_tcp_port, log=None):
        self.local_tcp_port = local_tcp_port
        self.log = log or (lambda msg: None)
        # P2: UDP 打洞成功后记录的对方 UDP 公网端口，用于预测 TCP 端口
        self.udp_hint = {}     # {peer_id: (ip, port)}
        self._hint_lock = threading.Lock()
        # P2 #9: 自适应 connect 超时（由 SYNC RTT 调整）
        self.connect_timeout = RM_PUNCH_CONNECT_TIMEOUT
        # 诊断开关：逐候选的失败/超时日志默认不输出（并行候选本就多数失败），
        # 只在排查问题时打开。成功和最终汇总日志始终输出。
        self.verbose = False

    def set_adaptive_timeout(self, rtt_ms):
        """P2 #9: 按 SYNC RTT 调整 connect 超时（3~8 秒）。"""
        if not rtt_ms or rtt_ms <= 0:
            return
        # 下限 2.0s（原 3.0s）：配合"重连风暴"模式，单次超时更短让重连更密
        t = max(2.0, min(8.0, rtt_ms / 1000.0 * 4.0))
        self.connect_timeout = t

    def set_udp_hint(self, peer_id, ip, port):
        """P2: 记录 UDP 打洞得到的对端公网地址，供预测 TCP 端口。"""
        with self._hint_lock:
            self.udp_hint[peer_id] = (ip, port)

    def punch(self, peer, at_ms, should_stop=None):
        """执行 TCP 打洞 —— 重连风暴 + 握手确认模式。

        与旧版的区别：
          · 旧版：每次只做一轮并行 connect（超时 6 秒），失败等下一轮。
          · 新版：在 RM_PUNCH_STORM_DURATION 窗口内【不停地】做并行
            connect 轮次（单次超时 2.5 秒），每成功一个立即做握手确认，
            握手通过才返回；握手失败（半开）则关闭继续下一轮。

        这样两端会持续交叉发 SYN，大幅提升"端口受限锥形 NAT"
        下同时打开的命中概率；握手确认则保证返回的连接是真双向通。

        should_stop：可选回调，返回 True 表示外部已连接（其他任务成功），
        风暴应立即停止。
        """
        peer_id = peer.get("id")
        tcp_port = peer.get("tcp") or 0
        if not tcp_port:
            self.log("[打洞] 放弃：对方 tcp 端口为 0 (peer=%s)" % peer_id)
            return None
        candidates = self._build_candidates(peer)
        if not candidates:
            self.log("[打洞] 放弃：候选地址为空 (peer=%s)" % peer_id)
            return None
        if self.verbose:
            self.log("[打洞] peer=%s 候选=%s"
                     % (peer_id,
                        ", ".join("%s:%d" % (ip, p) for ip, p in candidates)))
        self._wait_until(at_ms)

        # ---- 重连风暴：窗口内持续做并行 connect 轮次 ----
        deadline = time.time() + RM_PUNCH_STORM_DURATION
        round_no = 0
        while time.time() < deadline:
            if should_stop and should_stop():
                if self.verbose:
                    self.log("[打洞] 风暴停止：peer=%s 已由其他任务连接" % peer_id)
                return None
            round_no += 1
            result = self._connect_candidates_parallel(candidates, peer_id)
            if result:
                # 强制应用层握手确认：双方各发魔数、各读魔数。
                # 【只有握手成功才认这条连接】——握手失败=半开/单向（connect
                # 成功仅证明本端三次握手完成，不代表双向可达），必须丢弃重试，
                # 否则会把半开连接当可用通道，发数据石沉大海。
                # （曾"降级信任"半开连接，导致电脑自认成功、手机认为失败、
                #   互传失败——已废止。）
                if rm_punch_handshake(result.sock, RM_PUNCH_HANDSHAKE_TIMEOUT):
                    self.log("[打洞] 成功（握手确认）-> %s:%d (peer=%s, 第%d轮)"
                             % (result.ip, result.port, peer_id, round_no))
                    return result
                else:
                    self.log("[打洞] 握手无回应（半开），丢弃重试 -> %s:%d (peer=%s, 第%d轮)"
                             % (result.ip, result.port, peer_id, round_no))
                    try: result.sock.close()
                    except Exception: pass
                    # 继续循环（下一轮重试）
            # 轮间短暂停顿，避免 CPU 空转与端口耗尽
            time.sleep(0.1)
        if self.verbose:
            self.log("[打洞] 风暴结束未成功 peer=%s（%d 轮）" % (peer_id, round_no))
        return None

    def punch_once(self, peer, at_ms):
        """精准打洞（TCP 同时打开的"精确"版）：

        在 at_ms 时刻，只向候选地址发【一轮】并行 SYN，然后等结果。
        不跑 6 秒风暴——风暴把多个 SYN 分散到不同时刻/端口，SO_REUSEPORT
        还会把入站 SYN 分错 socket，反而降低"两端 SYN 精确交叉"的概率。

        一轮失败 → 返回 None，调用方回退到 punch()（6 秒风暴）兜底。
        """
        peer_id = peer.get("id")
        tcp_port = peer.get("tcp") or 0
        if not tcp_port:
            self.log("[打洞] punch_once 放弃：对方 tcp 端口为 0 (peer=%s)" % peer_id)
            return None
        candidates = self._build_candidates(peer)
        if not candidates:
            self.log("[打洞] punch_once 放弃：候选地址为空 (peer=%s)" % peer_id)
            return None
        if self.verbose:
            self.log("[打洞] punch_once peer=%s 候选=%s"
                     % (peer_id,
                        ", ".join("%s:%d" % (ip, p) for ip, p in candidates)))
        if at_ms:
            self._wait_until(at_ms)

        # 单轮打洞：T_go 时做一次（多候选并行）connect。
        #
        # 为什么单轮：TCP 同时打开只要两端 SYN 交叉即成；若某次没交叉，
        # 【内核会自动重传 SYN】（约 1s/3s/7s…），覆盖数秒窗口，无需手动多轮。
        # 手动多轮反而有害：
        #   · 并发多轮 → 多 socket 同 bind(9998) 连同一目标，四元组冲突，
        #     SYN,ACK 分错 socket（曾致"TCP 成功但应用层误判失败"）；
        #   · 串行多轮 → 每轮 connect 阻塞数秒，轮间隔被撑大，错过窗口。
        # 故回到单轮，让内核重传兜底。
        self.log("[打洞] punch_once（T_go 单轮，候选=%d）" % len(candidates))
        try:
            result = self._connect_candidates_parallel(candidates, peer_id)
        except Exception as e:
            self.log("[打洞] punch_once 异常: %s" % e)
            result = None
        if not result:
            return None
        # 强制握手确认：失败=半开，丢弃返回 None（由上层回退风暴重试）。
        if rm_punch_handshake(result.sock, RM_PUNCH_HANDSHAKE_TIMEOUT):
            self.log("[打洞] 精准成功（握手确认）-> %s:%d (peer=%s)"
                     % (result.ip, result.port, peer_id))
            return result
        else:
            self.log("[打洞] 精准握手无回应（半开），丢弃 -> %s:%d (peer=%s)"
                     % (result.ip, result.port, peer_id))
            try: result.sock.close()
            except Exception: pass
            return None

    def _connect_candidates_parallel(self, candidates, peer_id):
        """P2 #3: 并行尝试所有候选地址，返回首个成功的 RmPunchResult。"""
        n = len(candidates)
        if n == 1:
            return self._try_connect(candidates[0][0], candidates[0][1])
        barrier = threading.Barrier(n)
        results = {}
        done = threading.Event()
        win_lock = threading.Lock()
        # 方向 A：限制同时 connect 的候选数（每个候选都绑 9998）
        conn_sem = threading.Semaphore(RM_PUNCH_CANDIDATE_CONCURRENCY)

        def worker(idx, ip, port):
            try:
                barrier.wait(timeout=3.0)
            except Exception:
                pass
            if done.is_set():
                return
            conn_sem.acquire()
            try:
                if done.is_set():
                    return
                r = self._try_connect(ip, port)
            finally:
                conn_sem.release()
            if r is None:
                return
            with win_lock:
                if done.is_set():
                    # 已有赢家，关闭本条
                    try:
                        r.sock.close()
                    except Exception:
                        pass
                    return
                results["win"] = r
                done.set()

        threads = []
        for i, (ip, port) in enumerate(candidates):
            t = threading.Thread(target=worker, args=(i, ip, port), daemon=True)
            t.start()
            threads.append(t)
        # 等待赢家或全部结束（上限 = connect 超时 + 余量）
        done.wait(self.connect_timeout + 2.0)
        for t in threads:
            t.join(timeout=0.2)
        return results.get("win")

    def _build_candidates(self, peer):
        tcp_port = peer.get("tcp") or 0
        result = []
        seen = set()
        for ip in peer.get("lan", []):
            if ip and (ip, tcp_port) not in seen:
                seen.add((ip, tcp_port))
                result.append((ip, tcp_port))
        pub_tcp = peer.get("pub_tcp", "")
        parsed = rm_parse_host_port(pub_tcp)
        if parsed:
            pip, pport = parsed
            key = (pip, pport)
            if key not in seen:
                seen.add(key)
                result.append(key)
        # P2 #4: 若 pub_tcp 为空，用 UDP 打洞得到的公网端口预测 TCP 候选
        # （很多 NAT 对 TCP/UDP 从同一端口池按序分配）。
        # 服务器观测的 tcp_udp_offset = 对端 TCP端口 - UDP端口（同一目标 IP），
        # 用于把预测基准从「UDP 端口本身」修正为「UDP 端口 + offset」。
        # offset 缺省为 0，行为与旧版完全一致（纯增益、零风险）。
        if not parsed:
            with self._hint_lock:
                hint = self.udp_hint.get(peer.get("id"))
            if hint and ":" not in hint[0]:   # 仅 IPv4
                hip, hport = hint
                off = peer.get("tcp_udp_offset") or 0
                base = hport + off
                for dp in (0, -1, 1, -2, 2):
                    pp = base + dp
                    if pp <= 0 or pp > 65535:
                        continue
                    key = (hip, pp)
                    if key not in seen:
                        seen.add(key)
                        result.append(key)
                if self.verbose:
                    self.log("[打洞] 追加 UDP 预测候选 %s:%d±2 (base=%d, offset=%+d, peer=%s)"
                             % (hip, base, base, off, peer.get("id")))
        return result

    def _wait_until(self, at_ms):
        """等到服务器时间 at_ms 时刻。

        at_ms 是【服务器时钟】的绝对毫秒，本地需减去 clock_offset 换算成
        本地时刻。若时钟偏差大，按最小 0、最大 2 秒钳制（TCP SYN 会自动重传，
        早打洞没问题；晚太多会错过对端）。
        """
        if not at_ms:
            return
        local_target = (at_ms - _RM_CLOCK_OFFSET_MS) / 1000.0
        remain = local_target - time.time()
        if remain > 0:
            time.sleep(min(remain, 2.0))

    def _try_connect(self, ip, port):
        """非阻塞 connect + select 等待 —— TCP 同时打开的正确实现。

        重要：不能用 sock.settimeout()+connect_ex()，因为在 Windows 上
        settimeout 会让 socket 进入非阻塞模式，connect_ex 立即返回
        WSAEWOULDBLOCK(10035)，不代表连接失败。必须用 select 等待可写后，
        用 getsockopt(SO_ERROR) 读取真实结果。
        """
        import select as _select
        family = socket.AF_INET6 if ":" in ip else socket.AF_INET
        sock = None
        bind_ok = False
        try:
            sock = socket.socket(family, socket.SOCK_STREAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                # 用【动态端口】（= 映射观测连接的实际本地端口），而非缓存的
                # self.local_tcp_port。二者必须一致才能与映射共享同一 NAT 映射。
                # 映射端口被占用时会退化为随机端口并登记到全局，此处读回。
                _bindp = _get_dyn_punch_port() or self.local_tcp_port
                if family == socket.AF_INET6:
                    sock.bind(("::", _bindp))
                else:
                    sock.bind(("", _bindp))
                bind_ok = True
            except Exception as be:
                self.log("[打洞] bind %d 失败: %s（继续，走内核随机端口）"
                         % (_get_dyn_punch_port(), be))
            # 非阻塞 connect
            sock.setblocking(False)
            t0 = time.time()
            rc = sock.connect_ex((ip, port))
            if rc == 0:
                # 极少数情况立即成功
                sock.setblocking(True)
                sock.settimeout(None)
                optimize_tcp_socket(sock)   # 与局域网同一入口：缓冲区 + 关 Nagle
                self.log("[打洞] connect 立即成功 %s:%d（bind=%s）" % (ip, port, bind_ok))
                return RmPunchResult(ip, port, sock)
            # EINPROGRESS(115)/WSAEWOULDBLOCK(10035)：等待可写
            try:
                _, wlist, xlist = _select.select([], [sock], [sock],
                                                 self.connect_timeout)
            except Exception as se:
                self.log("[打洞] select 异常 %s:%d -> %s" % (ip, port, se))
                wlist, xlist = [], []
            dt_ms = int((time.time() - t0) * 1000)
            if not wlist and not xlist:
                if self.verbose:
                    self.log("[打洞] connect 超时 %s:%d（%dms, bind=%s, rc=%d）"
                             % (ip, port, dt_ms, bind_ok, rc))
            else:
                err = sock.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
                if err == 0:
                    sock.setblocking(True)
                    sock.settimeout(None)
                    optimize_tcp_socket(sock)   # 与局域网同一入口：缓冲区 + 关 Nagle
                    self.log("[打洞] connect 成功 %s:%d（%dms, bind=%s）"
                             % (ip, port, dt_ms, bind_ok))
                    return RmPunchResult(ip, port, sock)
                # 10060=超时, 10061=拒绝, 10065=无路由, 101=网络不可达
                if self.verbose:
                    self.log("[打洞] connect 失败 %s:%d errno=%d（%dms, bind=%s）"
                             % (ip, port, err, dt_ms, bind_ok))
        except Exception as e:
            if self.verbose:
                self.log("[打洞] connect 异常 %s:%d -> %s" % (ip, port, e))
        if sock:
            try:
                sock.close()
            except Exception:
                pass
        return None


class RmSignalingClient:
    """UDP 信令客户端：加入房间、心跳、接收成员/打洞事件。"""

    def __init__(self, server_ip, server_port, room, name, tcp_port, lan_ips,
                 on_joined=None, on_member_join=None, on_member_leave=None,
                 on_punch_go=None, on_error=None, log=None,
                 server_tcp_port=None, punch_local_port=None, device_id=None,
                 on_udp_hole_ready=None, on_mapping_ready=None, reuse_id=None,
                 password=None):
        self.server_ip = server_ip
        self.server_port = server_port
        self.server_tcp_port = server_tcp_port or RM_DEFAULT_SERVER_TCP_PORT
        self.room = str(room)
        self.name = name
        # 房间密码（可选）：空串/None = 开放房间。仅存内存，随 join 发送，
        # 网络切换重建时一并重发。
        self.password = str(password) if password else ""
        self.tcp_port = tcp_port
        self.lan_ips = list(lan_ips or [])
        self.device_id = device_id or ""
        # 身份复用：网络切换重建时传入上次的 my_id，服务器据此复用（不换 id），
        # 对端看到的是"同一成员回归"，避免掉线感与状态孤儿化。
        self.reuse_id = str(reuse_id) if reuse_id else ""
        self.punch_local_port = punch_local_port
        # NTP 式时钟校准：clock_offset_ms = 服务器时间 - 本地时间
        # 用于 wait_until，使两端不依赖本地时钟是否同步
        self.clock_offset_ms = 0
        self.on_joined = on_joined
        self.on_member_join = on_member_join
        self.on_member_leave = on_member_leave
        self.on_punch_go = on_punch_go
        self.on_error = on_error
        self.on_udp_hole_ready = on_udp_hole_ready   # 回调(peer_id, peer_addr)
        self.on_mapping_ready = on_mapping_ready     # P2 #6: TCP 映射就绪回调
        self.log = log or (lambda msg: None)
        self.my_id = None
        self._joined_members = []
        self.sock = None
        self.map_sock = None
        self._running = False
        self._recv_thread = None
        self._hb_thread = None
        # 房间级拒绝码（服务器明确回了 error）：NEED_PASSWORD / BAD_PASSWORD /
        # ROOM_OPEN / ROOM_FULL 等。非空表示"服务器可达但拒绝加入"，
        # 区别于"超时无响应"（服务器不可达）。供 start() 区分错误提示。
        self.last_join_error = None
        # UDP 打洞与可靠通道
        self.udp_hole_sock = None
        self._udp_hole_thread = None
        self._udp_rtp_handlers = {}
        self._udp_rtp_lock = threading.Lock()
        self._udp_recv_running = False
        self._udp_recv_thread = None
        self._hole_targets = {}
        self._hole_targets_lock = threading.Lock()

    def start(self):
        if self._running:
            return False
        # 按服务器地址族自适应（支持 IPv6 服务器）
        fam = socket.AF_INET6 if ":" in self.server_ip else socket.AF_INET
        self.sock = socket.socket(fam, socket.SOCK_DGRAM)
        self.sock.settimeout(RM_RECV_TIMEOUT)
        self._running = True
        self.last_join_error = None
        self._send_join()
        if not self._wait_joined():
            self._running = False
            if self.last_join_error:
                # 服务器可达，但明确拒绝了加入（密码错/房间满等）。
                # 错误详情已由 on_error 回调上报，这里【不再】误报"无法连接"，
                # 也【不再】发 CONNECT_FAILED（那会让 UI 提示去查网络，误导用户）。
                code = self.last_join_error
                _hint = {
                    "NEED_PASSWORD": "该房间需要密码，请填写房间密码后重试",
                    "BAD_PASSWORD": "房间密码错误，请检查后重试",
                    "ROOM_OPEN": "该房间是开放房间（无密码），请清空密码栏后重试",
                    "ROOM_FULL": "房间人数已满",
                }.get(code, "服务器拒绝了加入请求")
                self.log("[信令] 加入房间被拒绝（%s）：%s" % (code, _hint))
            else:
                # 真正连不上：超时无响应
                err = ("[信令] 无法连接信令服务器 %s:%d（10 秒内无响应）。"
                       "请检查：服务器是否已启动、地址是否正确、"
                       "防火墙是否放行 UDP %d。"
                       "提示：若服务器与本机在同一台机器/同一局域网，"
                       "请填局域网 IP（如 127.0.0.1 或服务器的局域网地址），"
                       "不要填公网 IP（多数路由器不支持从内网访问自己的公网 IP）。"
                       % (self.server_ip, self.server_port, self.server_port))
                self.log(err)
                if self.on_error:
                    try:
                        self.on_error("CONNECT_FAILED")
                    except Exception:
                        pass
            try:
                self.sock.close()
            except Exception:
                pass
            return False
        if self.my_id:
            # 先做时间同步（NTP 式），再建映射、开 UDP 打洞 socket、开始打洞
            self._sync_time()
            # 映射建立放到后台线程【持续重试】：映射是打洞靶子，失败/慢会让
            # 对端一直收不到 pub_tcp。不能阻塞 start()，否则 recv_thread 不启动、
            # 收不到后续 punch_go / member_update。
            threading.Thread(target=self._open_mapping, daemon=True).start()
            self._open_udp_hole_socket()
            time.sleep(0.2)
        if self.on_joined:
            self.on_joined(self._joined_members)
        self._recv_thread = threading.Thread(target=self._recv_loop, daemon=True)
        self._recv_thread.start()
        self._hb_thread = threading.Thread(target=self._hb_loop, daemon=True)
        self._hb_thread.start()
        self.log("[信令] 已加入房间 %s，我的ID=%s" % (self.room, self.my_id))
        # 异步执行 NAT 类型探测（只打日志，不阻塞信令）
        threading.Thread(target=self._run_nat_probe, daemon=True).start()
        return True

    def _run_nat_probe(self):
        """加入房间后异步探测本机 NAT 类型并写入日志。"""
        try:
            r = rm_detect_nat_type(self.server_ip, self.server_port,
                                   RM_NAT_PROBE_PORT, timeout=1.5,
                                   log=self.log)
            t = r.get("type")
            names = {"cone": "锥形 NAT（Cone）",
                     "symmetric": "对称 NAT（Symmetric）",
                     "no_udp": "UDP 被封堵（无法探测）",
                     "unknown": "未知（仅收到一路回应）"}
            self.log("[NAT探测] 本机 NAT 类型 = %s" % names.get(t, t))
            self.log("[NAT探测] 探测 1（UDP %d）观察到: %s"
                     % (self.server_port, r.get("primary")))
            self.log("[NAT探测] 探测 2（UDP %d）观察到: %s"
                     % (RM_NAT_PROBE_PORT, r.get("alt")))
            if t == "symmetric":
                self.log("[NAT探测] 提示：本机为对称 NAT，TCP 打洞成功率极低。")
            elif t == "cone":
                self.log("[NAT探测] 提示：本机为锥形 NAT，打洞可行性较高。")
        except Exception as e:
            self.log("[NAT探测] 异常: %s" % e)

    def release_map_socket(self):
        """时序独占打洞：释放映射观测 socket。

        打洞要求本地端口上【只有一个 socket】，否则入站 SYN 可能被映射
        socket（ESTABLISHED）抢走而丢弃（Windows 实测）。映射 socket 已完成
        登记 pub_tcp 的使命，可安全关闭：锥形 NAT 下同一本地端口的 TCP 映射
        保留数分钟，服务器也配置了延迟清理（180s），pub_tcp 仍有效。
        """
        s = self.map_sock
        if s is None:
            return
        try:
            s.close()
        except Exception:
            pass
        self.map_sock = None
        self.log("[信令] 映射 socket 已关闭（释放本地端口给打洞独占）")

    def stop(self, quiet=False):
        """停止信令客户端。

        quiet=True：不发 BYE（用于网络切换重建）——让服务器保留本成员条目，
        对端不收到 member_leave，重建后复用同一 id 回归，实现"无感重建"。
        """
        if not self._running:
            return
        self._running = False
        if not quiet:
            try:
                if self.my_id:
                    self._send({"type": RM_T_BYE, "ver": RM_VER, "id": self.my_id})
            except Exception:
                pass
        try:
            if self.map_sock:
                self.map_sock.close()
        except Exception:
            pass
        # 关闭 UDP 打洞 socket 与接收循环
        self._udp_recv_running = False
        try:
            if self.udp_hole_sock:
                self.udp_hole_sock.close()
        except Exception:
            pass
        try:
            if self.sock:
                self.sock.close()
        except Exception:
            pass

    # ==================== UDP 打洞与可靠通道 ====================

    def _open_udp_hole_socket(self):
        """打开 UDP 打洞专用 socket（本地 9996），并向服务器登记公网映射。"""
        try:
            fam = socket.AF_INET6 if ":" in self.server_ip else socket.AF_INET
            self.udp_hole_sock = socket.socket(fam, socket.SOCK_DGRAM)
            self.udp_hole_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            if fam == socket.AF_INET6:
                self.udp_hole_sock.bind(("::", RM_UDP_PORT))
            else:
                self.udp_hole_sock.bind(("", RM_UDP_PORT))
            self.udp_hole_sock.settimeout(1.0)
            # 必须【从 udp_hole_sock 本身】发 hello，服务器 recvfrom 看到的
            # 源端口才是 9996 的公网映射。
            hello = json.dumps({"type": RM_T_UDP_HELLO, "ver": RM_VER,
                                "id": self.my_id}).encode("utf-8")
            self.udp_hole_sock.sendto(hello, (self.server_ip, self.server_port))
            self.log("[UDP打洞] 打洞 socket 已绑定本地 %d，已向服务器登记映射" % RM_UDP_PORT)
            # 启动全局 UDP 接收循环（仅一次）
            if not self._udp_recv_running:
                self._udp_recv_running = True
                self._udp_recv_thread = threading.Thread(
                    target=self._udp_global_recv_loop, daemon=True)
                self._udp_recv_thread.start()
            # 立即重新 requestPunch：让服务器用刚登记的公网映射重新下发 punch_go
            for m in self._joined_members:
                mid = m.get("id")
                if mid:
                    try:
                        self._send({"type": RM_T_PUNCH_REQ, "ver": RM_VER,
                                    "id": self.my_id, "target": mid})
                    except Exception:
                        pass
        except Exception as e:
            self.log("[UDP打洞] socket 打开失败: %s" % e)
            self.udp_hole_sock = None

    def send_udp_to(self, peer_addr, data):
        """向指定对端地址发送一个 UDP 包（UDP-RTP 发送入口）。"""
        try:
            if self.udp_hole_sock:
                self.udp_hole_sock.sendto(data, peer_addr)
        except Exception as e:
            self.log("[UDP-RTP] send_udp_to 失败: %s" % e)

    def register_udp_rtp(self, peer_key, on_packet):
        """注册某对端地址的 RTP 处理回调。peer_key 为 'ip:port' 字符串。"""
        with self._udp_rtp_lock:
            self._udp_rtp_handlers[peer_key] = on_packet

    def unregister_udp_rtp(self, peer_key):
        with self._udp_rtp_lock:
            self._udp_rtp_handlers.pop(peer_key, None)

    def _udp_global_recv_loop(self):
        """全局 UDP 接收循环：从 udp_hole_sock 收包，按源地址分发。

        处理两类包：
          1) P2P_UDP_HOLE —— 打洞探测；若来自 _hole_targets 里的地址，判定打通
          2) UDP-RTP 数据 —— 按源地址分发给对应 handler
        """
        self.log("[UDP-RTP] 全局接收循环启动")
        buf = bytearray(2048)
        while self._udp_recv_running and self.udp_hole_sock:
            try:
                self.udp_hole_sock.settimeout(0.5)
                n, addr = self.udp_hole_sock.recvfrom_into(buf, len(buf))
            except socket.timeout:
                continue
            except Exception:
                break
            if n <= 0:
                continue
            data = bytes(buf[:n])
            key = "%s:%d" % (addr[0], addr[1])
            # 1) 打洞探测包
            if data == b"P2P_UDP_HOLE":
                with self._hole_targets_lock:
                    peer_id = self._hole_targets.get(key)
                if peer_id is not None:
                    with self._udp_rtp_lock:
                        already = key in self._udp_rtp_handlers
                    if not already:
                        self.log("[UDP打洞] ★ 成功！收到 %s 的探测包（peer=%s）"
                                 % (key, peer_id))
                        if self.on_udp_hole_ready:
                            try:
                                self.on_udp_hole_ready(peer_id, addr)
                            except Exception as ex:
                                self.log("[UDP打洞] 回调异常: %s" % ex)
                continue
            # 2) RTP 数据包
            with self._udp_rtp_lock:
                handler = self._udp_rtp_handlers.get(key)
            if handler:
                try:
                    handler(data)
                except Exception as e:
                    self.log("[UDP-RTP] 处理包异常: %s" % e)
        self.log("[UDP-RTP] 全局接收循环退出")

    def _start_udp_hole(self, peer):
        """收到 punch_go 后，启动 UDP 打洞探测：向对端 pub_udp 持续发包（只发不收）。"""
        if self.udp_hole_sock is None:
            self.log("[UDP打洞] socket 未打开，跳过")
            return
        peer_id = peer.get("id")
        pub_udp = peer.get("pub_udp", "")
        parsed = rm_parse_host_port(pub_udp)
        if not parsed:
            self.log("[UDP打洞] 对端 pub_udp 无效（%s），无法打洞" % pub_udp)
            return
        ip, port = parsed
        key = "%s:%d" % (ip, port)
        with self._hole_targets_lock:
            self._hole_targets[key] = peer_id

        def _probe():
            t_end = time.time() + RM_UDP_PROBE_DURATION
            sent = 0
            self.log("[UDP打洞] 开始向 %s 发探测包（%.1f 秒）"
                     % (key, RM_UDP_PROBE_DURATION))
            while time.time() < t_end:
                if not self._running:
                    break
                try:
                    self.udp_hole_sock.sendto(b"P2P_UDP_HOLE", (ip, port))
                    sent += 1
                except Exception as e:
                    self.log("[UDP打洞] 发送失败: %s" % e)
                    break
                time.sleep(RM_UDP_PROBE_INTERVAL)
            with self._hole_targets_lock:
                still_waiting = self._hole_targets.get(key) == peer_id
            with self._udp_rtp_lock:
                has_handler = key in self._udp_rtp_handlers
            if still_waiting and not has_handler:
                with self._hole_targets_lock:
                    self._hole_targets.pop(key, None)
                self.log("[UDP打洞] 失败：发了 %d 个包，未收到 %s 响应（peer=%s）"
                         % (sent, key, peer_id))

        self._udp_hole_thread = threading.Thread(target=_probe, daemon=True)
        self._udp_hole_thread.start()

    def request_punch(self, target_id):
        self._send({"type": RM_T_PUNCH_REQ, "ver": RM_VER,
                    "id": self.my_id, "target": target_id})

    def _send(self, obj):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.sock.sendto(data, (self.server_ip, self.server_port))

    def _send_join(self):
        msg = {
            "type": RM_T_JOIN, "ver": RM_VER, "room": self.room,
            "name": self.name, "tcp": self.tcp_port, "lan": self.lan_ips,
            "did": self.device_id,
            "can_listen": True,   # Windows 能 listen+映射同端口
        }
        # 网络重建时携带旧 id，服务器复用之（协议向后兼容：旧服务器忽略该字段）
        if self.reuse_id:
            msg["reuse_id"] = self.reuse_id
        # 房间密码：仅在【有密码时】才带该字段。无密码时不发 pwd，
        # 报文与旧版完全一致 → 开放房间零兼容风险。
        if self.password:
            msg["pwd"] = self.password
        self._send(msg)

    def _sync_time(self):
        """NTP 式时间同步（单次往返，4 次采样取最小 RTT 的那次）。

        t1: 客户端发送时刻（本地）
        t2: 服务器接收时刻（服务器）
        t3: 客户端收到回包时刻（本地）
        offset = t2 - (t1 + t3) / 2   （服务器时间 - 本地时间）

        完成后把 offset 写入模块级 _RM_CLOCK_OFFSET_MS，供打洞时使用。
        注意：本方法在 start() 中被调用，此时收包线程尚未启动，
        因此同步 recvfrom 是安全的（见 start 中的调用位置）。
        """
        global _RM_CLOCK_OFFSET_MS
        import time as _t
        best_rtt = None
        best_offset = 0
        # 临时保存旧超时，方法结束后恢复
        old_timeout = None
        try:
            old_timeout = self.sock.gettimeout()
        except Exception:
            pass
        for _ in range(4):
            try:
                t1 = int(_t.time() * 1000)
                self._send({"type": RM_T_TIME_REQ, "ver": RM_VER, "t1": t1})
                self.sock.settimeout(1.0)
                deadline = _t.time() + 1.0
                reply = None
                while _t.time() < deadline:
                    try:
                        data, _addr = self.sock.recvfrom(65535)
                    except socket.timeout:
                        break
                    try:
                        msg = json.loads(data.decode("utf-8"))
                    except Exception:
                        continue
                    # 只认时间同步回包（t1 匹配），其他消息丢弃
                    if msg.get("type") == RM_T_TIME_REPLY and msg.get("t1") == t1:
                        reply = msg
                        break
                if reply is None:
                    continue
                t3 = int(_t.time() * 1000)
                t2 = int(reply.get("t2", 0))
                rtt = t3 - t1
                offset = t2 - (t1 + t3) // 2
                if best_rtt is None or rtt < best_rtt:
                    best_rtt = rtt
                    best_offset = offset
                _t.sleep(0.05)
            except Exception as e:
                self.log("[校时] 采样失败: %s" % e)
                break
        # 恢复原超时
        try:
            if old_timeout is None:
                self.sock.settimeout(RM_RECV_TIMEOUT)
            else:
                self.sock.settimeout(old_timeout)
        except Exception:
            pass
        if best_rtt is not None:
            _RM_CLOCK_OFFSET_MS = best_offset
            self.clock_offset_ms = best_offset
            self.log("[校时] 与服务器时钟偏差 = %d ms（最小 RTT %d ms）"
                     % (best_offset, best_rtt))
        else:
            self.log("[校时] 未能同步，使用本地时钟（可能影响打洞时刻）")

    def _open_mapping(self):
        fam = socket.AF_INET6 if ":" in self.server_ip else socket.AF_INET
        # 先释放上一次会话残留的映射 socket：Windows 上 SO_REUSEADDR 语义较严，
        # 若旧 socket 未关就重绑 9998，易报 WinError 10048（端口占用）。
        if self.map_sock is not None:
            try:
                self.map_sock.close()
            except Exception:
                pass
            self.map_sock = None
        # 优先用固定端口（默认 9995）；若被占用（Windows TIME_WAIT 未释放，
        # 报 10048），退化为随机端口——但【必须】把随机端口登记到全局，
        # 让 TCP 打洞 socket 用同一端口 bind，才能共享同一 NAT 映射。
        want_port = self.punch_local_port or 0
        bind_port = want_port
        attempt = 0
        # 持续重试：映射是打洞靶子，失败/慢会让对端一直收不到 pub_tcp。
        # 只要信令还在运行就不断重试（间隔递增，上限 3s），直到成功。
        while self._running:
            attempt += 1
            s = None
            try:
                s = socket.socket(fam, socket.SOCK_STREAM)
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                # SO_LINGER(onoff=1, linger=0)：close() 时发 RST 而非正常四次挥手，
                # 【跳过 TIME_WAIT】——根治"反复进出房间导致端口被自己上次连接
                # 的 TIME_WAIT 占用（WinError 10048）"。映射连接只发过一次注册 id，
                # 无待传业务数据，RST 不丢东西，对服务器也无害。
                try:
                    s.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER,
                                 struct.pack("ii", 1, 0))
                except Exception:
                    pass
                bind_addr = ("::", bind_port) if fam == socket.AF_INET6 else ("", bind_port)
                s.bind(bind_addr)
                actual_port = s.getsockname()[1]
                s.connect((self.server_ip, self.server_tcp_port))
                mid = self.my_id.encode("utf-8")
                s.sendall(struct.pack("!H", len(mid)) + mid)
                self.map_sock = s
                # 登记实际端口（打洞 socket 须用同一端口）
                _set_dyn_punch_port(actual_port)
                if actual_port != want_port:
                    self.log("[信令] TCP 映射观测连接已建立（本地端口=%s，原 %s 被占用，改用随机端口）"
                             % (actual_port, want_port))
                else:
                    self.log("[信令] TCP 映射观测连接已建立（本地端口=%s）" % actual_port)
                # P2 #6: 通知上层 TCP 映射已就绪，可广播 TCP_READY
                if self.on_mapping_ready:
                    try:
                        self.on_mapping_ready()
                    except Exception as e:
                        self.log("[信令] on_mapping_ready 回调异常: %s" % e)
                return
            except Exception as e:
                if s:
                    try: s.close()
                    except Exception: pass
                # 固定端口被占用 → 退化为随机端口（一次性），继续重试
                if want_port and _is_addr_in_use(e):
                    self.log("[信令] 本地端口 %s 被占用（%s），退化为随机端口"
                             % (want_port, e))
                    bind_port = 0
                    want_port = 0
                    continue
                # 其它错误：退避后持续重试（间隔递增，上限 3s）
                backoff = min(0.5 * attempt, 3.0)
                self.log("[信令] TCP 映射观测连接失败（第 %d 次，%.1fs 后重试）: %s"
                         % (attempt, backoff, e))
                time.sleep(backoff)

    def _wait_joined(self):
        deadline = time.time() + 10
        while time.time() < deadline and self._running:
            try:
                data, _ = self.sock.recvfrom(65535)
            except socket.timeout:
                self._send_join()
                continue
            try:
                msg = json.loads(data.decode("utf-8"))
            except Exception:
                continue
            t = msg.get("type")
            if t == RM_T_JOINED:
                self.my_id = msg.get("id")
                self._joined_members = msg.get("members", [])
                # 房间是否开放（服务器 ver>=2 才带此字段）：
                # 有密码但服务器却回开放 → 说明密码未生效，上层据此提示用户。
                self.room_open = msg.get("room_open", None)
                self.server_ver = msg.get("ver", 1)
                return True
            elif t == RM_T_ERROR:
                code = msg.get("code", "UNKNOWN")
                # 记录房间级拒绝码：供 start() 区分"服务器明确拒绝"与"超时不可达"
                self.last_join_error = code
                if self.on_error:
                    self.on_error(code)
                self._running = False
                return False
        return False

    def _recv_loop(self):
        while self._running:
            try:
                data, _ = self.sock.recvfrom(65535)
            except socket.timeout:
                continue
            except Exception:
                break
            try:
                msg = json.loads(data.decode("utf-8"))
            except Exception:
                continue
            t = msg.get("type")
            if t == RM_T_MEMBER_JOIN:
                if self.on_member_join:
                    self.on_member_join(msg.get("member", {}))
            elif t == RM_T_MEMBER_UPDATE:
                # 成员信息更新（如对端 TCP 映射登记完成）：走 on_member_join
                # 路径（_add_member 更新信息 + 触发打洞），拿到刚登记的 pub_tcp。
                if self.on_member_join:
                    self.on_member_join(msg.get("member", {}))
            elif t == RM_T_MEMBER_LEAVE:
                if self.on_member_leave:
                    self.on_member_leave(msg.get("id"))
            elif t == RM_T_PUNCH_GO:
                peer = msg.get("peer", {})
                # UDP 打洞探测（与 TCP 打洞并行，独立通道）
                try:
                    self._start_udp_hole(peer)
                except Exception as e:
                    self.log("[UDP打洞] 启动异常: %s" % e)
                if self.on_punch_go:
                    self.on_punch_go(peer, msg.get("at", 0))
            elif t == RM_T_ERROR:
                if self.on_error:
                    self.on_error(msg.get("code", "UNKNOWN"))

    def _hb_loop(self):
        while self._running:
            time.sleep(RM_HEARTBEAT_INTERVAL)
            if not self._running:
                break
            try:
                self._send({"type": RM_T_HB, "ver": RM_VER, "id": self.my_id})
            except Exception:
                pass


class RmConn:
    """单条到某成员的长连接。io_lock 串行化收发。"""

    __slots__ = ("state", "sock", "addr", "member", "io_lock", "created_at",
                 "preferred")

    def __init__(self, member):
        self.state = RM_STATE_CONNECTING
        self.sock = None
        self.addr = None
        self.member = member
        self.io_lock = threading.Lock()
        # B：建连时间戳 —— 保活宽限期内不判死，避免刚连上被误杀
        self.created_at = time.time()
        # 是否为"约定主连接"（去重用）：同时打开可能形成两条独立 TCP
        # 连接（各方向一条），两端各用一条 → 半开。用 ID 规则约定只保留
        # 一条：ID 大者的出站连接 = 主；ID 小者的入站连接 = 主。
        self.preferred = True


# ==================== 梯度冗余多路径调度（叠加层） ====================
# 设计：不改变现有 connections/udp_conns 存储，仅在其上叠加"路径角色 +
# 保活调度"决策层。每个 peer 一个 PeerPathScheduler，管理该 peer 的多条路径。
#
# 角色：hot(热备，传数据) / warm_safe(保守暖备) / warm_loose(宽松暖备)
# 保活：软性探测（指数增长逼近 NAT 超时）+ 硬性下限兜底

RM_PROTO_FLOOR = {"tcp": 30, "udp": 15}
RM_PROTO_SAFE_CAP = {"tcp": 3600, "udp": 60}
RM_ROLE_FACTOR = {"hot": 1.0, "warm_safe": 1.5, "warm_loose": 4.0}


class RmKeepaliveScheduler:
    """单条路径的保活间隔调度（软性探测 + 硬性下限）。"""

    def __init__(self, role, proto):
        self.role = role
        self.proto = proto
        self.floor = RM_PROTO_FLOOR.get(proto, 15)
        self.safe_cap = RM_PROTO_SAFE_CAP.get(proto, 60)
        self.current_interval = self.floor * 2
        self.upper_bound = None
        self.lower_bound = None
        self.success_count = 0
        self.fail_count = 0
        self.stable = False

    def _clamp(self, v):
        v = max(v, self.floor)
        if self.role == "warm_safe":
            v = min(v, self.safe_cap)
        else:
            v = min(v, 3600)
        return int(v)

    def on_success(self):
        self.success_count += 1
        self.upper_bound = self.current_interval
        if self.stable:
            return
        total = self.success_count + self.fail_count
        if total >= 5 and self.fail_count / (total + 1) > 0.05:
            self.stable = True
            return
        # C 修复：纯成功（无任何失败样本）时，间隔不应无限翻倍到 3600s。
        # 没有失败样本就无从"逼近 NAT 超时边界"，继续翻倍只会让保活越来越稀疏，
        # 反而拖慢断线感知。此处封顶为当前角色的探测上限：
        #   · hot 需要最快感知断线 → 保持 floor*2（不增长）
        #   · warm_safe/warm_loose 允许适度增长，但不超过各自钳制上限
        if self.fail_count == 0:
            cap = self.floor * 2 if self.role == "hot" else self.safe_cap
            self.current_interval = self._clamp(min(self.current_interval * 2, cap))
            return
        c = int(self.current_interval * 2 * RM_ROLE_FACTOR.get(self.role, 1.0))
        if self.lower_bound is not None:
            c = min(c, (self.upper_bound + self.lower_bound) // 2)
            self.stable = True
        self.current_interval = self._clamp(c)

    def on_failure(self):
        self.fail_count += 1
        self.lower_bound = self.current_interval
        self.stable = False
        if self.upper_bound is not None:
            self.current_interval = (self.upper_bound + self.lower_bound) // 2
        else:
            self.current_interval = self.floor
        self.current_interval = self._clamp(self.current_interval)
        if self.fail_count >= 3:
            self.current_interval = self.floor


# 协议优先级：数字越小越优先当 hot。
# TCP > UDP-RTP（TCP 有内核拥塞控制、NAT 映射超时更长、丢包处理更成熟）。
RM_PROTO_PRIORITY = {"tcp": 0, "udp": 1}
# 测试开关：True = 只允许 TCP 通道（UDP-RTP 不参与发送）。
# 用于验证"TCP 打洞是否真的可用"——若 TCP 不可用则发送【明确失败】，
# 不会被 UDP-RTP 静默回退掩盖。生产环境应为 False（允许 UDP 兜底）。
RM_TCP_ONLY = False


class RmPath:
    """单条路径的运行时状态。"""

    __slots__ = ("path_id", "proto", "role", "scheduler", "last_seen", "rtt_ms")

    def __init__(self, path_id, proto, role, rtt_ms=0):
        self.path_id = path_id
        self.proto = proto
        self.role = role
        self.scheduler = RmKeepaliveScheduler(role, proto)
        self.last_seen = time.time()
        self.rtt_ms = rtt_ms

    def set_role(self, role):
        """改角色并同步重建 scheduler（保活参数随角色变化）。"""
        self.role = role
        self.scheduler = RmKeepaliveScheduler(role, self.proto)


class RmPeerPathScheduler:
    """单个 peer 的路径分级与保活决策。

    路径来源：
      · TCP 打洞成功 → 一条 "tcp" 路径
      · UDP 打洞成功 → 一条 "udp" 路径
    按 RTT 分级（最小→hot，次小→warm_safe，其余→warm_loose）。
    """

    def __init__(self, peer_id, log=None):
        self.peer_id = peer_id
        self.log = log or (lambda m: None)
        self.lock = threading.Lock()
        self.paths = {}      # {path_id: RmPath}

    def register(self, path_id, proto, rtt_ms):
        """新路径接入（TCP/UDP 打洞成功时调用）。"""
        with self.lock:
            existing = self.paths.get(path_id)
            # 已存在且【未失效】→ 直接复用
            if existing is not None and existing.role != "dead":
                return existing
            # 已存在但已 dead（如 TCP 重连）→ 重建对象。
            # 新路径先以 "warm_loose" 占位，再由 _regrade_locked 按
            # (协议优先级, RTT) 重新分级；不能用 "dead" 占位，否则会被
            # _regrade_locked 的 alive 过滤掉、永不上位（多路径调度失效）。
            p = RmPath(path_id, proto, "warm_loose", rtt_ms)
            self.paths[path_id] = p
            self._regrade_locked()
        self.log("[路径] peer=%s 注册 %s/%s rtt=%sms role=%s"
                 % (self.peer_id, path_id, proto, rtt_ms, p.role))
        return p

    def _regrade_locked(self):
        """按协议优先级（TCP > UDP）重排所有存活路径的角色。

        规则：存活路径按 (协议优先级, rtt) 排序，依次分配
        hot → warm_safe → warm_loose；多余路径保持 dead。
        这样 TCP 一旦可用，就会抢占 hot，让最强的链路传数据。
        """
        alive = [p for p in self.paths.values() if p.role != "dead"
                 or p.scheduler is not None]
        # 只对"存活"路径重排：此处以 scheduler 存在且非显式 dead 判断
        alive = [p for p in self.paths.values() if p.role != "dead"]
        alive.sort(key=lambda x: (RM_PROTO_PRIORITY.get(x.proto, 9), x.rtt_ms))
        roles = ["hot", "warm_safe", "warm_loose"]
        for i, p in enumerate(alive):
            new_role = roles[i] if i < len(roles) else "warm_loose"
            if p.role != new_role:
                p.set_role(new_role)

    def regrade(self):
        """外部可调用的重排（如新增路径后）。"""
        with self.lock:
            self._regrade_locked()

    def remove(self, path_id):
        with self.lock:
            self.paths.pop(path_id, None)

    def get_by_role(self, role):
        with self.lock:
            for p in self.paths.values():
                if p.role == role:
                    return p
        return None

    def get_hot(self):
        return self.get_by_role("hot")

    def promote_on_hot_failure(self):
        """热备失效 → 保守暖备上位，宽松暖备升保守。返回新的热备 path_id。"""
        with self.lock:
            hot = None
            safe = None
            loose = None
            for p in self.paths.values():
                if p.role == "hot":
                    hot = p
                elif p.role == "warm_safe":
                    safe = p
                elif p.role == "warm_loose":
                    loose = p
            if hot:
                hot.role = "dead"
            new_hot = None
            if safe:
                safe.set_role("hot")
                new_hot = safe.path_id
            if loose:
                loose.set_role("warm_safe")
        return new_hot

    def role_of(self, path_id):
        with self.lock:
            p = self.paths.get(path_id)
            return p.role if p else None


class RmRoomManager:
    """房间连接管理：全连接 + 长连接 + 入站接管 + 保活。"""

    def __init__(self, signaling, local_tcp_port, log=None):
        self.signaling = signaling
        self.local_tcp_port = local_tcp_port
        self.log = log or (lambda msg: None)
        self.lock = threading.Lock()
        self.members = {}
        self.connections = {}
        self.udp_conns = {}    # {peer_id: UdpReliableSocket}
        self._punch_sem = threading.Semaphore(RM_PUNCH_CONCURRENCY)
        self._puncher = RmHolePuncher(local_tcp_port, log=self.log)
        self._running = False
        self._keepalive_thread = None
        self.on_socket_ready = None
        self.on_udp_ready = None   # 回调(peer_id, UdpReliableSocket)
        self.on_state_changed = None   # 回调：成员状态变化时触发 UI 刷新
        # 自动重建冷却：{peer_id: last_rebuild_ts}
        # 避免同一 peer 短时间内重复触发打洞（失联检测 + keepalive 可能同时触发）
        self._rebuild_cooldown = {}
        self._rebuild_cooldown_lock = threading.Lock()
        self._rebuild_cooldown_sec = 30.0
        # P2: 打洞轮次对齐（对端下发 PUNCH_ROUND → 存入，供 _punch_task 等待）
        self._punch_round_cond = threading.Condition(threading.Lock())
        self._punch_round_evt = {}   # {peer_id: (round_no, t_go_r)}
        # P2 #6: 本机 TCP 映射是否就绪
        self._mapping_ready = False
        # C/S 降级：房间端口监听状态查询（由 P2PApp 注入）。
        # 未监听时 is_punch_initiator 一律返回 True（主动 connect）。
        self.room_port_bound_checker = None
        # 梯度冗余多路径调度：{peer_id: RmPeerPathScheduler}
        self._path_schedulers = {}
        # 打洞并发计数：{peer_id: int} —— 同一 peer 最多 2 个 _punch_task
        self._punch_active = {}
        # SYNC 精准打洞计划：{peer_id: t_go(本机毫秒)}。
        # 一旦收到 SYNC_COMMIT（进入精准打洞模式），记录 t_go；
        # _punch_task 检测到该 peer 有精准计划时【主动让路】，避免与
        # PUNCH_ROUND 驱动的重试风暴抢执行权、错过对齐时刻（导致半开）。
        self._sync_plan = {}
        # 「等待精准打洞」门闩：{peer_id: 加入时刻(秒)}。
        # UDP-RTP 就绪后加入；精准打洞结束（成功/失败）后移除。
        # 在此期间【任何】风暴触发（含 punch_go）都让路——
        # 保证"先精准打洞，失败才风暴"的严格顺序。
        # 超时（RM_SYNC_AWAIT_TIMEOUT）后自动失效，防止对端不支持
        # SYNC（旧版本/包丢失）时永久阻塞，兜底风暴仍能启动。
        self._awaiting_sync = {}
        # 连接池：{peer_id: 最后活跃时间戳} —— 用于空闲降频保活 / LRU 清理
        self._peer_last_active = {}
        # 接收代际：{peer_key: int} —— 新通道接管时递增，旧接收循环据此退出，
        # 避免换路时新旧两个接收循环同时写盘（竞态）
        self._recv_epoch = {}
        # B：TCP 保活连续失败计数 {peer_id: int}
        self._tcp_alive_fail = {}
        # TCP 心跳 RTT 探测：{peer_id: 上次发 PING 时刻}。
        # 仅在空闲（_peer_last_active 距今 >= RM_TCP_PING_IDLE）时才发，
        # 避免干扰文件数据传输（传输中 io_lock 被占，acquire(False) 失败即跳过）。
        self._tcp_last_ping_at = {}

    def _get_path_sched(self, peer_id):
        """取（或创建）某 peer 的路径调度器。"""
        with self.lock:
            s = self._path_schedulers.get(peer_id)
            if s is None:
                s = RmPeerPathScheduler(peer_id, log=self.log)
                self._path_schedulers[peer_id] = s
            return s

    def mark_mapping_ready(self):
        """P2 #6: TCP 映射就绪 → 向所有已有 UDP 通道广播，后续新建通道也会发。"""
        self._mapping_ready = True
        self.broadcast_tcp_ready()

    def start(self):
        self._running = True
        self._keepalive_thread = threading.Thread(target=self._keepalive_loop, daemon=True)
        self._keepalive_thread.start()

    def stop(self):
        self._running = False
        with self.lock:
            for conn in self.connections.values():
                self._close_sock(conn)
            self.connections.clear()
            for rtp in self.udp_conns.values():
                try:
                    rtp.close()
                except Exception:
                    pass
            self.udp_conns.clear()

    def on_udp_hole_ready(self, peer_id, peer_addr):
        """UDP 打洞成功：建立可靠 UDP 通道（共享 socket 模式）。"""
        sig = self.signaling
        if sig is None:
            self.log("[UDP-RTP] signaling 为空，跳过")
            return
        with self.lock:
            if peer_id in self.udp_conns:
                return
        try:
            from udp_reliable import UdpReliableSocket
            def _send(addr, data):
                sig.send_udp_to(addr, data)
            # P2 #4: 记录对端 UDP 公网地址，供 TCP 打洞预测端口
            try:
                self._puncher.set_udp_hint(peer_id, peer_addr[0], peer_addr[1])
            except Exception:
                pass
            def _on_dead(addr):
                self._handle_udp_peer_dead(peer_id)
            def _on_sync(t_go):
                # P1: SYNC 完成 → 到 t_go 精准 TCP 打洞
                # P2 #9: 用 SYNC RTT 自适应 connect 超时
                try:
                    rtt = rtp.get_sync_rtt()
                    if rtt:
                        self._puncher.set_adaptive_timeout(rtt)
                except Exception:
                    pass
                self._on_sync_ready(peer_id, t_go)
            def _on_round(round_no, t_go_r):
                # P2: 主导方下发下一轮打洞时刻
                self._on_punch_round(peer_id, round_no, t_go_r)
            def _on_tcp_ready():
                # P2 #6: 对端 TCP 映射就绪 → 立即触发一次打洞
                self._on_peer_tcp_ready(peer_id)
            def _on_punch_fail():
                # P2 #8: 对端放弃 TCP → 停止本端空等
                self._on_peer_punch_fail(peer_id)
            rtp = UdpReliableSocket(_send, peer_addr, log=self.log,
                                     on_peer_dead=_on_dead,
                                     on_sync_ready=_on_sync,
                                     on_punch_round=_on_round,
                                     on_tcp_ready=_on_tcp_ready,
                                     on_punch_fail=_on_punch_fail)
        except Exception as e:
            self.log("[UDP-RTP] 创建失败: %s" % e)
            return
        try:
            peer_key = "%s:%d" % (peer_addr[0], peer_addr[1])
            sig.register_udp_rtp(peer_key, rtp.on_packet)
        except Exception as e:
            self.log("[UDP-RTP] 注册接收回调失败: %s" % e)
            return
        with self.lock:
            self.udp_conns[peer_id] = rtp
            # 门闩：UDP-RTP 就绪 → 进入"等待精准打洞"窗口。
            # 窗口内所有风暴触发源（含 punch_go）都让路，优先走 SYNC 精准打洞。
            self._awaiting_sync[peer_id] = time.time()
            member = dict(self.members.get(peer_id, {}))
        self.log("[UDP-RTP] 已为 %s 建立可靠通道 %s"
                 % (member.get("name", peer_id), peer_addr))
        # 梯度冗余：注册 UDP 路径
        try:
            self._get_path_sched(peer_id).register("udp:%s" % peer_id, "udp", 0)
        except Exception:
            pass
        cb = self.on_udp_ready
        if cb:
            try:
                cb(peer_id, rtp)
            except Exception as e:
                self.log("[UDP-RTP] 回调异常: %s" % e)
        # P2 #6: 若本机 TCP 映射已就绪，立即告知对端可开始打洞
        if self._mapping_ready:
            try:
                rtp.send_tcp_ready()
                self.log("[打洞] 新通道通知 TCP_READY -> peer=%s" % peer_id)
            except Exception:
                pass
        # P1: UDP-RTP 通道就绪后，若是 initiator 则启动 SYNC 以精准 TCP 打洞
        try:
            my_id = getattr(sig, "my_id", None) or ""
            if my_id and my_id < peer_id:
                threading.Thread(target=self._maybe_start_sync,
                                 args=(peer_id, rtp), daemon=True).start()
        except Exception as e:
            self.log("[SYNC] 启动判断异常: %s" % e)

    def _maybe_start_sync(self, peer_id, rtp):
        """延迟 100ms 后发起 SYNC（门闩已挡 punch_go 触发的打洞，无需长等）。"""
        try:
            time.sleep(0.1)
            with self.lock:
                conn = self.connections.get(peer_id)
                if conn and conn.state == RM_STATE_CONNECTED:
                    return   # 已经 TCP 连接，不需要 SYNC
            self.log("[SYNC] 作为 initiator 向 peer=%s 发起 SYNC" % peer_id)
            rtp.start_sync()
        except Exception as e:
            self.log("[SYNC] 异常: %s" % e)

    def _is_punch_initiator(self, peer_id):
        """返回 True 表示本端主动 connect（打洞）。

        NAT 穿透铁律：纯 listen 不成立——监听方不发 SYN 则本机 NAT 无映射，
        对方 SYN 被丢弃（pcap 实证）。故双端都必须 connect（各发出站 SYN
        打洞本机 NAT），listen 仅作兜底。

        保留 listen（房间端口监听）+ 入站接管（on_inbound）作为额外兜底。
        """
        return True

    def _outbound_is_preferred(self, peer_id):
        """本端【出站 connect】连接是否为主连接（ID 大者的出站 = 主）。"""
        my_id = getattr(self.signaling, "my_id", None) or ""
        if not my_id or not peer_id:
            return True
        return my_id > peer_id

    def _inbound_is_preferred(self, peer_id):
        """本端【入站 accept】连接是否为主连接（ID 小者的入站 = 主）。"""
        my_id = getattr(self.signaling, "my_id", None) or ""
        if not my_id or not peer_id:
            return True
        return my_id < peer_id

    def _has_reachable_target(self, peer):
        """是否有可达靶子（打洞候选）。

        没靶子不打：
          · pub_tcp 非空 → 有公网 TCP 靶子
          · lan 非空 → 有内网候选（跨网时快速失败，但不至于完全无候选）
        都没有 = 打也白打（无候选可连），放弃本轮、等对端登记映射后重新触发。
        """
        if not peer:
            return False
        if peer.get("pub_tcp"):
            return True
        if peer.get("lan"):
            return True
        return False

    def _on_sync_ready(self, peer_id, t_go):
        """P1: SYNC 完成，到 t_go 时刻精准执行 TCP 同时打开。"""
        with self.lock:
            conn = self.connections.get(peer_id)
            if conn and conn.state == RM_STATE_CONNECTED:
                # 已连接（其他路径已成功）→ 清门闩，避免残留
                self._awaiting_sync.pop(peer_id, None)
                return
            peer = self.members.get(peer_id)
        if not peer:
            return
        # 没靶子不打：无候选可连，精准打洞也无意义（等对端登记映射后重触发）。
        if not self._has_reachable_target(peer):
            self.log("[打洞] peer=%s 无靶子，跳过精准打洞（不打）" % peer_id)
            with self.lock:
                self._sync_plan.pop(peer_id, None)
                self._awaiting_sync.pop(peer_id, None)
            return
        # 登记精准打洞计划：_punch_task 见此即让路（避免抢执行权错过 t_go）
        with self.lock:
            self._sync_plan[peer_id] = t_go
        threading.Thread(target=self._sync_punch_task,
                         args=(peer_id, peer, t_go), daemon=True).start()

    def _sync_punch_task(self, peer_id, peer, t_go):
        """单次精准 TCP 打洞（不重试，因为 SYNC 已对齐时刻）。"""
        # 标准 C/S：ID 小者 listen 等待，不主动 connect。
        if not self._is_punch_initiator(peer_id):
            self.log("[打洞] peer=%s：本端 ID 较小，listen 等待（精准打洞跳过）" % peer_id)
            with self.lock:
                self._sync_plan.pop(peer_id, None)
                self._awaiting_sync.pop(peer_id, None)
            return
        try:
            # at_ms = t_go 是【本机时刻】（由 SYNC 计算），_wait_until 的换算
            # 在 punch 内部用 _RM_CLOCK_OFFSET_MS——但 t_go 已是本机时刻，
            # 故需绕过换算直接等待。
            now_ms = int(time.time() * 1000)
            wait_sec = (t_go - now_ms) / 1000.0
            # NAT 预热必须在 T_go【之前】完成，否则预热耗时(约100ms)会把
            # 实际发 SYN 的时刻推迟到 T_go 之后，错过对端的同时打开窗口。
            # 提前 warmup_lead 秒唤醒，预热完刚好到 T_go。
            warmup_lead = 0.12
            if wait_sec > warmup_lead:
                time.sleep(wait_sec - warmup_lead)
                try:
                    _rtp = self.get_udp_socket(peer_id)
                    if _rtp is not None:
                        _rtp.warm_up_nat(5, 20)
                except Exception:
                    pass
            elif wait_sec > 0:
                time.sleep(min(wait_sec, 3.0))
            # 用 punch_once：到点只发一轮 SYN（真正的同时打开），不跑风暴。
            # 一轮失败 → 回退 _punch_task（6秒风暴）兜底。
            result = self._puncher.punch_once(peer, 0)   # at_ms=0，已 sleep 到点
            if result:
                self._install_socket(peer_id, result)
                self.log("[SYNC] 精准 TCP 打洞成功 peer=%s" % peer_id)
            else:
                # 注意：punch 返回 None 也可能是"打洞期间已被其他路径连接"，
                # 此时不是失败，不打误导性日志。
                with self.lock:
                    _c = self.connections.get(peer_id)
                    already_connected = bool(_c and _c.state == RM_STATE_CONNECTED)
                if already_connected:
                    self.log("[SYNC] 精准打洞期间已由其他路径连接，跳过 peer=%s" % peer_id)
                    with self.lock:
                        self._sync_plan.pop(peer_id, None)
                        self._awaiting_sync.pop(peer_id, None)
                    return
                self.log("[SYNC] 精准 TCP 打洞失败 peer=%s，转入多轮重试" % peer_id)
                # 精准打洞失败：清除计划与门闩，让 _punch_task 恢复（否则被永久抑制）
                with self.lock:
                    self._sync_plan.pop(peer_id, None)
                    self._awaiting_sync.pop(peer_id, None)
                # P2: 单次精准打洞失败后，回落到轮次对齐的多轮重试
                self._punch_task(peer, 0)
                return
            # 精准打洞成功 → 清除计划与门闩
            with self.lock:
                self._sync_plan.pop(peer_id, None)
                self._awaiting_sync.pop(peer_id, None)
        except Exception as e:
            self.log("[SYNC] TCP 打洞异常: %s" % e)

    def get_udp_socket(self, peer_id):
        with self.lock:
            return self.udp_conns.get(peer_id)

    def broadcast_tcp_ready(self):
        """P2 #6: TCP 映射就绪 → 向所有已有 UDP 通道的对端广播 TCP_READY。"""
        with self.lock:
            items = list(self.udp_conns.items())
        if not items:
            return
        for pid, rtp in items:
            try:
                rtp.send_tcp_ready()
                self.log("[打洞] 广播 TCP_READY -> peer=%s" % pid)
            except Exception:
                pass

    def _on_peer_tcp_ready(self, peer_id):
        """P2 #6: 对端 TCP 就绪。

        关键：TCP_READY 的语义是"我 TCP 映射就绪，可以打洞"（可以），
        不是"现在就打"（现在）。若收到即打（at_ms=0，无时刻对齐），会与
        initiator 发起的 SYNC 精确对齐路径【抢同一个本地端口】，把
        6 秒打洞窗口先耗光，等 SYNC 算好 t_go 时窗口已过 → 必然失败。

        新策略：TCP_READY 只当"就绪门"，打洞交给 SYNC 精确对齐：
          - 有 UDP-RTP → 等待 SYNC 协调（两端同 t_go 同时发 SYN）
          - 超过 RM_TCP_READY_SYNC_WAIT 仍未连上 → 回退"直接打洞"
            （回退时也等 RM_TCP_READY_FALLBACK_DELAY，让两端发出时刻
             误差 ≈ RTT/2，优于立即打的随机错开）
        """
        with self.lock:
            peer = self.members.get(peer_id)
            conn = self.connections.get(peer_id)
            if conn and conn.state in (RM_STATE_CONNECTING, RM_STATE_CONNECTED):
                return
            if not peer:
                return
            self.connections[peer_id] = RmConn(peer)
        self.log("[打洞] 对端 TCP 就绪 peer=%s，等待 SYNC 协调" % peer_id)
        threading.Thread(target=self._on_peer_tcp_ready_wait,
                         args=(peer,), daemon=True).start()

    def _on_peer_tcp_ready_wait(self, peer):
        """等待 SYNC 协调；超时则回退"直接打洞"（带近似对齐延迟）。"""
        peer_id = peer.get("id")
        try:
            rtp = self.get_udp_socket(peer_id)
            if rtp is not None:
                time.sleep(RM_TCP_READY_SYNC_WAIT)
                with self.lock:
                    conn = self.connections.get(peer_id)
                    still_not_connected = (conn is None or
                                           conn.state != RM_STATE_CONNECTED)
                if not still_not_connected:
                    return
                self.log("[打洞] SYNC 未完成，回退直接打洞 peer=%s（再等 %.0fms）"
                         % (peer_id, RM_TCP_READY_FALLBACK_DELAY * 1000))
                time.sleep(RM_TCP_READY_FALLBACK_DELAY)
            self._punch_task(peer, 0)
        except Exception as e:
            self.log("[打洞] TCP_READY 回退打洞异常: %s" % e)

    def _on_peer_punch_fail(self, peer_id):
        """P2 #8: 对端放弃 TCP → 本端停止空等，标记失败并回退 UDP。

        关键：对端放弃 TCP 说明它那条方向没打通。本端即便 connect 成功，
        也可能是【半开连接】——本端 SYN 到了对端，但对端 SYN 没到本端，
        对端应用层没有对应 socket 接收。这种连接本端 send() 能进内核缓冲，
        对端却收不到，数据会超时/RST。
        因此必须【无论本端是否 CONNECTED 都关闭 TCP】，否则会把文件发进
        一条单向死连接（现象：协商续传 timeout、通道失败 10053/10054）。
        """
        with self.lock:
            conn = self.connections.get(peer_id)
            # 关键：若本端已 CONNECTED（且是经握手确认的真连接），
            # 对端的 PUNCH_FAIL 只是"它那条腿失败"，不代表本端连接不可用。
            # 此时【不关闭】本端连接，避免误杀刚建立的有效通道。
            # 只有当本端也非 CONNECTED（半开或未连）时才关闭重试。
            if conn and conn.state == RM_STATE_CONNECTED:
                self.log("[打洞] 收到对端 PUNCH_FAIL，但本端已握手确认连接，忽略")
                return
        self.log("[打洞] 对端放弃 TCP，peer=%s 关闭半开连接并回退 UDP" % peer_id)
        with self.lock:
            conn = self.connections.get(peer_id)
            if conn:
                self._close_sock(conn)
                conn.state = RM_STATE_FAILED
        # 路径调度：热备失效 → 暖备上位
        try:
            self._on_path_failure(peer_id, "tcp")
        except Exception:
            pass
        cb = self.on_state_changed
        if cb:
            try:
                cb()
            except Exception:
                pass

    def _on_punch_round(self, peer_id, round_no, t_go_r):
        """P2: 收到主导方下发的打洞轮次时刻。"""
        with self._punch_round_cond:
            self._punch_round_evt[peer_id] = (round_no, t_go_r)
            self._punch_round_cond.notify_all()

    def _wait_punch_round(self, peer_id, round_no, timeout):
        """P2: 等待主导方下发的 >= round_no 的时刻（responder 钟）。返回 t_go_r 或 None。"""
        deadline = time.time() + timeout
        with self._punch_round_cond:
            while True:
                v = self._punch_round_evt.get(peer_id)
                if v and v[0] >= round_no:
                    return v[1]
                remain = deadline - time.time()
                if remain <= 0:
                    return None
                self._punch_round_cond.wait(remain)

    def _sleep_until_local(self, t_local_ms):
        """P2: sleep 到本机绝对毫秒时刻（用于轮次对齐后的打洞）。"""
        remain = (t_local_ms - int(time.time() * 1000)) / 1000.0
        if remain > 0:
            time.sleep(min(remain, 3.0))

    def _on_path_failure(self, peer_id, proto):
        """梯度冗余：某协议路径失效 → 触发角色轮转（保守暖备上位热备）。"""
        sched = self._path_schedulers.get(peer_id)
        if not sched:
            return
        if proto == "tcp":
            new_hot = sched.promote_on_hot_failure()
            if new_hot:
                self.log("[路径] peer=%s 热备失效，%s 上位为热备"
                         % (peer_id, new_hot))
        else:
            # UDP 失效：从调度器移除该路径（TCP 通常已是热备）
            sched.remove("udp:%s" % peer_id)

    def new_recv_epoch(self, peer_key):
        """新接收循环启动时调用：递增该 peer 的代际号，返回新值。"""
        with self.lock:
            e = self._recv_epoch.get(peer_key, 0) + 1
            self._recv_epoch[peer_key] = e
            return e

    def is_current_epoch(self, peer_key, epoch):
        """检查某接收循环是否仍是当前代际（旧的应退出）。"""
        with self.lock:
            return self._recv_epoch.get(peer_key) == epoch

    def _request_rebuild(self, peer_id):
        """请求服务器重新协调打洞（带冷却）。【不销毁任何现存连接】。

        与 _handle_udp_peer_dead 的区别：本方法只发重建请求，
        不会 pop/close 任何通道 —— 供 TCP 死亡等场景使用，
        避免误杀另一条健康路径（如刚上位的 UDP 热备）。
        """
        now = time.time()
        with self._rebuild_cooldown_lock:
            last = self._rebuild_cooldown.get(peer_id, 0)
            if now - last < self._rebuild_cooldown_sec:
                self.log("[房间] peer=%s 重建冷却中（%.0f 秒前）"
                         % (peer_id, now - last))
                return
            self._rebuild_cooldown[peer_id] = now
        try:
            if self.signaling:
                self.log("[房间] peer=%s 请求重新打洞" % peer_id)
                self.signaling.request_punch(peer_id)
        except Exception as e:
            self.log("[房间] peer=%s 重新打洞请求失败: %s" % (peer_id, e))

    def _handle_udp_peer_dead(self, peer_id):
        """UDP-RTP 对端失联 → 清理 UDP 连接 + 触发重建（带冷却）。"""
        self.log("[UDP-RTP] peer=%s 通道失联，清理并触发重建" % peer_id)
        try:
            self._on_path_failure(peer_id, "udp")
        except Exception:
            pass
        with self.lock:
            rtp = self.udp_conns.pop(peer_id, None)
        if rtp:
            try:
                rtp.close()
            except Exception:
                pass
            try:
                addr = rtp.peer
                peer_key = "%s:%d" % (addr[0], addr[1])
                if self.signaling:
                    self.signaling.unregister_udp_rtp(peer_key)
            except Exception:
                pass
        self._request_rebuild(peer_id)

    def on_joined(self, members):
        for m in members:
            self._add_member(m)

    def on_member_join(self, member):
        self._add_member(member)

    def on_member_leave(self, peer_id):
        with self.lock:
            self.members.pop(peer_id, None)
            conn = self.connections.pop(peer_id, None)
            if conn:
                self._close_sock(conn)
            # 梯度冗余：清理该 peer 的路径调度器
            self._path_schedulers.pop(peer_id, None)
            # 连接池：清理空闲记录
            self._peer_last_active.pop(peer_id, None)
            self._tcp_alive_fail.pop(peer_id, None)
            self._tcp_last_ping_at.pop(peer_id, None)
            self._sync_plan.pop(peer_id, None)
            self._awaiting_sync.pop(peer_id, None)
            # UDP 连接也要清理（否则成员离开后仍占资源）
            rtp = self.udp_conns.pop(peer_id, None)
        if rtp:
            try:
                rtp.close()
            except Exception:
                pass
        with self._rebuild_cooldown_lock:
            self._rebuild_cooldown.pop(peer_id, None)
        self.log("[房间] 成员离开 %s" % peer_id)

    def on_punch_go(self, peer, at_ms):
        peer_id = peer.get("id")
        if not peer_id:
            return
        with self.lock:
            self.members[peer_id] = peer
            conn = self.connections.get(peer_id)
            if conn and conn.state in (RM_STATE_CONNECTING, RM_STATE_CONNECTED):
                return
            self.connections[peer_id] = RmConn(peer)
        threading.Thread(target=self._punch_task, args=(peer, at_ms), daemon=True).start()

    def on_inbound(self, sock, addr):
        """处理 listener accept 到的连接：若匹配成员 TCP 公网映射则接管。

        回调必须在【释放锁之后】执行（get_conn 需要同一把锁，锁内回调会死锁）。

        握手确认：入站方作为"被连方"，与连接方各发一次魔数、各收一次。
        两端都 send+recv（魔数相同），即可确认双向可达；握手失败说明
        对方并未真正建立双向连接（例如对方是另一方向的半开），拒绝接管。
        """
        ip, port = addr[0], addr[1]
        key = "%s:%d" % (ip, port)
        ready_pid = None
        ready_member = None
        with self.lock:
            # 优先精确匹配 ip:port（pub_tcp 与入站源端口一致时）
            for pid, m in self.members.items():
                if m.get("pub_tcp", "") == key:
                    ready_pid = pid
                    ready_member = dict(m)
                    break
            # 回退：按【公网 IP】匹配。原因：NAT 可能给"映射 socket（朝服务器）"
            # 和"打洞 socket（朝对端）"分配不同的公网端口，导致精确 ip:port
            # 匹配失败（实测：TCP 握手成功但 on_inbound 不认、连接被关）。
            # 用 IP 匹配即可唯一识别对端（1v1 场景）。
            if ready_pid is None and ip:
                for pid, m in self.members.items():
                    pt = m.get("pub_tcp", "")
                    if pt and pt.rsplit(":", 1)[0] == ip:
                        ready_pid = pid
                        ready_member = dict(m)
                        break
        if ready_pid is None:
            return False
        # 强制握手确认：失败=半开/单向 → 关闭不接管（避免把死连接当通道）。
        if rm_punch_handshake(sock, RM_PUNCH_HANDSHAKE_TIMEOUT):
            self.log("[打洞] 入站连接 %s 握手确认" % key)
        else:
            self.log("[打洞] 入站连接 %s 握手无回应（半开），关闭不接管" % key)
            try: sock.close()
            except Exception: pass
            return False
        my_pref = self._inbound_is_preferred(ready_pid)
        with self.lock:
            conn = self.connections.get(ready_pid)
            if conn and conn.state == RM_STATE_CONNECTED and conn.sock:
                # 已有连接：仅当"新来是主、已有非主"才替换；否则保留已有，
                # 关闭新入站 socket（避免 fd 泄漏）。
                if not (my_pref and not conn.preferred):
                    try: sock.close()
                    except Exception: pass
                    return True
                self._close_sock(conn)
            elif conn:
                # 覆盖前先关掉旧 socket，避免并发入站/出站竞争导致 fd 泄漏
                self._close_sock(conn)
            new_conn = RmConn(ready_member)
            new_conn.state = RM_STATE_CONNECTED
            new_conn.sock = sock
            new_conn.addr = (ip, port)
            new_conn.preferred = my_pref
            self.connections[ready_pid] = new_conn
        self._apply_keepalive(sock)
        self.log("[房间] 入站连接 %s <- %s" % (ready_member.get("name", ready_pid), key))
        cb = self.on_socket_ready
        if cb:
            try:
                cb(ready_pid, sock, ready_member)
            except Exception as e:
                self.log("[房间] 入站回调异常: %s" % e)
        return True

    def get_socket(self, peer_id):
        with self.lock:
            conn = self.connections.get(peer_id)
            if conn and conn.state == RM_STATE_CONNECTED and conn.sock:
                return conn.sock
        return None

    def _channel_usable(self, sock):
        """检查通道是否仍可用（未被关闭）。

        换路后旧接收循环会在 finally 里 sock.close()，但 connections/udp_conns
        表可能仍留着该对象，get_send_channel 若返回它会立即发送失败。
        """
        if sock is None:
            return False
        if getattr(sock, "is_udp_rtp", False):
            try:
                return not sock._closed
            except Exception:
                return True
        try:
            return sock.fileno() != -1
        except Exception:
            return False

    def mark_active(self, peer_id):
        """连接池：标记某 peer 刚被使用（传输开始/结束），重置空闲计时。"""
        with self.lock:
            self._peer_last_active[peer_id] = time.time()

    def _idle_factor(self, peer_id, now):
        """连接池：按空闲时长返回保活间隔放大系数。

        空闲越久，保活越稀疏（省电省流量）；下次传输前会被 mark_active 重置。
        · < 60s    → ×1（正常）
        · < 10min  → ×4
        · < 1h     → ×16
        · 更久     → ×64（几乎停摆，接受下次重建）
        """
        last = self._peer_last_active.get(peer_id, now)
        idle = now - last
        if idle < 60:
            return 1
        if idle < 600:
            return 4
        if idle < 3600:
            return 16
        return 64

    def get_send_channel(self, peer_id, exclude_socks=None):
        """按路径角色选路发送：优先热备，其次保守暖备，最后宽松暖备。

        返回 (sock, io_lock, is_udp, role) 或 None。
        · 角色由梯度冗余调度器维护（hot/warm_safe/warm_loose）
        · 无调度器时退化为旧行为（TCP 优先，UDP 回退）
        · exclude_socks：已试过且失败的 socket 集合（按 id() 比较），换路时跳过
        """
        exclude = exclude_socks or set()
        sched = self._path_schedulers.get(peer_id)
        if sched is not None:
            for role in ("hot", "warm_safe", "warm_loose"):
                with sched.lock:
                    path = next((p for p in sched.paths.values()
                                 if p.role == role), None)
                if path is None:
                    continue
                if path.proto == "tcp":
                    with self.lock:
                        conn = self.connections.get(peer_id)
                        if (conn and conn.state == RM_STATE_CONNECTED and conn.sock
                                and id(conn.sock) not in exclude
                                and self._channel_usable(conn.sock)):
                            return (conn.sock, conn.io_lock, False, role)
                elif not RM_TCP_ONLY:
                    with self.lock:
                        rtp = self.udp_conns.get(peer_id)
                        if (rtp is not None and id(rtp) not in exclude
                                and self._channel_usable(rtp)):
                            return (rtp, rtp.io_lock, True, role)
        # 回退：任意可用通道
        with self.lock:
            conn = self.connections.get(peer_id)
            if (conn and conn.state == RM_STATE_CONNECTED and conn.sock
                    and id(conn.sock) not in exclude
                    and self._channel_usable(conn.sock)):
                return (conn.sock, conn.io_lock, False, "?")
            if not RM_TCP_ONLY:
                rtp = self.udp_conns.get(peer_id)
                if (rtp is not None and id(rtp) not in exclude
                        and self._channel_usable(rtp)):
                    return (rtp, rtp.io_lock, True, "?")
        return None

    def get_path_roles(self, peer_id):
        """供 UI 展示：返回该 peer 的 {path_id: role}。"""
        sched = self._path_schedulers.get(peer_id)
        if not sched:
            return {}
        with sched.lock:
            return {pid: p.role for pid, p in sched.paths.items()}

    def has_peer(self, peer_id):
        with self.lock:
            return peer_id in self.connections

    def get_conn(self, peer_id):
        with self.lock:
            return self.connections.get(peer_id)

    def get_peer_did(self, peer_id):
        with self.lock:
            m = self.members.get(peer_id)
            if m and m.get("did"):
                return m["did"]
        return peer_id

    def get_members(self):
        with self.lock:
            out = []
            for pid, m in self.members.items():
                conn = self.connections.get(pid)
                state = conn.state if conn else "idle"
                addr = conn.addr if conn else None
                # UDP 通道已通即视为在线可用，无需等待 TCP（TCP 可能仍在打洞中/失败）
                if state != RM_STATE_CONNECTED and pid in self.udp_conns:
                    state = RM_STATE_CONNECTED
                    rtp = self.udp_conns[pid]
                    addr = "udp://%s:%d" % rtp.peer
                # 梯度冗余：附加路径角色（hot/warm_safe/warm_loose）
                roles = {}
                sched = self._path_schedulers.get(pid)
                if sched:
                    with sched.lock:
                        roles = {ppath.path_id: ppath.role for ppath in sched.paths.values()}
                out.append({
                    "id": pid,
                    "name": m.get("name", ""),
                    "state": state,
                    "addr": addr,
                    "path_roles": roles,
                })
            return out

    def _add_member(self, member):
        peer_id = member.get("id")
        if not peer_id:
            return
        with self.lock:
            self.members[peer_id] = member
        try:
            self.signaling.request_punch(peer_id)
        except Exception as e:
            self.log("[房间] 请求打洞失败: %s" % e)

    def _punch_task(self, peer, at_ms):
        """多轮重试打洞。

        关键：每轮开始前【重读】self.members 里的最新 peer——
        因为第一次 punch_go 到达时，对端的 pub_tcp 可能还没在服务器登记
        （对端此时还在 syncTime / openMapping），导致下发的 peer 里
        pub_tcp 为空。之后对端完成登记并再次 request_punch 时，会下发第二次
        punch_go 携带有效 pub_tcp。若本任务不重读，就会一直用旧的空 pub_tcp。
        """
        peer_id = peer.get("id")
        # 标准 C/S：ID 小者 listen 等待（不主动 connect，靠 LISTEN socket
        # 接住对方 SYN）；ID 大者才主动 connect。
        if not self._is_punch_initiator(peer_id):
            self.log("[打洞] peer=%s：本端 ID 较小，listen 等待对方连接" % peer_id)
            return
        # 打洞并发限制：同一 peer 最多允许 2 个 _punch_task 并发。
        # 保留适度冗余（多触发源中若一个卡住，另一个可补位），又不至于泛滥。
        with self.lock:
            cnt = self._punch_active.get(peer_id, 0)
            if cnt >= 2:
                return
            self._punch_active[peer_id] = cnt + 1
        self._punch_sem.acquire()
        try:
            for attempt in range(1, RM_PUNCH_RETRY + 1):
                if not self._running:
                    return
                # 已连接则退出：其他并发任务已成功，避免无谓尝试
                # （消除 errno=10048 端口冲突 / 无路由等噪音日志，省资源省电）
                with self.lock:
                    _c = self.connections.get(peer_id)
                    if _c and _c.state == RM_STATE_CONNECTED:
                        self.log("[打洞] peer=%s 已由其他任务连接，本任务退出" % peer_id)
                        return
                    # SYNC 精准打洞优先（全局门闩）：若该 peer 正在"等待/执行精准打洞"
                    # 且未超时，本重试任务（无论来自 punch_go / TCP_READY / 轮次重试）
                    # 一律让路退出，保证"先精准打洞，失败才风暴"的严格顺序。
                    # 超时保护：对端不支持 SYNC / COMMIT 丢失时，超时后放行兜底风暴。
                    _t0 = self._awaiting_sync.get(peer_id)
                    _waiting = (_t0 is not None
                                and (time.time() - _t0) < RM_SYNC_AWAIT_TIMEOUT)
                    _planned = self._sync_plan.get(peer_id)
                if _waiting or _planned is not None:
                    self.log("[打洞] peer=%s 等待精准打洞中%s，重试任务让路退出"
                             % (peer_id,
                                ("（t_go=%d）" % _planned) if _planned is not None else ""))
                    return
                # 重读最新 peer（含可能刚更新过的 pub_tcp）
                with self.lock:
                    latest = self.members.get(peer_id) or peer
                # 没靶子不打：pub_tcp 与内网候选都为空 → 无候选可连，打也白打。
                # 主动再发一次 punch_req 请求映射，短暂等待后重读；仍无则放弃
                # 本轮（等对端登记映射后由 member_update / punch_go 重新触发）。
                if not self._has_reachable_target(latest):
                    try:
                        if self.signaling:
                            self.log("[打洞] peer=%s 无靶子（pub_tcp 与内网均空），请求映射后等待"
                                     % peer_id)
                            self.signaling.request_punch(peer_id)
                    except Exception:
                        pass
                    time.sleep(0.5)
                    with self.lock:
                        latest = self.members.get(peer_id) or latest
                    if not self._has_reachable_target(latest):
                        self.log("[打洞] peer=%s 仍无靶子，放弃本轮（不打）" % peer_id)
                        return
                if attempt == 1:
                    self.log("[打洞] 任务启动 peer=%s 本地端口=%d"
                             % (peer_id, self._puncher.local_tcp_port))
                else:
                    self.log("[打洞] 第 %d 轮重试 peer=%s（pub_tcp=%s）"
                             % (attempt, peer_id, latest.get("pub_tcp", "")))
                # 传入 should_stop：风暴期间若其他任务已连上，立即停止
                def _already_connected(_pid=peer_id):
                    with self.lock:
                        _cc = self.connections.get(_pid)
                        return bool(_cc and _cc.state == RM_STATE_CONNECTED)
                # NAT 预热：打洞前用 UDP keepalive 保持本端 NAT conntrack 热态，
                # 让紧接的 SYN 更易被 NAT 转发（很多 NAT 对刚建/将超时的 TCP 映射丢 SYN）。
                try:
                    _rtp = self.get_udp_socket(peer_id)
                    if _rtp is not None:
                        _rtp.warm_up_nat(5, 20)
                except Exception:
                    pass
                result = self._puncher.punch(latest, at_ms, should_stop=_already_connected)
                if result:
                    self._install_socket(peer_id, result)
                    return
                if attempt < RM_PUNCH_RETRY:
                    next_round = attempt + 1
                    backoff = attempt * RM_PUNCH_RETRY_BACKOFF
                    rtp = self.get_udp_socket(peer_id)
                    my_id = getattr(self.signaling, "my_id", None) or ""
                    if rtp is not None and my_id and my_id < peer_id:
                        # 主导方：约定下一轮本机时刻 → 换算 responder 钟下发
                        t_go_next_i = int(time.time() * 1000) + backoff * 1000
                        off = rtp.get_sync_offset() or 0
                        rtp.send_punch_round(next_round, t_go_next_i + off)
                        self.log("[打洞] 主导轮次 %d，约定 T_go(本地)=%d"
                                 % (next_round, t_go_next_i))
                        self._sleep_until_local(t_go_next_i)
                        at_ms = 0
                    elif rtp is not None and my_id and my_id > peer_id:
                        # 响应方：等待主导方下发的下一轮时刻
                        t_go_r = self._wait_punch_round(peer_id, next_round, backoff + 2.0)
                        if t_go_r is not None:
                            self.log("[打洞] 跟随主导轮次 %d，T_go(本地)=%d"
                                     % (next_round, t_go_r))
                            self._sleep_until_local(t_go_r)
                        else:
                            time.sleep(backoff)
                        at_ms = 0
                    else:
                        # 无 UDP 通道：退化为本地退避
                        time.sleep(backoff)
                        at_ms = 0
            # 仅在仍未连接时才标记失败：避免把并发任务已建立的 CONNECTED 覆盖
            mark_failed = False
            with self.lock:
                conn = self.connections.get(peer_id)
                if conn and conn.state != RM_STATE_CONNECTED:
                    conn.state = RM_STATE_FAILED
                    mark_failed = True
            if not mark_failed:
                self.log("[房间] 本任务失败，但 peer=%s 已由其他任务连接，忽略"
                         % peer_id)
                return
            self.log("[房间] 连接失败 %s" % peer.get("name", peer_id))
            # P2 #8: 通知对端本端已放弃 TCP，避免对端空等
            try:
                rtp = self.get_udp_socket(peer_id)
                if rtp is not None:
                    rtp.send_punch_fail()
            except Exception:
                pass
        finally:
            with self.lock:
                c = self._punch_active.get(peer_id, 1) - 1
                if c <= 0:
                    self._punch_active.pop(peer_id, None)
                else:
                    self._punch_active[peer_id] = c
            self._punch_sem.release()

    def _install_socket(self, peer_id, result):
        my_pref = self._outbound_is_preferred(peer_id)
        with self.lock:
            old = self.connections.get(peer_id)
            if old and old.state == RM_STATE_CONNECTED and old.sock:
                # 已有连接：若已有的是主连接、新来非主 → 丢弃新的；
                # 反之（已有非主、新来主）→ 替换；同优先级 → 保留先到的。
                if not (my_pref and not old.preferred):
                    try:
                        result.sock.close()
                    except Exception:
                        pass
                    return
                self._close_sock(old)
            elif old:
                self._close_sock(old)
            conn = RmConn(self.members.get(peer_id, {}))
            conn.state = RM_STATE_CONNECTED
            conn.sock = result.sock
            conn.addr = (result.ip, result.port)
            conn.preferred = my_pref
            self.connections[peer_id] = conn
            member = dict(self.members.get(peer_id, {}))
        self._apply_keepalive(result.sock)
        self.log("[房间] 已连接 %s -> %s:%d" % (member.get("name", peer_id), result.ip, result.port))
        # 梯度冗余：注册 TCP 路径（角色由调度器按空缺位分配）
        try:
            self._get_path_sched(peer_id).register("tcp:%s" % peer_id, "tcp", 0)
        except Exception:
            pass
        cb = self.on_socket_ready
        if cb:
            try:
                cb(peer_id, result.sock, member)
            except Exception as e:
                self.log("[房间] 接收回调异常: %s" % e)
        # 打洞成功 → 通知 UI 刷新（状态从"连接中"变"已连接"）
        sc = self.on_state_changed
        if sc:
            try:
                sc()
            except Exception:
                pass

    def _apply_keepalive(self, sock):
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        except Exception:
            pass

    def _close_sock(self, conn):
        if conn and conn.sock:
            try:
                conn.sock.close()
            except Exception:
                pass
            conn.sock = None

    def _keepalive_loop(self):
        """梯度冗余保活循环。

        以 1 秒为节拍轮询；每条路径按各自的软性探测间隔决定何时检查/保活。
        - 热备：间隔最短，快速感知断线
        - 保守暖备：硬性钳制，确保 NAT 映射有效
        - 宽松暖备：间隔最长，尽量省电
        路径检查失败 → 更新调度器（on_failure）+ 触发角色轮转。
        """
        _next_due = {}
        while self._running:
            time.sleep(1.0)
            if not self._running:
                break
            now = time.time()
            state_changed = False

            # ---- 1) 按路径调度器的软性间隔，逐条检查/保活 ----
            for pid in list(self._path_schedulers.keys()):
                sched = self._path_schedulers.get(pid)
                if not sched:
                    continue
                with sched.lock:
                    items = list(sched.paths.items())
                idle_factor = self._idle_factor(pid, now)
                for path_id, p in items:
                    # A) 跳过 dead 路径（不再无谓探测，等重连时 register 替换）
                    if p.role == "dead":
                        continue
                    if now < _next_due.get(path_id, 0):
                        continue
                    ok = self._probe_path(pid, path_id, p)
                    if ok:
                        p.scheduler.on_success()
                        p.last_seen = now
                    else:
                        p.scheduler.on_failure()
                        self.log("[保活] 路径 %s 失败（间隔=%ds）"
                                 % (path_id, p.scheduler.current_interval))
                    # 连接池：空闲降频（间隔 ×idle_factor）
                    _next_due[path_id] = now + p.scheduler.current_interval * idle_factor
            # B) 清理已移除路径的 _next_due 条目。
            #    注意：必须在【所有 peer】处理完后统一清理——此前 live_ids 是
            #    每个 peer 独立的局部变量，用它清理全局 _next_due 会把【其他
            #    peer 的到期时间】误删，导致那些路径每秒被重复探测、保活间隔
            #    指数膨胀到 3600s（实测日志 [保活] 间隔=3600s 的根因）。
            all_live = set()
            for pid2 in list(self._path_schedulers.keys()):
                sched2 = self._path_schedulers.get(pid2)
                if not sched2:
                    continue
                with sched2.lock:
                    for path_id2, p2 in sched2.paths.items():
                        if p2.role != "dead":
                            all_live.add(path_id2)
            for stale_id in [k for k in _next_due if k not in all_live]:
                _next_due.pop(stale_id, None)

            # ---- 2) TCP 长连接存活检查（兜底，快速感知） ----
            # B：健壮性改进 ——
            #   ① 宽限期：新建连接 RM_TCP_KEEPALIVE_GRACE 秒内不判死。
            #      刚 connect（尤其半开/握手未完成）时 MSG_PEEK 可能瞬时异常，
            #      立即判死会误杀本可用的连接（实测"建连即失效"）。
            #   ② 连续失败阈值：单次探测失败不判死，需连续 N 次（默认 2）才判，
            #      抗瞬时抖动。
            with self.lock:
                tcp_items = [(pid, conn) for pid, conn in self.connections.items()
                             if conn.state == RM_STATE_CONNECTED]
            for pid, conn in tcp_items:
                # ① 宽限期：连接太新，跳过判活
                if now - getattr(conn, "created_at", 0) < RM_TCP_KEEPALIVE_GRACE:
                    continue
                if self._alive(conn.sock):
                    # 探测成功 → 清空失败计数
                    self._tcp_alive_fail.pop(pid, None)
                    # TCP 心跳 RTT 探测：空闲时发 PING（与 UDP-RTP 心跳对称）。
                    # 用 io_lock.acquire(False) 非阻塞获取写权——若正有文件
                    # 传输持锁，直接跳过本次（不干扰数据流，避免字节流错位）。
                    with self.lock:
                        last_active = self._peer_last_active.get(pid, 0)
                        last_ping = self._tcp_last_ping_at.get(pid, 0)
                    idle_enough = (now - last_active >= RM_TCP_PING_IDLE
                                   and now - last_ping >= RM_TCP_PING_INTERVAL)
                    if idle_enough:
                        got = conn.io_lock.acquire(blocking=False)
                        if got:
                            try:
                                conn.sock.sendall(struct.pack('!B', FLAG_PING))
                                with self.lock:
                                    self._tcp_last_ping_at[pid] = now
                                tcp_rtt_mark_ping_sent(pid)
                            except Exception:
                                pass
                            finally:
                                conn.io_lock.release()
                    continue
                # ② 失败累计：未达阈值先记账，不判死
                fail_n = self._tcp_alive_fail.get(pid, 0) + 1
                if fail_n < RM_TCP_KEEPALIVE_FAIL_THRESHOLD:
                    self._tcp_alive_fail[pid] = fail_n
                    self.log("[保活] %s TCP 探测失败（%d/%d），暂不判死"
                             % (pid, fail_n, RM_TCP_KEEPALIVE_FAIL_THRESHOLD))
                    continue
                # 达阈值 → 判死
                self.log("[保活] %s TCP 通道失效（连续 %d 次），重新打洞" % (pid, fail_n))
                dead = False
                with self.lock:
                    cur = self.connections.get(pid)
                    if cur is conn:
                        self._close_sock(cur)
                        cur.state = RM_STATE_FAILED
                        dead = True
                self._tcp_alive_fail.pop(pid, None)
                if not dead:
                    continue
                try:
                    self._on_path_failure(pid, "tcp")
                except Exception:
                    pass
                # 关键修复：TCP 死亡【不能】走 _handle_udp_peer_dead ——
                # 那会销毁健康的 UDP 通道（可能是刚上位的热备）。
                # 只请求重建 TCP，绝不触碰其他路径。
                self._request_rebuild(pid)
                state_changed = True

            # ---- 3) UDP-RTP 存活检查 ----
            with self.lock:
                udp_items = list(self.udp_conns.items())
            for pid, rtp in udp_items:
                try:
                    if rtp.is_dead():
                        self.log("[保活] %s UDP-RTP 通道失联" % pid)
                        self._handle_udp_peer_dead(pid)
                        state_changed = True
                except Exception:
                    pass

            if state_changed:
                cb = self.on_state_changed
                if cb:
                    try:
                        cb()
                    except Exception:
                        pass

    def _probe_path(self, peer_id, path_id, path):
        """探测某条路径是否存活（按协议选方式）。

        · tcp：用 _alive() 检查 socket（MSG_PEEK）
        · udp：检查 UdpReliableSocket 是否失联
        返回 True 表示存活。
        """
        if path.proto == "tcp":
            with self.lock:
                conn = self.connections.get(peer_id)
            if conn and conn.state == RM_STATE_CONNECTED and conn.sock:
                return self._alive(conn.sock)
            return False
        else:
            with self.lock:
                rtp = self.udp_conns.get(peer_id)
            if rtp is None:
                return False
            try:
                return not rtp.is_dead()
            except Exception:
                return False

    def _alive(self, sock):
        if not sock:
            return False
        try:
            sock.setblocking(False)
        except Exception:
            # 套接字已被其他线程关闭（WinError 10038 等）→ 视为已失效。
            # 必须在这里捕获：setblocking 失败后 fd 无效，后续 recv 也无意义。
            return False
        try:
            data = sock.recv(1, socket.MSG_PEEK)
            return data != b""
        except BlockingIOError:
            return True
        except Exception:
            return False
        finally:
            try:
                sock.setblocking(True)
            except Exception:
                pass


# 兼容别名（主程序按原类名使用）
SignalingClient = RmSignalingClient
RoomManager = RmRoomManager


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

# 动态本地打洞端口（映射观测 + TCP打洞共用）。
# 默认 = RM_TCP_PORT（9995）。若被占用（Windows 上次会话 TIME_WAIT 未释放，
# 报 10048），映射连接退化为随机端口并登记到全局；打洞 socket 必须用
# 【同一端口】，才能与映射共享同一 NAT 映射（否则服务器记录的 pub_tcp
# 与实际打洞源端口不一致 → 打洞必败）。
_DYNAMIC_PUNCH_PORT = [RM_TCP_PORT]
_DYN_PORT_LOCK = threading.Lock()

def _get_dyn_punch_port():
    with _DYN_PORT_LOCK:
        return _DYNAMIC_PUNCH_PORT[0]

def _set_dyn_punch_port(p):
    with _DYN_PORT_LOCK:
        _DYNAMIC_PUNCH_PORT[0] = p

def _is_addr_in_use(exc):
    """判断异常是否为"地址/端口已被占用"（跨平台：WinError 10048 / errno 98/48）。"""
    try:
        en = getattr(exc, "errno", None) or getattr(exc, "winerror", None)
        if en in (10048, 98, 48, 10049):   # WSAEADDRINUSE / EADDRINUSE
            return True
        s = str(exc).lower()
        return "10048" in s or "address already in use" in s or "只允许使用一次" in s
    except Exception:
        return False

# TCP 心跳 RTT 追踪（本地计时，零协议变更）。
# 结构：{peer_key: {pingSentAt, rttMs}}，仅在房间模式长连接使用。
_TCP_RTT_LOCK = threading.Lock()
_TCP_PING_SENT_AT = {}    # peer_key -> 纳秒时刻
_TCP_RTT_MS = {}          # peer_key -> 最近 RTT（毫秒）

def tcp_rtt_mark_ping_sent(peer_key):
    with _TCP_RTT_LOCK:
        _TCP_PING_SENT_AT[peer_key] = time.perf_counter_ns()

def tcp_rtt_mark_pong(peer_key):
    """收到 PONG：若有匹配的在途 PING，返回本次 RTT（毫秒），否则 None。"""
    with _TCP_RTT_LOCK:
        t = _TCP_PING_SENT_AT.pop(peer_key, None)
        if t is None:
            return None
        rtt = (time.perf_counter_ns() - t) // 1_000_000
        _TCP_RTT_MS[peer_key] = rtt
        return rtt

def tcp_rtt_get(peer_key):
    with _TCP_RTT_LOCK:
        return _TCP_RTT_MS.get(peer_key, -1)

BUFFER_SIZE = 1 * 1024 * 1024   # 1 MB 初始值，会动态调整
MAX_BUFFER_SIZE = 8 * 1024 * 1024  # 最大缓冲区 8MB（防止内存占用过高）
SOCKET_BUFFER_SIZE = 16 * 1024 * 1024  # socket 缓冲区大小 16MB（高延迟网络适配）
FLAG_FILE   = 0                 # 标志位：文件
FLAG_FOLDER = 1                 #  
FLAG_COMPRESS = 0x01
FLAG_RESUME   = 0x02            # 断点续传标志
# TCP 心跳探测（仅房间模式长连接使用，空闲时发）：
#   PING: 发送方 -> 接收方，单字节；PONG: 接收方 -> 发送方，单字节。
# 与 UDP-RTP 的 KEEPALIVE 语义对齐，用于对比两协议的应用层 RTT。
FLAG_PING   = 0x10
FLAG_PONG   = 0x11
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

# 续传状态保留天数（超过则清理，防止目录膨胀）
RESUME_RETENTION_DAYS = 30
# 日志保留天数
LOG_RETENTION_DAYS = 7
LOG_FILE = 'transfer.log'


# 全局诊断日志钩子：由 P2PApp 初始化时设置为 GUI 的 log 方法。
# 模块级函数（如 optimize_tcp_socket）通过它把诊断输出到界面日志。
_DIAG_LOG = None


def _diag(msg):
    """输出诊断信息：优先走 GUI 日志，否则回退 stdout。"""
    if _DIAG_LOG is not None:
        try:
            _DIAG_LOG(msg)
            return
        except Exception:
            pass
    try:
        import sys as _sys
        _sys.stdout.write(msg + "\n")
        _sys.stdout.flush()
    except Exception:
        pass


def _print_socket_buffers(sock):
    """读回并打印 socket 的 SNDBUF/RCVBUF 实际值（诊断用，失败静默）。"""
    try:
        snd = sock.getsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF)
        rcv = sock.getsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF)
        _diag("[TCP缓冲] 实际生效 SNDBUF=%d RCVBUF=%d（期望 %d）"
              % (snd, rcv, SOCKET_BUFFER_SIZE))
    except Exception:
        pass


def optimize_tcp_socket(sock):
    """统一的 TCP socket 优化 —— 全项目唯一入口。

    所有 TCP socket（局域网发送/接收、房间出站打洞、房间入站 accept）
    都调用本函数，确保缓冲区与 Nagle 策略一致；避免某条路径漏设导致
    吞吐被系统默认值卡住（例如房间打洞出站 socket 曾漏设，公网 TCP
    速度只有 2.6 MB/s）。

    做三件事：
      1. TCP_NODELAY=1：关闭 Nagle，避免小包延迟累积
      2. SO_KEEPALIVE + 参数：减少防火墙超时断连
      3. SO_SNDBUF / SO_RCVBUF 设大（逐级下调），扩大发送/接收窗口
    注意：SO_RCVBUF 影响 window scale，connect 之前设对接收窗口最有效；
    connect 之后设主要改善 SO_SNDBUF（发送方向），对发送吞吐仍有效。
    """
    try:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    except Exception:
        pass
    sock.settimeout(None)  # 大文件传输不设置超时
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 60)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 10)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 3)
    except AttributeError:
        pass  # 部分系统不支持
    # 设置 socket 缓冲区（内核可能限制实际值，逐级下调尝试）
    for opt in (socket.SO_SNDBUF, socket.SO_RCVBUF):
        for buf_size in (SOCKET_BUFFER_SIZE * 2, SOCKET_BUFFER_SIZE):
            try:
                sock.setsockopt(socket.SOL_SOCKET, opt, buf_size)
                break
            except Exception:
                continue
    # 诊断：读回实际生效值（内核通常会限制；用于定位吞吐瓶颈）
    _print_socket_buffers(sock)


def cleanup_generated_files(log=None):
    """清理自身生成的文件：过期续传 JSON + 过期日志。

    · 续传状态（RESUME_DIR 下 *.json）：超过 RESUME_RETENTION_DAYS 天未修改则删除
    · 日志（transfer.log）：超过 LOG_RETENTION_DAYS 天则清空（保留文件名）
    仅清理本程序自己生成的文件，不触碰用户接收目录。
    """
    _log = log or (lambda m: None)
    # 1) 续传状态
    try:
        cutoff = time.time() - RESUME_RETENTION_DAYS * 86400
        removed = 0
        for jf in RESUME_DIR.glob('*.json'):
            try:
                if jf.stat().st_mtime < cutoff:
                    jf.unlink()
                    removed += 1
            except Exception:
                pass
        if removed:
            _log(f"[清理] 删除 {removed} 个过期续传记录（>{RESUME_RETENTION_DAYS}天）")
    except Exception as e:
        _log(f"[清理] 续传记录清理异常: {e}")
    # 2) 日志文件
    try:
        lf = Path(LOG_FILE)
        if lf.exists() and (time.time() - lf.stat().st_mtime) > LOG_RETENTION_DAYS * 86400:
            with open(lf, 'w'):
                pass
            _log(f"[清理] 日志超过 {LOG_RETENTION_DAYS} 天，已清空")
    except Exception as e:
        _log(f"[清理] 日志清理异常: {e}")
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
            # 用 /16 而非 /12：企业网/校园网常用 /21 /22 或 /16，
            # /12 覆盖 1048576 个 IP，会被 MAX_SCAN_IPS 截断到 65536
            # （只覆盖 172.16.0.0-172.16.255.255），反而扫不到真实网段
            # （如 172.20.93.x）。/16 正好 65536 个 IP，不触发截断。
            # 注：此函数同时用于【广播地址推断】，需保持较宽范围；
            #     【主动扫描】改用 get_scan_subnets()（见下）以 /24 提速。
            return f'{ip_str}/16'
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


def get_scan_subnets():
    """返回【主动扫描】用的子网列表（/24 粒度）。

    与 get_all_subnets() 的区别：后者用于广播推断（保留 /16 等宽范围），
    本函数专供 scan_subnet() 主动扫描使用——把大网段收敛到 /24（254 个 IP），
    避免每 20 秒遍历 6.5 万个地址却常发现 0 个设备。

    跨 /24 的设备由「广播搜索」(broadcast_search，走宽范围广播) 负责发现。
    """
    subnets = set()
    for ip in get_all_local_ips(ipv6=False):
        if ':' in ip:
            continue
        try:
            octets = ip.split('.')
            if len(octets) != 4:
                continue
            # 统一收敛到该 IP 所在 /24
            subnets.add("%s.%s.%s.0/24" % (octets[0], octets[1], octets[2]))
        except Exception:
            continue
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

def save_resume_state(target_ip, filepath, offset, total_size, mtime, role='sender', key_name=None):
    """保存续传状态，role=sender（发送方记录要发的文件）或 recv（接收方记录已收的文件）
    key_name: 续传记录的键（默认 basename(filepath)）。
              接收方改名保存时传入发送方原始文件名，保证重试能查到记录。"""
    key = key_name or os.path.basename(filepath)
    resume_file = get_resume_file(target_ip, key, role)
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
            # 接收方：按记录中的【实际路径】检查（支持接收时改名的情况）
            dest_path = Path(data.get('filepath', ''))
            if dest_path and dest_path.exists():
                if dest_path.stat().st_size == data.get('offset'):
                    return data
        # 不匹配则删除记录
        resume_file.unlink()
        return None
    except:
        return None

def delete_resume_state(target_ip, filepath, role='sender', key_name=None):
    """删除续传状态（key_name 与 save 时保持一致）"""
    key = key_name or os.path.basename(filepath)
    resume_file = get_resume_file(target_ip, key, role)
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
        # 房间模式专用监听（方案 B）：电脑 listen 9998，接住手机主动发来的
        # 打洞连接。原因：实测"手机→电脑"方向的 SYN 能到达，"电脑→手机"
        # 方向常被 NAT 丢弃（异构设备 TCP 同时打开失败）。故电脑侧开监听，
        # 让手机单向 connect 即可建立（C/S 模式，绕开失败方向）。
        self.room_listener_sock = None
        self.room_listener_running = False
        self.room_listener_thread = None
        self.auto_scan_enabled = False
        self.scanning = False
        self.pausing_network = False  # 传输时暂停广播/扫描/心跳

    def run(self):
        threading.Thread(target=self.udp_listener, daemon=True).start()
        threading.Thread(target=self.scan_listener, daemon=True).start()
        self._start_broadcasters()
        threading.Thread(target=self.tcp_file_receiver, daemon=True).start()
        # 房间模式端口 9998 不监听：
        # TCP 打洞靠双方同时从 9998 出站（simultaneous open），内核自动匹配
        # 对端 SYN；若本地存在 listener 反而拦截 SYN，且占用端口使打洞 socket
        # 无法 bind 9998。映射观测 socket 与打洞 socket 共用 9998，靠
        # SO_REUSEADDR/SO_REUSEPORT（4 元组不同）。
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
                    # 按设备指纹去重：收到自己发的包（device_id 相同）直接忽略。
                    # 比按 IP 过滤可靠——多网卡/IP 变化都不会误把自己加入列表。
                    if msg.get('device_id') and msg.get('device_id') == self.device_id:
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

    def _refresh_my_ips(self):
        """刷新本机 IP 列表（网络变动后 IP 会变，启动快照会失准）。

        device_id 过滤是"不显示自己"的主防线，本方法作为辅助：
        网络切换后及时更新 my_ips，避免扫描/广播时把本机新 IP 当对端。
        """
        try:
            new4 = get_all_local_ips(ipv6=False)
            new6 = get_all_local_ips(ipv6=True)
            old = set(self.my_ips) | set(self.my_ips_v6)
            new = set(new4) | set(new6)
            if new != old:
                self.my_ips = new4
                self.my_ips_v6 = new6
                added = new - old
                removed = old - new
                if added or removed:
                    self.gui.log("[网络] 本机 IP 已刷新"
                                 + (f"，新增 {', '.join(added)}" if added else "")
                                 + (f"，移除 {', '.join(removed)}" if removed else ""))
                    # 本机 IP 集合变化 → 网络切换。若在房间中，重建房间连接
                    self.gui._on_local_network_changed()
        except Exception:
            pass

    def clean_nodes(self):
        # 顺带刷新本机 IP（网络变动后可能变化）
        self._refresh_my_ips()
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
                remote_did = ""
                # 双端口探测：先试 SCAN_PORT，再试 UDP_PORT，任一回复即发现
                for port in (SCAN_PORT, UDP_PORT):
                    try:
                        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                        s.settimeout(SCAN_TIMEOUT)
                        probe = self._build_msg()  # 含 hostname/device_id/mac
                        s.sendto(probe.encode(), (ip_str, port))
                        data, _ = s.recvfrom(1024)
                        s.close()
                        reply = json.loads(data.decode('utf-8'))
                        hostname = reply.get('hostname', ip_str)
                        remote_did = reply.get('device_id', '') or ''
                        break  # 任一端口收到回复即成功
                    except:
                        try:
                            s.close()
                        except:
                            pass
                if hostname:
                    return (ip_str, hostname, remote_did)
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
                        ip_str, hostname, remote_did = result
                        # 设备指纹去重：跳过自己（即使本机 IP 快照失准）
                        if remote_did and remote_did == self.device_id:
                            continue
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
        # E：主动扫描用 /24 粒度（快），跨 /24 由广播搜索兜底
        subnets = get_scan_subnets()
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
        """薄封装：调用全项目统一的 socket 优化入口。"""
        optimize_tcp_socket(sock)

    def start_room_listener(self):
        """启动房间模式监听（方案 B：电脑 listen 9998 接住手机的主动连接）。"""
        if self.room_listener_running:
            return
        self.room_listener_running = True
        self.room_listener_thread = threading.Thread(
            target=self.room_tcp_listener, daemon=True)
        self.room_listener_thread.start()

    def stop_room_listener(self):
        """停止房间模式监听。"""
        self.room_listener_running = False
        s = self.room_listener_sock
        if s is not None:
            try:
                s.close()
            except Exception:
                pass
            self.room_listener_sock = None

    def room_tcp_listener(self):
        """房间模式专用 TCP 监听（端口 9998）—— 方案 B。

        背景：实测"电脑→手机"方向的打洞 SYN 常被 NAT 丢弃（异构设备
        TCP 同时打开失败），而"手机→电脑"方向可达。故电脑侧 listen 9998，
        让手机单向 connect 即可建立（标准 C/S，绕开失败方向）。
        仅接管房间模式打洞入站连接（匹配成员 pub_tcp），其余一律关闭。
        """
        sock = None
        for family, addr in ((socket.AF_INET, ('0.0.0.0', RM_TCP_PORT)),
                             (socket.AF_INET6, ('::', RM_TCP_PORT))):
            try:
                s = socket.socket(family, socket.SOCK_STREAM)
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                if family == socket.AF_INET6:
                    try:
                        s.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
                    except Exception:
                        pass
                # 关键：房间监听 socket 也要在 listen 前设 RCVBUF（window scale）
                try:
                    s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, SOCKET_BUFFER_SIZE)
                except Exception:
                    pass
                s.bind(addr)
                s.listen(50)
                sock = s
                self.gui.log(f"[房间] 房间端口 {RM_TCP_PORT} 监听成功 ({'IPv6' if family == socket.AF_INET6 else 'IPv4'})")
                break
            except Exception as e:
                try: s.close()
                except Exception: pass
                continue
        if sock is None:
            self.gui.log(f"[房间] 端口 {RM_TCP_PORT} 监听失败（房间模式不可用）")
            self.room_listener_running = False
            return
        self.room_listener_sock = sock
        self.gui.log(f"[房间] 已监听 {RM_TCP_PORT}（方案B：接住手机主动连接）")
        sock.settimeout(1.0)
        while self.room_listener_running:
            try:
                conn, addr = sock.accept()
                self._optimize_socket(conn)
                room_mgr = getattr(self.gui, 'room_mgr', None)
                if room_mgr is not None:
                    try:
                        if room_mgr.on_inbound(conn, addr):
                            continue
                    except Exception as e:
                        self.gui.log(f"[房间] 入站接管异常: {e}")
                # 非房间连接：直接关闭（房间端口不接受普通文件传输）
                try: conn.close()
                except Exception: pass
            except socket.timeout:
                continue
            except OSError:
                break
            except Exception as e:
                if self.room_listener_running:
                    self.gui.log(f"[房间] accept 异常: {e}")
        try: sock.close()
        except Exception: pass
        self.room_listener_sock = None
        self.room_listener_running = False

    def tcp_file_receiver(self):
        # IPv4优先，兼容性更好
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            # 关键：SO_RCVBUF 必须在 listen 之前设。TCP window scale 在
            # 三次握手时协商，accept 之后再设 RCVBUF 对已建立连接无效。
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, SOCKET_BUFFER_SIZE)
            except Exception:
                pass
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
                # 房间模式：若这是打洞入站连接，交给房间管理接管（收发走长连接）
                room_mgr = getattr(self.gui, 'room_mgr', None)
                if room_mgr is not None:
                    try:
                        if room_mgr.on_inbound(conn, addr):
                            continue
                    except Exception as e:
                        self.gui.log(f"[房间] 入站接管异常: {e}")
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
        """循环处理一条 TCP 连接上的多条消息。

        房间模式下打洞建立的是长连接，会在这条连接上串行发送多个文件；
        局域网模式下发送方发完即关闭，循环读到 EOF 自然退出。
        """
        try:
            while self.running:
                conn.settimeout(15)
                try:
                    flag_byte = recv_exact(conn, 1)
                except socket.timeout:
                    continue
                except ConnectionError:
                    break
                if not flag_byte:
                    break
                flag = flag_byte[0]
                conn.settimeout(None)
                if flag == FLAG_FILE:
                    self._receive_single_file(conn, addr)
                elif flag == FLAG_FOLDER:
                    self._receive_folder(conn, addr)
                elif flag == 0x02:
                    self._handle_query_offset(conn, addr)
                else:
                    break
        except socket.timeout:
            pass
        except Exception as e:
            error_msg = str(e)
            if error_msg and error_msg not in ('连接中断', '', 'Connection aborted', 'timed out'):
                self.gui.log(f"[接收] 连接异常: {error_msg}")
        finally:
            try:
                conn.close()
            except:
                pass

    def handle_room_receive(self, sock, io_lock, peer_key, epoch=None, room_mgr=None):
        """房间模式长连接的接收循环。

        收发共用同一条连接，用 io_lock 串行化。
        传输类型：
          · TCP：用 select 检测可读
          · UDP-RTP：用 recv_exact 超时判断（内部条件变量）

        epoch/room_mgr：接收代际。换路时新循环代际递增，旧循环检测到
        自己不再是当前代际即退出，避免新旧两个循环同时写盘。
        """
        is_udp = getattr(sock, "is_udp_rtp", False)
        # epoch 键按协议区分（与启动侧一致）
        epoch_key = peer_key + (":udp" if is_udp else ":tcp")
        try:
            while self.running:
                # 代际检查：若【同协议】通道已被替换，旧循环退出
                if room_mgr is not None and epoch is not None:
                    if not room_mgr.is_current_epoch(epoch_key, epoch):
                        self.gui.log(f"[房间接收] {epoch_key} 已被新通道接管，旧接收循环退出")
                        break
                io_lock.acquire()
                try:
                    if is_udp:
                        try:
                            sock.settimeout(0.5)
                            flag_byte = sock.recv_exact(1)
                        except TimeoutError:
                            continue
                        except Exception:
                            break
                    else:
                        r, _, _ = select.select([sock], [], [], 0.5)
                        if not r:
                            continue
                        try:
                            flag_byte = recv_exact(sock, 1)
                        except Exception:
                            break
                    if not flag_byte:
                        break
                    flag = flag_byte[0]
                    sock.settimeout(None)
                    addr = (peer_key, 0)
                    if flag == FLAG_FILE:
                        # 代际检查：写盘前确认自己仍是当前接收者
                        if (room_mgr is not None and epoch is not None
                                and not room_mgr.is_current_epoch(epoch_key, epoch)):
                            break
                        try:
                            self._receive_single_file(sock, addr)
                        except (PermissionError, OSError) as fe:
                            # 文件层错误（权限/磁盘满/路径无效）：只拒绝该文件，不关闭连接
                            self.gui.log(f"[房间接收] 文件接收失败（连接保留）: {fe}")
                            try:
                                sock.sendall(b'MISMATCH  ')
                            except Exception:
                                pass
                    elif flag == FLAG_FOLDER:
                        if (room_mgr is not None and epoch is not None
                                and not room_mgr.is_current_epoch(epoch_key, epoch)):
                            break
                        try:
                            self._receive_folder(sock, addr)
                        except (PermissionError, OSError) as fe:
                            self.gui.log(f"[房间接收] 文件夹接收失败（连接保留）: {fe}")
                            try:
                                sock.sendall(b'MISMATCH  ')
                            except Exception:
                                pass
                    elif flag == 0x02:
                        self._handle_query_offset(sock, addr)
                    elif flag == FLAG_PING:
                        # TCP 心跳探测：收到 PING 立即回 PONG（空闲长连接，不干扰数据）
                        try:
                            sock.sendall(struct.pack('!B', FLAG_PONG))
                        except Exception:
                            pass
                    elif flag == FLAG_PONG:
                        # 收到 PONG → 记录 RTT
                        rtt = tcp_rtt_mark_pong(peer_key)
                        if rtt is not None:
                            self.gui.log("[TCP] 心跳 RTT = %d ms（peer=%s）"
                                         % (rtt, peer_key))
                    else:
                        break
                finally:
                    io_lock.release()
        except Exception as e:
            self.gui.log(f"[房间接收] 连接异常: {e}")
        finally:
            try:
                sock.close()
            except Exception:
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
            # 关键：以【续传记录】为唯一判据。
            # 仅当存在匹配的 recv 续传记录（且记录里的实际文件大小 == offset）时，
            # 才回报已收字节数；否则一律回报 0（视为新文件）。
            # 这样避免"同名但不同内容"的旧文件被误判为续传起点。
            local_offset = 0
            rec_key = get_resume_file(addr[0], filename, role='recv')
            if rec_key.exists():
                try:
                    with open(rec_key, 'r') as f:
                        rec = json.load(f)
                    actual_path = Path(rec.get('filepath', ''))
                    if (actual_path and actual_path.exists()
                            and rec.get('total_size') == total_size
                            and actual_path.stat().st_size == rec.get('offset')):
                        local_offset = rec.get('offset', 0)
                except Exception:
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

    @staticmethod
    def _unique_path(save_dir, filename):
        """同名文件已存在时，生成 name(1).ext / name(2).ext ... 的可用路径。"""
        base = Path(filename)
        stem, ext = base.stem, base.suffix
        for i in range(1, 10000):
            candidate = save_dir / ("%s(%d)%s" % (stem, i, ext))
            if not candidate.exists():
                return candidate.resolve()
        # 极端情况兜底
        return (save_dir / ("%s_%d%s" % (stem, int(time.time()), ext))).resolve()

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

        # 续传记录键 = 发送方原始文件名（改名后仍用它查记录）
        resume_key = filename
        if save_path.exists():
            current_size = os.path.getsize(save_path)
            if resume and current_size == offset:
                # 真正的续传：沿用现有部分文件
                pass
            else:
                # 非续传：同名文件已被占用 → 改名 name(1).ext，不覆盖、不删除
                save_path = self._unique_path(save_dir, filename)
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
        save_resume_state(addr[0], str(save_path), offset, file_size, 0,
                          role='recv', key_name=resume_key)
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
                save_resume_state(addr[0], str(save_path), received, file_size, 0,
                                  role='recv', key_name=resume_key)
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
            delete_resume_state(addr[0], str(save_path), role='recv', key_name=resume_key)
        else:
            conn.sendall(b'MISMATCH  ')
            self.gui.log(f"[接收] 文件 {filename} 校验失败 ✗")
            # 关键：删除损坏的接收文件，否则对方重传时续传协商会再次读到它，
            # 仅凭大小回报"已收 N 字节"，导致永远从头续传到错误位置、反复失败。
            try:
                if save_path.exists():
                    save_path.unlink()
            except Exception:
                pass
            if success:
                self.gui.root.after(0, lambda: messagebox.showwarning("接收失败", f"文件 {filename} 校验失败！"))
            delete_resume_state(addr[0], str(save_path), role='recv', key_name=resume_key)

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
        # PC 端【直接接收】，不弹窗询问覆盖（避免跨线程 GUI 调用阻塞接收线程）。
        # 已存在时：保留原目录，逐文件按 size/mtime 决定 skip/resume/full（见下方逻辑）。
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
                    # 清理失败的文件 + .tmp 临时文件 + 续传状态，以便重试时从头接收
                    if dest_path.exists():
                        dest_path.unlink()
                    try:
                        tmp_cleanup = dest_path.with_suffix('.tmp')
                        if tmp_cleanup.exists():
                            tmp_cleanup.unlink()
                    except Exception:
                        pass
                    delete_folder_resume_state(addr[0], folder_name, recv_rel_path)

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
                        # 清除续传状态，重试时从头传
                        delete_folder_resume_state(target_ip, folder_name, rel_path)
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
            # 关键：清除双方续传状态，并抛出异常触发上层从头重传。
            # 否则残留的续传记录会让下次仍从错误偏移续传，反复失败。
            delete_resume_state(target_ip, filepath, role='sender')
            delete_resume_state(target_ip, filepath, role='recv')
            raise ConnectionError(f"对方校验失败: {filename}")
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

    def send_items_via_socket(self, sock, items, target_key, callback=None, io_lock=None,
                              room_mgr=None, peer_id=None):
        """在【已有的长连接 socket】上串行发送多个项目（房间模式）。

        与 send_files 的区别：
          - 不建立/关闭连接（复用打洞得到的长连接）
          - target_key 为稳定标识（用于续传状态键）
          - io_lock：发送期间持锁，避免与接收循环争抢同一 socket 的读

        【换路续传】当某项目在当前通道发送失败时，若 room_mgr/peer_id 已提供，
        则自动换到【另一条】可用路径重发该项目（最多 MAX_CHANNEL_SWITCH 次）：
          · 单文件：靠 QUERY_OFFSET 协商从断点继续，不重传已收部分
          · 文件夹：重新握手，接收端按本地文件状态 skip/resume/full
        返回：成功发送的项目数
        """
        MAX_CHANNEL_SWITCH = 3
        ok_count = 0
        cur_sock, cur_lock = sock, io_lock
        used_socks = set()
        for path, is_folder in items:
            sent_ok = False
            for switch in range(MAX_CHANNEL_SWITCH + 1):
                try:
                    if cur_lock:
                        cur_lock.acquire()
                    try:
                        # 发送前重置读超时：UDP-RTP 接收循环会设 0.5s 超时，
                        # 若发送方读 ACK 继承该超时，会立刻 TimeoutError。
                        try:
                            if getattr(cur_sock, "is_udp_rtp", False):
                                cur_sock.settimeout(0)
                            else:
                                cur_sock.settimeout(None)
                        except Exception:
                            pass
                        if is_folder:
                            self._send_folder(cur_sock, path, target_key)
                        else:
                            self._send_single_file(cur_sock, path, target_key)
                    finally:
                        if cur_lock:
                            cur_lock.release()
                    sent_ok = True
                    break
                except Exception as e:
                    used_socks.add(id(cur_sock))
                    self.gui.log(f"[房间发送] 通道失败: {e}")
                    if room_mgr is None or peer_id is None or switch >= MAX_CHANNEL_SWITCH:
                        break
                    # 取另一条可用通道（排除已失败的），换路续传
                    alt = room_mgr.get_send_channel(peer_id, exclude_socks=used_socks)
                    if alt is None:
                        # 【修复】无其他可用通道时【不立即放弃】：
                        # 触发重新打洞并等待新通道，再断点续传本项目。
                        # 旧逻辑在此直接放弃，导致 1GB 传了 41 秒后通道一断就全废。
                        self.gui.log(f"[房间发送] 无其他可用通道，触发重建并等待: {path}")
                        alt = self._wait_new_channel(room_mgr, peer_id, timeout=30.0)
                        if alt is None:
                            self.gui.log(f"[房间发送] 等待重建超时，放弃 {path}")
                            break
                        self.gui.log(f"[房间发送] 重建成功，续传 {path}")
                    cur_sock, cur_lock = alt[0], alt[1]
                    proto = "UDP-RTP" if alt[2] else "TCP"
                    self.gui.log(f"[房间发送] 换路续传 {path} -> {proto}（角色={alt[3]}）")
            if sent_ok:
                ok_count += 1
                if callback:
                    callback(path, True)
            else:
                if callback:
                    callback(path, False)
                break  # 所有通道都失败，停止后续项目
        return ok_count

    def _wait_new_channel(self, room_mgr, peer_id, timeout=30.0):
        """通道全失败后：触发重新打洞，轮询等待新通道出现。

        用于 send_items_via_socket：当所有已知通道都不可用时，不再直接放弃，
        而是请求重建并等待，拿到新通道后由上层断点续传。

        返回新的 (sock, io_lock, is_udp, role)；超时返回 None。
        """
        # 1) 触发重建（带冷却，避免频繁请求）
        try:
            rb = getattr(room_mgr, "_request_rebuild", None)
            if rb is not None:
                rb(peer_id)
        except Exception as e:
            self.gui.log(f"[房间发送] 触发重建异常: {e}")
        # 2) 轮询等待新通道
        deadline = time.time() + timeout
        while time.time() < deadline:
            time.sleep(0.5)
            try:
                chan = room_mgr.get_send_channel(peer_id)
            except Exception:
                chan = None
            if chan is not None:
                return chan
        return None

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
        self.root.title("文件互传 V%s-全网通" % APP_VERSION)
        self.root.geometry("1200x700")
        self.root.resizable(True, True)

        # 把 GUI 日志方法挂到模块级诊断钩子（供 optimize_tcp_socket 等使用）
        global _DIAG_LOG
        _DIAG_LOG = self.log

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

        # ===== 房间模式（公网信令 + TCP 打洞）状态 =====
        self.room_sig = None          # SignalingClient 实例
        self.room_mgr = None          # RoomManager 实例
        self.room_name = ""           # 当前房间号
        self._last_room_password = ""  # 缓存的房间密码（供网络重建重发）
        self.room_server = ""         # 信令服务器地址
        self.room_server_port = 0     # 信令服务器 UDP 端口
        self.room_members = []        # 房间成员（供 UI 展示）
        self.selected_room_peers = set()  # 房间成员中选中的 peer_id
        self.server_history = []      # 信令服务器地址历史（最多5个，最新在前）
        self._room_lock = threading.Lock()
        # 网络切换后房间重建：记录最近成功加入的【原始输入】与房间名
        self._last_room_server_raw = ""
        self._last_room_name = ""
        # 上次网络重建时间（冷却，避免 IP 抖动导致频繁重建）
        self._last_net_rebuild = 0.0

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

        # 0) 优先：内嵌图标（打包后自包含，无需 spec / --add-data）
        src = _materialize_embedded_icon()
        # 1) 回退：外部 icon.ico（源码运行 / 已打包 datas）
        if not src:
            candidates = []
            meipass = getattr(sys, '_MEIPASS', None)
            if meipass:
                candidates.append(os.path.join(meipass, 'icon.ico'))
            if getattr(sys, 'frozen', False):
                candidates.append(os.path.join(os.path.dirname(sys.executable), 'icon.ico'))
            else:
                candidates.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'icon.ico'))
            candidates.append(os.path.join(os.getcwd(), 'icon.ico'))
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

    def _build_room_ui(self):
        """房间模式面板：输入服务器地址与房间号加入公网房间（留空则仅局域网）。"""
        frame = ttk.LabelFrame(self.root, text="房间模式（公网互传，可留空仅用局域网）", padding=5)
        frame.pack(fill=tk.X, padx=10, pady=(0, 5))

        ttk.Label(frame, text="服务器:").pack(side=tk.LEFT, padx=2)
        # 首次使用（无历史）时预填参考服务器地址
        _hist = getattr(self, 'server_history', [])
        self.room_server_var = tk.StringVar(value=_hist[0] if _hist else DEFAULT_ROOM_SERVER)
        self.room_server_combo = ttk.Combobox(frame, textvariable=self.room_server_var,
                                              width=20,
                                              values=list(getattr(self, 'server_history', [])))
        self.room_server_combo.pack(side=tk.LEFT, padx=2)
        ttk.Label(frame, text="(可下拉选历史)", font=('', 8),
                  foreground='gray').pack(side=tk.LEFT, padx=(0, 6))

        ttk.Label(frame, text="房间号:").pack(side=tk.LEFT, padx=2)
        self.room_name_var = tk.StringVar(value="")
        ttk.Entry(frame, textvariable=self.room_name_var, width=12).pack(side=tk.LEFT, padx=2)

        # 房间密码（可选）：留空 = 开放房间（任何人凭房间号可进）；
        # 填写 = 受保护房间（必须密码匹配才能进）。仅房间创建者决定性质。
        ttk.Label(frame, text="密码:").pack(side=tk.LEFT, padx=2)
        self.room_pwd_var = tk.StringVar(value="")
        ttk.Entry(frame, textvariable=self.room_pwd_var, width=12, show="*").pack(side=tk.LEFT, padx=2)
        ttk.Label(frame, text="(留空=开放)", font=('', 8),
                  foreground='gray').pack(side=tk.LEFT, padx=(0, 6))

        self.room_join_btn = ttk.Button(frame, text="加入房间", command=self.toggle_room)
        self.room_join_btn.pack(side=tk.LEFT, padx=5)

        self.room_status_label = ttk.Label(frame, text="未加入", font=('', 9))
        self.room_status_label.pack(side=tk.LEFT, padx=5)

    def _remember_server(self, addr):
        """记录服务器地址到历史（去重、最新在前、最多 5 个），并持久化。"""
        addr = (addr or "").strip()
        if not addr:
            return
        hist = [a for a in getattr(self, 'server_history', []) if a != addr]
        hist.insert(0, addr)
        self.server_history = hist[:5]
        try:
            if hasattr(self, 'room_server_combo'):
                self.room_server_combo['values'] = list(self.server_history)
        except Exception:
            pass
        try:
            self.save_config()
        except Exception:
            pass

    def toggle_room(self):
        """加入/退出房间。"""
        if self.room_sig is not None:
            self.leave_room()
            return
        if not ROOM_AVAILABLE:
            messagebox.showerror("不可用", "房间模块加载失败，请检查 net/ 目录")
            return
        server = self.room_server_var.get().strip()
        room = self.room_name_var.get().strip()
        pwd = self.room_pwd_var.get().strip()
        if not server or not room:
            messagebox.showwarning("参数缺失", "请输入服务器地址与房间号")
            return
        self.join_room(server, room, password=pwd)

    def join_room(self, server, room, reuse_id=None, password=None):
        """连接信令服务器并加入房间。

        reuse_id：网络切换重建时传入上次的 my_id，让服务器复用（不换 id）。
        password：房间密码（可选）。空/None = 开放房间。
        """
        try:
            # 支持三种格式：host / host:port / [ipv6]:port / 纯 IPv6
            host = server.strip()
            port = RM_DEFAULT_SERVER_PORT
            if host.startswith("["):
                # [ipv6]:port 或 [ipv6]
                close = host.find("]")
                if close < 0:
                    raise ValueError("服务器地址格式错误：缺少 ]")
                inner = host[1:close]
                rest = host[close + 1:]
                if rest.startswith(":"):
                    port = int(rest[1:])
                host = inner
            elif ":" in host:
                # 判断是 host:port 还是纯 IPv6
                if host.count(":") == 1:
                    h, ps = host.rsplit(":", 1)
                    host, port = h, int(ps)
                # 含多个冒号 → 纯 IPv6，host 保持不变，用默认端口
            self.room_server = host
            self.room_server_port = port
            self.room_name = room
            # 记录原始输入，供网络切换后自动重建
            self._last_room_server_raw = server.strip()
            self._last_room_name = room
            # 缓存密码：网络切换重建时一并重发（重建不带密码会连不上受保护房间）
            if password is not None:
                self._last_room_password = password
            password = getattr(self, "_last_room_password", "")

            # 单一来源：设备标识统一从 Node.device_id 取（Node 启动时已生成/持久化）
            device_id = self.node.device_id
            # 单一来源：本机 IP 统一从 Node 取（my_ips/my_ips_v6 会随网络变化刷新）。
            # 过滤 IPv6 链路本地（fe80::/10）与回环——它们无法跨网段使用。
            lan_ips = list(self.node.my_ips)
            for v6 in self.node.my_ips_v6:
                low = v6.lower()
                if low.startswith("fe80:") or low == "::1":
                    continue
                lan_ips.append(v6)
            punch_port = RM_TCP_PORT

            self.room_mgr = RoomManager(None, punch_port, log=self.log)
            # C/S 降级：房间端口未成功监听时一律主动 connect。
            self.room_mgr.room_port_bound_checker = (
                lambda: self.node.room_listener_sock is not None)
            self.room_mgr.on_socket_ready = self._on_room_socket_ready
            self.room_mgr.on_udp_ready = self._on_room_udp_ready
            # 成员状态变化（保活检测到死亡/重建成功等）→ 派发到主线程刷新 UI
            self.room_mgr.on_state_changed = lambda: self.root.after(0, self.refresh_nodes)
            self.room_mgr.start()

            self.room_sig = SignalingClient(
                server_ip=host, server_port=port, room=room,
                name=self.device_name, tcp_port=RM_TCP_PORT, lan_ips=lan_ips,
                on_joined=self._on_room_joined,
                on_member_join=self._on_room_member_join,
                on_member_leave=self._on_room_member_leave,
                on_punch_go=self._on_room_punch_go,
                on_error=self._on_room_error,
                log=self.log,
                server_tcp_port=RM_DEFAULT_SERVER_TCP_PORT,
                punch_local_port=punch_port,
                device_id=device_id,
                on_udp_hole_ready=self._on_udp_hole_ready,
                on_mapping_ready=self.room_mgr.mark_mapping_ready,
                reuse_id=reuse_id,
                password=password,
            )
            self.room_mgr.signaling = self.room_sig
            ok = self.room_sig.start()

            # 标准 C/S（替代脆弱的"同时打开"）：
            #   电脑 listen 房间端口（与映射 socket 同端口共存——已在本地
            #   验证：Windows 上 listen socket 能接住入站连接，且不影响
            #   ESTABLISHED 的映射 socket）。
            #   规则：ID 小者 listen 等对方 connect，ID 大者主动 connect。
            #   这样入站 SYN 由 LISTEN socket 按规则接住（不再靠 SYN_SENT
            #   的 socket 碰运气），NAT 全支持，异构设备（电脑↔手机）也稳。
            if ok:
                try:
                    self.node.start_room_listener()
                except Exception as e:
                    self.log(f"[房间] 启动房间监听失败: {e}")

            if not ok:
                # 连接失败：清理，不显示"已加入"
                self.log("[房间] 加入失败：无法连接信令服务器")
                try:
                    self.room_mgr.stop()
                except Exception:
                    pass
                self.room_sig = None
                self.room_mgr = None
                self.root.after(0, lambda: self.room_status_label.config(text="连接失败"))
                return

            self._remember_server(server)

            self.root.after(0, lambda: self.room_join_btn.config(text="退出房间"))
            self.root.after(0, lambda: self.room_status_label.config(
                text=f"已加入 {room} @ {host}"))
            self.log(f"[房间] 已加入房间 {room}（服务器 {host}:{port}）")
        except Exception as e:
            self.log(f"[房间] 加入失败: {e}")
            self.room_sig = None
            self.room_mgr = None
            messagebox.showerror("加入失败", str(e))

    def leave_room(self, quiet=False):
        """退出房间，停止信令与房间管理。

        quiet=True：网络切换重建用——不发 BYE，保留服务器侧成员条目与重建参数。

        顺序关键：先关 RoomManager（内部含所有 UdpReliableSocket，会停
        keepalive 线程），再关 SignalingClient（含 udp_hole_sock）。
        若先关 udp_hole_sock，UdpReliableSocket 的 keepalive 线程仍在跑，
        会用它发送 → WinError 10038。
        """
        # 方案 B：先停房间监听（避免它持有 9998 影响映射 socket 释放）
        try:
            self.node.stop_room_listener()
        except Exception:
            pass
        try:
            if self.room_mgr:
                self.room_mgr.stop()
        except Exception:
            pass
        try:
            if self.room_sig:
                self.room_sig.stop(quiet=quiet)
        except Exception:
            pass
        self.room_sig = None
        self.room_mgr = None
        self.room_name = ""
        if not quiet:
            # 清除重建参数：用户主动退出后，后续网络变化不应再重建该房间。
            # （网络切换触发的重建保留参数，重建完成后继续可用）
            self._last_room_server_raw = ""
            self._last_room_name = ""
            self._last_room_password = ""   # 主动退出：清空密码缓存
        with self._room_lock:
            self.room_members = []
        self.selected_room_peers = set()
        self.root.after(0, lambda: self.room_join_btn.config(text="加入房间"))
        self.root.after(0, lambda: self.room_status_label.config(text="未加入"))
        try:
            self.refresh_nodes()
        except Exception:
            pass
        self.log("[房间] 已退出房间")

    def _on_local_network_changed(self):
        """本机网络切换（IP 集合变化）→ 若在房间中，重建房间连接。

        原因：房间模式的信令 UDP socket、TCP 映射观测连接、打洞长连接
        都绑定在旧网络的路由/源 IP 上。网络一旦切换，旧 NAT 映射全部失效：
          · 信令心跳失联，服务器 30 秒后剔除本机；
          · 服务器看到的 pub_tcp / pub_udp 是旧网络的，打洞必然失败。
        重建方式：leave_room() + join_room(原参数)，所有 socket 用新网络
        重开、重新登记映射、重新打洞。

        仅当【当前在房间中】时触发，并加 10 秒冷却避免 IP 抖动频繁重建。
        由 Node._refresh_my_ips 在本机 IP 变化时调用（Node 后台线程）。
        """
        if self.room_sig is None or not self._last_room_server_raw or not self._last_room_name:
            return
        now = time.time()
        if now - self._last_net_rebuild < 10.0:
            return
        self._last_net_rebuild = now
        srv = self._last_room_server_raw
        rn = self._last_room_name
        # 保存旧 my_id，重建时传给服务器复用（身份稳定，不换 id）
        old_id = self.room_sig.my_id if self.room_sig else None
        self.log("[房间] 检测到网络切换，重建房间连接（server=%s, room=%s, 复用ID=%s）"
                 % (srv, rn, old_id))
        # 调度到 Tk 主线程执行：join_room / leave_room 内部会操作 UI 并阻塞
        # （wait_joined 最多数秒），不能直接从本后台线程调用。
        try:
            self.root.after(0, lambda: self._do_network_rebuild(srv, rn, old_id))
        except Exception:
            pass

    def _do_network_rebuild(self, srv, rn, old_id):
        """网络切换重建（运行在 Tk 主线程）：静默退出 → 延迟 → 复用 id 重新加入。

        用 quiet=True：不发 BYE，服务器保留本成员条目，对端不掉线；
        重建后以同一 id 回归，实现"无感重建"。
        """
        try:
            self.leave_room(quiet=True)
        except Exception as e:
            self.log("[房间] 网络切换重建：退出房间异常 %s" % e)
        # 延迟 0.8 秒等新网络路由收敛，再重新加入（带旧 id 复用身份）
        try:
            self.root.after(800, lambda: self._rejoin_room(srv, rn, old_id))
        except Exception:
            pass

    def _rejoin_room(self, srv, rn, old_id=None):
        """重新加入房间（网络切换重建的第二步）。"""
        try:
            self.join_room(srv, rn, reuse_id=old_id)
        except Exception as e:
            self.log("[房间] 网络切换重建失败: %s" % e)

    # ---------- 房间回调 ----------
    def _on_room_joined(self, members):
        self.room_mgr.on_joined(members)
        self.root.after(0, self.refresh_nodes)

    def _on_room_member_join(self, member):
        self.room_mgr.on_member_join(member)
        self.root.after(0, self.refresh_nodes)

    def _on_room_member_leave(self, peer_id):
        self.room_mgr.on_member_leave(peer_id)
        self.root.after(0, self.refresh_nodes)

    def _on_room_punch_go(self, peer, at_ms):
        self.room_mgr.on_punch_go(peer, at_ms)

    def _on_room_error(self, code):
        self.log(f"[房间] 服务器返回错误: {code}")
        if code == "ROOM_FULL":
            self.root.after(0, lambda: messagebox.showwarning("房间已满", "该房间人数已达上限"))
        elif code == "NEED_PASSWORD":
            self.root.after(0, lambda: messagebox.showerror(
                "需要密码", "该房间已设置密码，请填写正确的房间密码后重试"))
        elif code == "BAD_PASSWORD":
            self.root.after(0, lambda: messagebox.showerror(
                "密码错误", "房间密码不正确，无法加入"))
        elif code == "ROOM_OPEN":
            self.root.after(0, lambda: messagebox.showwarning(
                "开放房间", "该房间是开放房间（无密码）。\n请清空密码栏后再加入。"))

    def _on_room_socket_ready(self, peer_id, sock, member):
        """打洞成功：启动长连接的收发循环（收发共用同一 socket，用 io_lock 串行化）。"""
        try:
            peer = sock.getpeername()
        except Exception:
            peer = "?"
        conn = self.room_mgr.get_conn(peer_id)
        io_lock = conn.io_lock if conn else threading.Lock()
        peer_key = self.room_mgr.get_peer_did(peer_id)
        self.log(f"[房间] 长连接就绪 {member.get('name', peer_id)} <-> {peer}")
        # epoch 键按协议区分：TCP 启动不应误杀 UDP 的接收循环
        epoch = self.room_mgr.new_recv_epoch(peer_key + ":tcp")
        threading.Thread(target=self.node.handle_room_receive,
                         args=(sock, io_lock, peer_key, epoch, self.room_mgr),
                         daemon=True).start()
        self.root.after(0, self.refresh_nodes)

    def _on_udp_hole_ready(self, peer_id, peer_addr):
        """信令客户端通知 UDP 打洞成功 → 交给 RoomManager 建立可靠通道。"""
        if self.room_mgr:
            self.room_mgr.on_udp_hole_ready(peer_id, peer_addr)

    def _on_room_udp_ready(self, peer_id, rtp):
        """UDP-RTP 通道就绪：启动接收循环。"""
        try:
            peer = rtp.getpeername()
        except Exception:
            peer = "?"
        peer_key = self.room_mgr.get_peer_did(peer_id)
        self.log(f"[房间] UDP-RTP 通道就绪 peer={peer_id} <-> {peer}，启动接收循环")
        # 关键：用 rtp 自带的 io_lock（与发送侧 get_send_channel 返回的是同一把），
        # 保证应用层收发互斥。
        # epoch 键按协议区分
        epoch = self.room_mgr.new_recv_epoch(peer_key + ":udp")
        threading.Thread(target=self.node.handle_room_receive,
                         args=(rtp, rtp.io_lock, peer_key, epoch, self.room_mgr),
                         daemon=True).start()
        self.root.after(0, self.refresh_nodes)

    def send_room_action(self):
        """向选中的房间成员发送待发送列表（复用打洞长连接）。"""
        # 防重：正在发送中则忽略（避免连点导致重复发送）
        if self._sending:
            self.log("[发送] 正在发送中，请等待当前任务完成")
            return
        if self.room_mgr is None:
            messagebox.showwarning("未加入房间", "请先加入房间")
            return
        if not self.selected_room_peers:
            messagebox.showwarning("未选择成员", "请在设备列表中选择房间成员")
            return
        with self._items_lock:
            items = list(self.send_items)
        if not items:
            messagebox.showwarning("列表为空", "请添加要发送的文件或文件夹")
            return
        targets = list(self.selected_room_peers)

        self._sending = True
        try:
            self.send_btn.config(text="发送中...", state='disabled')
        except Exception:
            pass

        def do_send():
            try:
                for pid in targets:
                    key = self.room_mgr.get_peer_did(pid)
                    # 连接池：标记活跃，重置该 peer 的空闲计时（保活恢复正常频率）
                    try:
                        self.room_mgr.mark_active(pid)
                    except Exception:
                        pass
                    # 梯度冗余：按路径角色选路（hot → warm_safe → warm_loose）
                    chan = self.room_mgr.get_send_channel(pid)
                    if chan:
                        sock, io_lock, is_udp, role = chan
                        proto = "UDP-RTP" if is_udp else "TCP"
                        self.log(f"[房间发送] 向 {pid} 发送（{proto}，角色={role}）{len(items)} 个项目")
                        # 传入 room_mgr/peer_id → 通道失败时自动换路续传
                        self.node.send_items_via_socket(
                            sock, items, key, io_lock=io_lock,
                            room_mgr=self.room_mgr, peer_id=pid)
                        continue
                    self.log(f"[房间发送] {pid} 无可用通道（TCP/UDP 均未就绪），跳过")
                # 连接池：传输结束再标记一次（空闲计时从此刻算起）
                for pid in targets:
                    try:
                        self.room_mgr.mark_active(pid)
                    except Exception:
                        pass
                self.root.after(0, lambda: self.log("[房间发送] 完成"))
            finally:
                self._sending = False
                try:
                    self.root.after(0, lambda: self.send_btn.config(
                        text="极速发送", state='normal'))
                except Exception:
                    pass

        threading.Thread(target=do_send, daemon=True).start()

    # ---------- 统一设备列表：提取辅助 ----------
    @staticmethod
    def _extract_bracket(text):
        """提取文本中最后一个 [...] 的内容（房间 peer_id）。"""
        close = text.rfind(']')
        if close < 0:
            return None
        open_ = text.rfind('[', 0, close)
        if open_ < 0 or close <= open_ + 1:
            return None
        return text[open_ + 1:close]

    @staticmethod
    def _extract_paren(text):
        """提取文本中最后一个 (...) 的内容（局域网 IP）。"""
        close = text.rfind(')')
        if close < 0:
            return None
        open_ = text.rfind('(', 0, close)
        if open_ < 0 or close <= open_ + 1:
            return None
        return text[open_ + 1:close]

    def _build_ui(self):
        # 显示当前保存路径（置于最上方）
        self.save_dir_label = ttk.Label(self.root, text=f"保存位置: {SAVE_DIR}", font=('', 9))
        self.save_dir_label.pack(anchor='w', padx=10, pady=(10, 5))

        # ---- 房间模式（公网信令 + 打洞）----
        self._build_room_ui()

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
        """统一列表选中：区分【房间设备】（末尾 [peer_id]）与局域网设备（末尾 (ip)）。"""
        selected_indices = self.node_listbox.curselection()
        lan_ips = set()
        room_peers = set()
        for i in selected_indices:
            text = self.node_listbox.get(i)
            if text.startswith("[房间设备]"):
                pid = self._extract_bracket(text)
                if pid:
                    room_peers.add(pid)
            else:
                ip = self._extract_paren(text)
                if ip:
                    lan_ips.add(ip)
        self.selected_ips = lan_ips
        self.selected_room_peers = room_peers
        parts = []
        if room_peers:
            parts.append(f"房间 {len(room_peers)} 台")
        if lan_ips:
            parts.append(f"局域网 {len(lan_ips)} 台")
        self.log("已选择设备: " + ("，".join(parts) if parts else "无"))

    def refresh_nodes(self):
        """统一刷新设备列表：局域网设备 + 房间成员合并显示。

        局域网设备: 名字 (ip)
        房间成员:   [房间设备] 名字 (状态 地址) [peer_id]
        """
        prev_ips = set(getattr(self, 'selected_ips', set()))
        prev_peers = set(getattr(self, 'selected_room_peers', set()))
        self.node_listbox.delete(0, tk.END)

        # 1) 房间成员（置顶，带标识）
        try:
            members = self.room_mgr.get_members() if self.room_mgr else []
        except Exception:
            members = []
        for m in members:
            state = m.get('state', 'idle')
            addr = m.get('addr')
            addr_txt = ""
            if addr:
                try:
                    addr_txt = " %s:%d" % (addr[0], addr[1])
                except Exception:
                    addr_txt = " " + str(addr)
            display = "[房间设备] %s (%s%s) [%s]" % (
                m.get('name', ''), state, addr_txt, m['id'])
            self.node_listbox.insert(tk.END, display)

        # 2) 局域网设备
        with self.node.lock:
            items = sorted(self.node.nodes.items(), key=lambda x: x[1]['last_seen'], reverse=True)
        for ip, node in items:
            self.node_listbox.insert(tk.END, "%s (%s)" % (node['hostname'], ip))

        # 恢复选中
        new_ips = set()
        new_peers = set()
        for i in range(self.node_listbox.size()):
            text = self.node_listbox.get(i)
            if text.startswith("[房间设备]"):
                pid = self._extract_bracket(text)
                if pid and pid in prev_peers:
                    self.node_listbox.selection_set(i)
                    new_peers.add(pid)
            else:
                ip = self._extract_paren(text)
                if ip and ip in prev_ips:
                    self.node_listbox.selection_set(i)
                    new_ips.add(ip)
        self.selected_ips = new_ips
        self.selected_room_peers = new_peers

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
        # 同时刷新房间成员状态显示（房间内成员由保活循环持续检测）
        try:
            self.root.after(0, self.refresh_nodes)
        except Exception:
            pass

    def log(self, msg):
        """写事件日志（线程安全）。

        tkinter 不是线程安全的：后台线程直接操作控件会与主线程 mainloop
        争锁，打包成 exe 后时序变化极易死锁（表现为界面卡死）。
        故：主线程直接写；后台线程用 root.after(0, ...) 派发到主线程。
        """
        logging.info(msg)
        # 已在主线程：直接更新
        if threading.current_thread() is threading.main_thread():
            self._append_log_ui(msg)
        else:
            # 后台线程：派发到主线程执行
            try:
                self.root.after(0, lambda m=msg: self._append_log_ui(m))
            except Exception:
                pass

    def _append_log_ui(self, msg):
        """真正操作控件（只允许主线程调用）。"""
        try:
            timestamp = datetime.now().strftime("%H:%M:%S")
            self.log_text.config(state='normal')
            self.log_text.insert(tk.END, f"[{timestamp}] {msg}\n")
            self.log_text.see(tk.END)
            self.log_text.config(state='disabled')
        except Exception:
            pass

    def set_status(self, msg):
        """更新状态栏（线程安全）。"""
        def _do():
            try:
                self.status.config(text=msg)
            except Exception:
                pass
        if threading.current_thread() is threading.main_thread():
            _do()
        else:
            try:
                self.root.after(0, _do)
            except Exception:
                pass

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
        # 房间成员优先：若在房间中选中了成员，则走房间发送（复用打洞长连接）
        if self.selected_room_peers and self.room_mgr is not None:
            self.send_room_action()
            return
        if not self.selected_ips:
            messagebox.showwarning("未选择设备", "请先在设备列表或房间成员中选中至少一台设备")
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

        # 退出房间（停止信令与打洞连接）
        try:
            if self.room_sig is not None:
                self.leave_room()
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
                    hist = cfg.get('server_history')
                    if isinstance(hist, list):
                        self.server_history = [str(x) for x in hist][:5]
            except:
                pass
        SAVE_DIR.mkdir(parents=True, exist_ok=True)
        self.update_save_dir_label()
        # 服务器地址历史填入下拉框；有历史则预填最近一条，否则保持参考地址
        try:
            if hasattr(self, 'room_server_combo'):
                self.room_server_combo['values'] = list(self.server_history)
                if self.server_history:
                    self.room_server_var.set(self.server_history[0])
        except Exception:
            pass

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
            'server_history': list(getattr(self, 'server_history', []))[:5],
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
    # 启动时清理自身生成的过期文件（续传 JSON / 日志）
    try:
        cleanup_generated_files(log=lambda m: logging.info(m))
    except Exception:
        pass
    app = P2PApp()
    if files:
        # 冷启动时把命令行参数里的文件/文件夹加入待发送列表（等 GUI 就绪）
        app.root.after(300, lambda ps=files: app._add_paths_from_ipc(ps))
    app.run()
