# P2P 文件互传 Android 应用

本项目为 Python 桌面端 `文件互传.py` 的 Android 移植版本，使用 Kotlin + Jetpack Compose 实现，
与桌面端协议完全兼容（UDP 设备发现 + TCP 文件传输 + 断点续传 + zlib 压缩 + SHA-256 校验）。

## 环境要求

- Android Studio Ladybug (2024.2) 或更高
- JDK 17 或 21（Android Studio 会自动下载）
- Android SDK 35
- Gradle 8.7（wrapper 已配置）

## 构建与运行

1. 使用 Android Studio 打开 `android/` 目录。
2. 等待 Gradle Sync 完成（首次会自动下载 JDK 和依赖）。
3. 连接 Android 7.0+（API 24）设备，直接点击 Run。
4. 首次启动会请求通知 / Wi-Fi 相关权限，请全部允许。
5. 建议在系统设置中允许应用“忽略电池优化”，以避免后台网络被系统杀死。

## 与桌面端联调

1. 手机与电脑连接同一 Wi-Fi（或同一 VPN 网络如 ZeroTier/Tailscale）。
2. 启动电脑端 `文件互传.py`。
3. 手机上点击【广播搜索】或【扫描子网】，电脑端应出现在“在线设备”列表中。
4. 也可以在电脑端点击【自动搜索】/【手动添加】，输入手机 IP。
5. 手机端选择设备 → 添加文件/文件夹 → 点击【极速发送】。

## 功能对照（桌面端）

| 功能 | 状态 |
|------|------|
| UDP 设备发现（广播 + 扫描 + 心跳） | 支持 |
| 单文件传输 | 支持 |
| 文件夹传输（增量 skip/full/resume） | 支持 |
| 断点续传（单文件 + 文件夹） | 支持 |
| 文本文件 zlib 压缩 | 支持 |
| SHA-256 完整性校验 | 支持 |
| 动态缓冲区 | 发送端自适应（16KB~2MB）+ 接收端动态（256KB~4MB） |
| IPv6 双栈 | UDP 监听（IPv4+IPv6）+ 手动添加（v4/v6） |
| 前台服务保活 | 支持 |
| Wi-Fi/CPU 唤醒锁 | 支持 |
| 来件确认弹窗 | Service 与 UI 交互，60s 超时默认拒绝 |
| 传输中暂停广播/扫描/心跳 | 支持 |
| 指数退避重试 | 2/4/8s，最多 3 次 |
| 静默超时（低网络触发续传） | 接收 60s / 发送 30s |
| 设备上线自动续传提醒 | 弹窗询问 |
| 传输中实时保存续传进度 | 每 5s / 每 10% |
| 发送前离线检查 | 支持 |
| 文件打开失败重试 | 3 次 |
| 保存目录选择 | 支持 |
| 手动输入网段扫描 | 支持 |
| mtime 精度兼容 | 1ms 容差 |
| 路径穿越防护 | 支持 |

## 端口

- UDP 9998: 设备发现（心跳/广播/回复）
- UDP 9997: 扫描探测
- TCP 9999: 文件传输

## 存储位置

- 接收文件默认保存到应用外部私有目录 `Android/data/com.p2p.filetransfer/files/Received/`。
- 发送文件时，SAF 选择的内容会先复制到应用缓存目录 `cache/to_send/`，再传输。
- 续传状态保存在应用私有目录 `.p2p_resume/`，命名规则与桌面端一致。

## 目录结构

```
android/app/src/main/java/com/p2p/filetransfer/
├── MainActivity.kt               # 主界面 + ViewModel + Compose Screen
├── P2PApplication.kt             # Application + 通知渠道
├── P2PFileTransferService.kt     # 前台服务（网络核心）
├── model/                        # DeviceNode / ResumeState / TransferItem / IncomingRequest
├── protocol/                     # 常量 + 二进制读写 + UDP 发现 + TCP 传输
├── repository/                   # 续传状态 + 设备列表管理
├── ui/                           # Compose 组件（设备/文件/进度/日志）
└── util/                         # Socket 优化 / 哈希 / 子网 / 格式化
```

## 已知限制

- 无零拷贝 `sendfile`（Android 无对应 API），全部走普通缓冲读写。
- UDP 广播在部分 ROM 被限，需优先使用“扫描子网”。
- 子网扫描仅支持 IPv4；IPv6 通过手动添加 + 心跳维持。
- 扫描并发 200（桌面端 1000），可在 `Constants.SCAN_CONCURRENCY` 调整。
- SAF 选择的内容会先复制到应用缓存再发送，可能额外占用缓存空间。
- 文件夹续传自动重发仅限单文件；文件夹续传需手动重新添加文件夹。

# 项目结构与代码简概

## 一、目录总览

