package com.p2p.filetransfer.model

import android.net.Uri
import java.io.File

/**
 * 待发送项目：文件或文件夹。
 * 使用 [file] 表示普通文件系统路径；[treeUri] 用于 SAF 选择的内容 Uri（暂存）。
 */
data class TransferItem(
    val path: String,
    val isFolder: Boolean,
    /** 可选，SAF 选择的 Uri 字符串 */
    val uriString: String? = null
) {
    fun asFile(): File = File(path)
}

/** 一次传输进度快照 */
data class TransferProgress(
    val percent: Float = 0f,
    val speedText: String = "",
    val remainText: String = "",
    val statusText: String = ""
)

/** 接收请求（后台服务通知 UI） */
data class ReceiveRequest(
    val fromIp: String,
    val fileName: String,
    val totalSize: Long,
    val isFolder: Boolean = false
)

/** 携带 ID 的待确认接收请求，UI 通过 [id] 向 Service 回传用户选择 */
data class IncomingRequest(
    val id: String,
    val fromIp: String,
    val fileName: String,
    val totalSize: Long,
    val isFolder: Boolean = false
)

/** 续传提醒中的单个项目 */
data class ResumeReminderItem(
    val filepath: String,
    val relPath: String?,
    val offset: Long,
    val totalSize: Long,
    val isFolder: Boolean
)

/** 设备上线时的续传提醒 */
data class ResumeReminder(
    val id: String,
    val fromIp: String,
    val hostname: String,
    val items: List<ResumeReminderItem>
)
