package com.p2p.filetransfer.model

/**
 * 单文件续传状态（JSON 持久化模型，与桌面端协议兼容）
 */
data class ResumeState(
    val filepath: String,
    val offset: Long,
    val totalSize: Long,
    val mtime: Double,
    val targetIp: String,
    /** "sender" 或 "recv" */
    val role: String,
    val timestamp: Double = System.currentTimeMillis() / 1000.0
)

/**
 * 文件夹中单个文件的续传状态
 */
data class FolderResumeState(
    val folderName: String,
    val relPath: String,
    val offset: Long,
    val totalSize: Long,
    val mtime: Double,
    val targetIp: String,
    val timestamp: Double = System.currentTimeMillis() / 1000.0
)