```
android/
├── build.gradle.kts                    # 根构建（AGP 8.5.2 + Kotlin 1.9.24）
├── settings.gradle.kts                 # 模块声明 + 仓库配置
├── gradle.properties                   # JVM 参数、AndroidX、overridePathCheck
├── gradle/wrapper/                     # Gradle 8.7 wrapper
├── icon.ico                            # 源图标（转成了 mipmap PNG）
├── README.md                           # 使用说明
└── app/
    ├── build.gradle.kts                # app 模块配置 + 依赖
    ├── proguard-rules.pro
    └── src/main/
        ├── AndroidManifest.xml         # 权限 + Activity + Service 声明
        ├── java/com/p2p/filetransfer/  # Kotlin 源码（26 个文件，约 4300 行）
        └── res/
            ├── mipmap-*/               # 5 档启动图标 PNG
            ├── drawable/               # 旧自适应图标（保留但未用）
            ├── values/                 # strings / colors / themes
            └── xml/                    # backup / data_extraction 规则
```

## 二、Kotlin 源码结构（26 个文件）

### 入口层（3 个）

| 文件 | 行数 | 职责 |
|---|---:|---|
| `MainActivity.kt` | 796 | UI + ViewModel + Compose Screen（手机单列布局）|
| `P2PApplication.kt` | 48 | Application 子类，创建 3 个通知渠道 |
| `P2PFileTransferService.kt` | 458 | **前台服务**，网络核心（UDP + TCP + 广播/心跳/暂停）|

### 模型层（3 个）

| 文件 | 行数 | 内容 |
|---|---:|---|
| `model/DeviceNode.kt` | 12 | 在线设备数据类（ip/hostname/lastSeen/source/heartbeatFail）|
| `model/ResumeState.kt` | 29 | 单文件续传状态 + 文件夹续传状态 |
| `model/TransferItem.kt` | 60 | 待发送项 + 进度快照 + 来件请求 + 续传提醒 |

### 协议层（5 个）

| 文件 | 行数 | 职责 |
|---|---:|---|
| `protocol/Constants.kt` | 90 | 端口/超时/缓冲区/自适应算法/压缩白名单 |
| `protocol/ProtoReader.kt` | 58 | 大端序二进制读取（readExact/readInt32/readInt64/readDouble）|
| `protocol/ProtoWriter.kt` | 53 | 大端序二进制写入 |
| `protocol/UdpDiscovery.kt` | 560 | **设备发现**：广播/扫描/心跳/清理 + IPv4&IPv6 监听 |
| `protocol/TcpFileTransfer.kt` | **1178** | **文件传输核心**：单文件/文件夹/续传/压缩/校验 |

### 仓库层（2 个）

| 文件 | 行数 | 职责 |
|---|---:|---|
| `repository/DeviceRepository.kt` | 109 | 设备列表内存管理 + StateFlow 推送 |
| `repository/ResumeRepository.kt` | 172 | 续传状态 JSON 持久化（与 Python 端同格式）|

### 工具层（5 个）

| 文件 | 行数 | 职责 |
|---|---:|---|
| `util/NetworkUtil.kt` | 67 | 枚举本机 IPv4/IPv6、主机名 |
| `util/SubnetUtil.kt` | 85 | CIDR 推断 + 主机列表生成 |
| `util/HashUtil.kt` | 63 | SHA-256（整文件/分段）|
| `util/SocketOptimizer.kt` | 55 | TCP_NODELAY + KeepAlive + 大缓冲（逐级尝试）|
| `util/SizeFormatter.kt` | 38 | 大小/速度/时间格式化 |
| `util/PublicStorage.kt` | 261 | **文件发布**：默认 MediaStore Downloads + 用户 SAF 树 |

### UI 层（7 个）

| 文件 | 行数 | 职责 |
|---|---:|---|
| `ui/DeviceList.kt` | 62 | 旧版设备列表（已由 MainActivity 内联，保留）|
| `ui/FileList.kt` | 66 | 旧版文件列表（同上）|
| `ui/LogView.kt` | 35 | 日志滚动区 |
| `ui/ProgressView.kt` | 44 | 进度条组件 |
| `ui/theme/Color.kt` | 12 | 主题色定义 |
| `ui/theme/Theme.kt` | 46 | Material3 主题 + 动态色 |
| `ui/theme/Type.kt` | 18 | 字体排版 |

## 三、核心类职责

### `P2PFileTransferService`（前台服务）

```
生命周期: onCreate → 初始化 DeviceRepository + ResumeRepository
                  → 创建 UdpDiscovery + TcpFileTransfer
                  → startForeground + Wi-Fi 锁 + CPU 唤醒锁
                  → udp.start() / tcp.startServer() / udp.startAutoScan()
         onDestroy → 停止所有 + 释放锁

对外 API:
  broadcastSearch() / scanAllSubnets() / scanSubnet(cidr)
  addManualNode(ip) / refreshOnline() / toggleAutoScan()
  sendItems(targetIps, items)
  setSaveTreeUri(uri) / clearSaveTreeUri()
  respondIncoming(id, accept) / respondResumeReminder(id, accept)

对外状态流（StateFlow / SharedFlow）:
  isRunning / autoScan / sendProgress / recvProgress
  logs / incomingRequest / resumeReminder / saveDirPath
```

