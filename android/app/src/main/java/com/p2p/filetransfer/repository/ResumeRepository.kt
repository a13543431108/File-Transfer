package com.p2p.filetransfer.repository

import android.content.Context
import com.google.gson.Gson
import com.p2p.filetransfer.model.FolderResumeState
import com.p2p.filetransfer.model.ResumeState
import java.io.File

/**
 * 续传状态持久化。
 *
 * 存储目录: 应用私有目录 /.p2p_resume/
 * 命名规则:
 *   单文件:  resume_{role}_{ip}_{safeFilename}.json
 *   文件夹:  folder_resume_{ip}_{safeFolder}_{safeRelPath}.json
 *
 * JSON 字段与桌面端 Python 实现保持一致，便于交叉续传。
 */
class ResumeRepository(context: Context) {

    private val gson = Gson()
    private val resumeDir: File = File(context.filesDir, ".p2p_resume").apply { mkdirs() }

    // ---------- 单文件续传 ----------

    private fun safeName(name: String): String =
        name.replace('/', '_').replace('\\', '_')

    private fun resumeFile(ip: String, filename: String, role: String): File =
        File(resumeDir, "resume_" + role + "_" + ip + "_" + safeName(filename) + ".json")

    fun saveState(
        targetIp: String,
        filepath: String,
        offset: Long,
        totalSize: Long,
        mtime: Double,
        role: String,
        keyName: String? = null
    ) {
        try {
            val state = ResumeState(
                filepath = filepath,
                offset = offset,
                totalSize = totalSize,
                mtime = mtime,
                targetIp = targetIp,
                role = role
            )
            // keyName：接收方改名保存时传入发送方原始文件名，保证重试能查到记录
            val key = keyName ?: File(filepath).name
            resumeFile(targetIp, key, role).writeText(gson.toJson(state))
        } catch (_: Exception) {
        }
    }

    /**
     * 加载续传状态。
     * - role = "sender": 校验源文件存在且 mtime 一致
     * - role = "recv":   校验目标文件存在且大小 == offset
     * 不匹配则删除记录并返回 null。
     */
    fun loadState(targetIp: String, filepath: String, role: String, keyName: String? = null): ResumeState? {
        val key = keyName ?: File(filepath).name
        val f = resumeFile(targetIp, key, role)
        if (!f.exists()) return null
        return try {
            val state = gson.fromJson(f.readText(), ResumeState::class.java) ?: return null
            if (role == "sender") {
                val src = File(state.filepath)
                // mtime 使用 1ms 容差比较，兼容 Python(浮点秒,微秒精度)
                // 与 Android(毫秒精度) 两端记录的差异
                val sameMtime = Math.abs((src.lastModified() / 1000.0) - state.mtime) < 0.001
                if (state.filepath == filepath && src.exists() && sameMtime) {
                    state
                } else {
                    f.delete()
                    null
                }
            } else {
                val dest = File(state.filepath)
                if (dest.exists() && dest.length() == state.offset) {
                    state
                } else {
                    f.delete()
                    null
                }
            }
        } catch (_: Exception) {
            null
        }
    }

    fun deleteState(targetIp: String, filepath: String, role: String, keyName: String? = null) {
        val key = keyName ?: File(filepath).name
        val f = resumeFile(targetIp, key, role)
        if (f.exists()) f.delete()
    }

    /** 按接收方原始文件名查找 recv 续传记录（供续传查询判据）。 */
    fun loadRecvByKey(targetIp: String, keyName: String): ResumeState? {
        val f = resumeFile(targetIp, keyName, "recv")
        if (!f.exists()) return null
        return try {
            gson.fromJson(f.readText(), ResumeState::class.java)
        } catch (_: Exception) {
            null
        }
    }

