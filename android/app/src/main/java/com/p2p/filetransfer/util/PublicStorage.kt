package com.p2p.filetransfer.util

import android.content.ContentValues
import android.content.Context
import android.net.Uri
import android.os.Build
import android.os.Environment
import android.provider.MediaStore
import androidx.documentfile.provider.DocumentFile
import java.io.File

/**
 * 把接收到的文件/文件夹复制到系统公共存储，让普通文件管理器可见。
 *
 * - Android 10 (API 29) 及以上：使用 MediaStore.Downloads（无需存储权限）
 * - Android 9 及以下：直接写入 /storage/emulated/0/Download/P2PFileTransfer/
 *   （需要在 Manifest 声明 WRITE_EXTERNAL_STORAGE 且运行时申请）
 */
object PublicStorage {

    const val SUBDIR = "P2PFileTransfer"

    /**
     * 发布单个文件到公共下载目录。
     * 返回人类可读的目标描述（用于日志），失败返回 null。
     */
    fun publishFile(context: Context, src: File): String? {
        if (!src.exists() || !src.isFile) return null
        return try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                publishViaMediaStore(context, src)
            } else {
                publishViaLegacyPath(src)
            }
        } catch (e: Exception) {
            null
        }
    }

    /**
     * 递归发布文件夹到公共下载目录。
     * 返回目标文件夹的描述，失败返回 null。
     */
    fun publishFolder(context: Context, srcDir: File): String? {
        if (!srcDir.exists() || !srcDir.isDirectory) return null
        return try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                publishFolderViaMediaStore(context, srcDir)
            } else {
                publishFolderViaLegacyPath(srcDir)
            }
        } catch (e: Exception) {
            null
        }
    }

    // ================= MediaStore (API 29+) =================

    private fun publishViaMediaStore(context: Context, src: File): String {
        val resolver = context.contentResolver
        val values = ContentValues().apply {
            put(MediaStore.Downloads.DISPLAY_NAME, src.name)
            put(MediaStore.Downloads.MIME_TYPE, guessMime(src.name))
            put(MediaStore.Downloads.RELATIVE_PATH, Environment.DIRECTORY_DOWNLOADS + "/" + SUBDIR)
            put(MediaStore.Downloads.IS_PENDING, 1)
        }
        val uri = resolver.insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, values)
            ?: throw IllegalStateException("MediaStore.insert 返回 null")
        try {
            resolver.openOutputStream(uri)?.use { out ->
                src.inputStream().use { input -> input.copyTo(out, 64 * 1024) }
            } ?: throw IllegalStateException("openOutputStream 返回 null")
        } finally {
            values.clear()
            values.put(MediaStore.Downloads.IS_PENDING, 0)
            resolver.update(uri, values, null, null)
        }
        return "下载/" + SUBDIR + "/" + src.name
    }

    private fun publishFolderViaMediaStore(context: Context, srcDir: File): String {
        var ok = 0
        var fail = 0
        srcDir.walkTopDown().forEach { f ->
            if (f.isFile) {
                val rel = f.relativeTo(srcDir).path.replace('\\', '/')
                val target = publishViaMediaStoreWithSubpath(context, f, srcDir.name + "/" + rel)
                if (target != null) ok++ else fail++
            }
        }
        return "下载/" + SUBDIR + "/" + srcDir.name + " (" + ok + " 个文件" +
                (if (fail > 0) "，$fail 失败" else "") + ")"
    }

    private fun publishViaMediaStoreWithSubpath(context: Context, src: File, relPath: String): String? {
        return try {
            val resolver = context.contentResolver
            val dirPart = relPath.substringBeforeLast('/', "")
            val namePart = relPath.substringAfterLast('/')
            val values = ContentValues().apply {
                put(MediaStore.Downloads.DISPLAY_NAME, namePart)
                put(MediaStore.Downloads.MIME_TYPE, guessMime(namePart))
                val rel = if (dirPart.isEmpty())
                    Environment.DIRECTORY_DOWNLOADS + "/" + SUBDIR
                else
                    Environment.DIRECTORY_DOWNLOADS + "/" + SUBDIR + "/" + dirPart
                put(MediaStore.Downloads.RELATIVE_PATH, rel)
                put(MediaStore.Downloads.IS_PENDING, 1)
            }
            val uri = resolver.insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, values) ?: return null
            try {
                resolver.openOutputStream(uri)?.use { out ->
                    src.inputStream().use { input -> input.copyTo(out, 64 * 1024) }
                }
            } finally {
                values.clear()
                values.put(MediaStore.Downloads.IS_PENDING, 0)
                resolver.update(uri, values, null, null)
            }
            relPath
        } catch (_: Exception) {
            null
        }
    }

    // ================= Legacy (API < 29) =================

    private fun publicDownloadsRoot(): File {
        val root = Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOWNLOADS)
        val dir = File(root, SUBDIR)
        if (!dir.exists()) dir.mkdirs()
        return dir
    }

    private fun publishViaLegacyPath(src: File): String {
        val dest = File(publicDownloadsRoot(), src.name)
        src.inputStream().use { input ->
            dest.outputStream().use { out -> input.copyTo(out, 64 * 1024) }
        }
        return "下载/" + SUBDIR + "/" + src.name
    }

    private fun publishFolderViaLegacyPath(srcDir: File): String {
        val destRoot = File(publicDownloadsRoot(), srcDir.name)
        if (!destRoot.exists()) destRoot.mkdirs()
        var ok = 0
        var fail = 0
        srcDir.walkTopDown().forEach { f ->
            if (f.isFile) {
                val rel = f.relativeTo(srcDir).path
                val dest = File(destRoot, rel)
                dest.parentFile?.mkdirs()
                try {
                    f.inputStream().use { input ->
                        dest.outputStream().use { out -> input.copyTo(out, 64 * 1024) }
                    }
                    ok++
                } catch (_: Exception) {
                    fail++
                }
            }
        }
        return "下载/" + SUBDIR + "/" + srcDir.name + " (" + ok + " 个文件" +
                (if (fail > 0) "，$fail 失败" else "") + ")"
    }

    // ================= 用户选择的 SAF 树目录 =================

    /**
     * 把单个文件发布到用户通过 SAF 选择的树目录下。
     * 返回显示描述（“<树名>/<文件名>”），失败返回 null。
     */
    fun publishFileToTree(context: Context, treeUri: Uri, src: File): String? {
        if (!src.exists() || !src.isFile) return null
        return try {
            val root = DocumentFile.fromTreeUri(context, treeUri) ?: return null
            val existing = root.findFile(src.name)
            existing?.delete()
            val mime = guessMime(src.name)
            val target = root.createFile(mime, src.name) ?: return null
            context.contentResolver.openOutputStream(target.uri)?.use { out ->
                src.inputStream().use { input -> input.copyTo(out, 64 * 1024) }
            }
            (root.name ?: "选定目录") + "/" + src.name
        } catch (e: Exception) {
            null
        }
    }

    /**
     * 把文件夹发布到用户选择的 SAF 树目录下。
     */
    fun publishFolderToTree(context: Context, treeUri: Uri, srcDir: File): String? {
        if (!srcDir.exists() || !srcDir.isDirectory) return null
        return try {
            val root = DocumentFile.fromTreeUri(context, treeUri) ?: return null
            val existing = root.findFile(srcDir.name)
            existing?.delete()
            val targetDir = root.createDirectory(srcDir.name) ?: return null
            var ok = 0
            var fail = 0
            srcDir.walkTopDown().forEach { f ->
                if (f.isFile) {
                    val rel = f.relativeTo(srcDir).path.replace('\\', '/')
                    val parts = rel.split('/')
                    var cur = targetDir
                    for (i in 0 until parts.size - 1) {
                        val seg = parts[i]
                        cur = cur.findFile(seg) ?: cur.createDirectory(seg) ?: cur
                    }
                    val fileName = parts.last()
                    val created = cur.createFile(guessMime(fileName), fileName)
                    if (created != null) {
                        context.contentResolver.openOutputStream(created.uri)?.use { out ->
                            f.inputStream().use { input -> input.copyTo(out, 64 * 1024) }
                        }
                        ok++
                    } else {
                        fail++
                    }
                }
            }
            (root.name ?: "选定目录") + "/" + srcDir.name + " (" + ok + " 个文件" +
                    (if (fail > 0) "，$fail 失败" else "") + ")"
        } catch (e: Exception) {
            null
        }
    }

    /**
     * 尝试把 Uri 表示为可读的显示路径（尽力而为，不保证一定拿到绝对路径）。
     */
    fun treeDisplayName(context: Context, treeUri: Uri): String? {
        return try {
            DocumentFile.fromTreeUri(context, treeUri)?.name
        } catch (_: Exception) {
            null
        }
    }

    // ================= 工具 =================

    private fun guessMime(name: String): String {
        val ext = name.substringAfterLast('.', "").lowercase()
        return when (ext) {
            "txt", "log", "md" -> "text/plain"
            "json" -> "application/json"
            "xml" -> "application/xml"
            "html", "htm" -> "text/html"
            "jpg", "jpeg" -> "image/jpeg"
            "png" -> "image/png"
            "gif" -> "image/gif"
            "mp4" -> "video/mp4"
            "mp3" -> "audio/mpeg"
            "zip" -> "application/zip"
            "apk" -> "application/vnd.android.package-archive"
            else -> "application/octet-stream"
        }
    }
}