### `TcpFileTransfer`（协议核心，1178 行）

```
startServer(scope)          —— ServerSocket 监听 9999，accept 循环
  └─ handleReceive(socket)  —— 循环读取 flag（修复：同连接多消息）
       ├─ FLAG_FILE (0x00)     → receiveSingleFile(peerIp)
       ├─ FLAG_FOLDER (0x01)   → receiveFolder(peerIp)
       └─ FLAG_QUERY_OFFSET    → handleQueryOffset(reader)

sendFiles(ip, items, cb)    —— 串行发送多个项目
  └─ sendItemWithRetry      —— 指数退避重试
       ├─ sendFolder        —— 清单 + skip/full/resume 指令 + 逐文件
       └─ sendSingleFile    —— QUERY_OFFSET 协商 + FLAG_FILE 发送
            └─ sendFileDataFast —— BufferedInputStream + 1MB 自适应块

receiveData(socket, ...)    —— 固定 1MB 双缓冲，零分配写盘 + 实时 SHA256
```

### `UdpDiscovery`（设备发现，560 行）

```
start(scope)
  ├─ udpListenerLoop()     —— 监听 9998 (IPv4)
  ├─ udpListenerV6Loop()   —— 监听 9998 (IPv6)
  ├─ scanListenerLoop()    —— 监听 9997 (IPv4)
  ├─ scanListenerV6Loop()  —— 监听 9997 (IPv6)
  ├─ broadcasterLoop()     —— 启动 5 秒内爆发广播，之后停止
  └─ cleanLoop()           —— 每 2 秒清理超时节点

startAutoScan(scope)       —— 每 20 秒：扫子网 + 心跳检测
```

## 四、数据流

```
┌─────────────────────────────────────────────────────────┐
│  UI (Compose)                                            │
│    MainActivity ──► MainViewModel ──► P2PFileTransferService.instance
└───────────────────────────┬─────────────────────────────┘
                            │  StateFlow / SharedFlow
┌───────────────────────────▼─────────────────────────────┐
│  P2PFileTransferService (ForegroundService)              │
│    ├─ DeviceRepository (StateFlow<List<DeviceNode>>)     │
│    ├─ ResumeRepository (.p2p_resume/*.json)              │
│    ├─ UdpDiscovery  ◄──► UDP 9997/9998                   │
│    └─ TcpFileTransfer ◄──► TCP 9999                      │
└───────────────────────────┬─────────────────────────────┘
                            │ File
┌───────────────────────────▼─────────────────────────────┐
│  PublicStorage                                           │
│    ├─ 默认: MediaStore.Downloads/P2PFileTransfer/        │
│    └─ 用户SAF: DocumentFile 写入用户选择的树目录          │
└─────────────────────────────────────────────────────────┘
```

## 五、与桌面端协议对齐

| 项 | 值 |
|---|---|
| UDP 发现 | 9998 |
| UDP 扫描 | 9997 |
| TCP 传输 | 9999 |
| 端序 | 大端序 |
| SHA-256 | 64 字节 hex |
| 压缩 | zlib level 6（22 种文本扩展名）|
| 续传状态 | `.p2p_resume/resume_{role}_{ip}_{file}.json` |
| 心跳阈值 | 3 次失败移除 |
| 节点超时 | 600 秒 |
| 重试退避 | 2/4/8 秒，最多 3 次 |

## 六、关键修复过的坑

1. **同连接多消息** —— 桌面端非压缩单文件时"查询 + 发送"复用同一 TCP 连接
2. **offset 未清零** —— Python 端 QUERY_OFFSET 返回 0 时也需清 `offset` + `FLAG_RESUME`
3. **预读跳数据** —— Python 端 `_send_file_data_fast` 的预读逻辑导致数据丢失
4. **续传偏移校验** —— Android 端本地大小 ≠ 发送方 offset 时拒绝而非覆盖写
5. **零拷贝写盘** —— 接收端去掉 `ByteArrayOutputStream.toByteArray()`
6. **负反馈缓冲区** —— 去掉"速度慢 → 缓冲小 → 更慢"的调整
7. **广播刷屏** —— Android 端只在启动 5 秒内广播，避免桌面端每秒弹续传窗

## 七、代码量统计

| 分类 | 文件数 | 行数 |
|---|---:|---:|
| 核心协议（Tcp+Udp+Proto）| 5 | 1939 |
| 服务与入口 | 3 | 1302 |
| UI + 主题 | 7 | 283 |
| 模型 + 仓库 | 5 | 382 |
| 工具 | 6 | 569 |
| **合计** | **26** | **~4475** |