    /**
     * 列出所有"接收中"单文件续传记录对应的文件绝对路径。
     * 用于清理私有暂存目录时，保留未完成（中断）的接收文件。
     */
    fun listRecvFilepaths(): Set<String> {
        val out = HashSet<String>()
        try {
            resumeDir.listFiles { f ->
                f.name.startsWith("resume_recv_") && f.name.endsWith(".json")
            }?.forEach { f ->
                try {
                    gson.fromJson(f.readText(), ResumeState::class.java)?.let {
                        if (it.filepath.isNotEmpty()) out.add(it.filepath)
                    }
                } catch (_: Exception) {}
            }
        } catch (_: Exception) {}
        return out
    }

    /**
     * 列出所有"接收中"文件夹续传记录涉及的文件夹名。
     * 用于清理私有暂存目录时，保留未完成（中断）的接收文件夹。
     */
    fun listFolderNames(): Set<String> {
        val out = HashSet<String>()
        try {
            resumeDir.listFiles { f ->
                f.name.startsWith("folder_resume_") && f.name.endsWith(".json")
            }?.forEach { f ->
                try {
                    gson.fromJson(f.readText(), FolderResumeState::class.java)?.let {
                        if (it.folderName.isNotEmpty()) out.add(it.folderName)
                    }
                } catch (_: Exception) {}
            }
        } catch (_: Exception) {}
        return out
    }

    /** 清理超过指定天数的续传记录（自清生成文件）。 */
    fun cleanupExpired(retentionDays: Int): Int {
        val cutoff = System.currentTimeMillis() - retentionDays.toLong() * 86400_000L
        var n = 0
        resumeDir.listFiles()?.forEach { f ->
            try {
                if (f.isFile && f.lastModified() < cutoff) {
                    if (f.delete()) n++
                }
            } catch (_: Exception) {}
        }
        return n
    }

    /** 返回所有以给定 IP 为目标的 sender 续传文件 */
    fun listSenderStatesForIp(targetIp: String): List<File> {
        val prefix = "resume_sender_" + targetIp + "_"
        return resumeDir.listFiles { f -> f.name.startsWith(prefix) }?.toList() ?: emptyList()
    }

    /** 返回所有以给定 IP 为目标的文件夹续传文件 */
    fun listFolderStatesForIp(targetIp: String): List<File> {
        val prefix = "folder_resume_" + targetIp + "_"
        return resumeDir.listFiles { f -> f.name.startsWith(prefix) }?.toList() ?: emptyList()
    }

    // ---------- 文件夹续传 ----------

    private fun folderResumeFile(targetIp: String, folderName: String, relPath: String): File {
        val safeFolder = safeName(folderName)
        val safePath = safeName(relPath)
        return File(resumeDir, "folder_resume_" + targetIp + "_" + safeFolder + "_" + safePath + ".json")
    }

    fun saveFolderState(
        targetIp: String,
        folderName: String,
        relPath: String,
        offset: Long,
        totalSize: Long,
        mtime: Double
    ) {
        try {
            val state = FolderResumeState(
                folderName = folderName,
                relPath = relPath,
                offset = offset,
                totalSize = totalSize,
                mtime = mtime,
                targetIp = targetIp
            )
            folderResumeFile(targetIp, folderName, relPath).writeText(gson.toJson(state))
        } catch (_: Exception) {
        }
    }

    /**
     * 加载文件夹中单个文件的续传状态。
     * [destPath] 非空时用于校验目标文件是否与 offset 一致。
     */
    fun loadFolderState(
        targetIp: String,
        folderName: String,
        relPath: String,
        destPath: File?
    ): FolderResumeState? {
        val f = folderResumeFile(targetIp, folderName, relPath)
        if (!f.exists()) return null
        return try {
            val state = gson.fromJson(f.readText(), FolderResumeState::class.java) ?: return null
            if (destPath != null) {
                if (destPath.exists() && destPath.length() == state.offset &&
                    destPath.length() < state.totalSize) {
                    state
                } else {
                    f.delete()
                    null
                }
            } else {
                state
            }
        } catch (_: Exception) {
            null
        }
    }

    fun deleteFolderState(targetIp: String, folderName: String, relPath: String) {
        val f = folderResumeFile(targetIp, folderName, relPath)
        if (f.exists()) f.delete()
    }
}
