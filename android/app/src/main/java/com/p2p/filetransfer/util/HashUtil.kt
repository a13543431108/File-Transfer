package com.p2p.filetransfer.util

import java.io.File
import java.io.InputStream
import java.security.MessageDigest

/** SHA-256 计算工具 */
object HashUtil {

    private const val BUF = 1024 * 1024

    private fun toHex(bytes: ByteArray): String {
        val sb = StringBuilder(bytes.size * 2)
        for (b in bytes) sb.append(String.format("%02x", b))
        return sb.toString()
    }

    /** 计算整个文件的 SHA-256（hex 小写，64 字符） */
    fun sha256(file: File): String {
        val digest = MessageDigest.getInstance("SHA-256")
        file.inputStream().use { input ->
            val buf = ByteArray(BUF)
            var n = input.read(buf)
            while (n > 0) {
                digest.update(buf, 0, n)
                n = input.read(buf)
            }
        }
        return toHex(digest.digest())
    }

    /** 从 offset 开始计算 length 字节的 SHA-256；length = null 表示到文件末尾 */
    fun sha256(file: File, offset: Long, length: Long?): String {
        val digest = MessageDigest.getInstance("SHA-256")
        file.inputStream().use { raw ->
            skipFully(raw, offset)
            var remaining = length ?: (file.length() - offset)
            val buf = ByteArray(BUF)
            while (remaining > 0) {
                val want = minOf(buf.size.toLong(), remaining).toInt()
                val n = raw.read(buf, 0, want)
                if (n <= 0) break
                digest.update(buf, 0, n)
                remaining -= n
            }
        }
        return toHex(digest.digest())
    }

    private fun skipFully(input: InputStream, offset: Long) {
        var remaining = offset
        while (remaining > 0) {
            val skipped = input.skip(remaining)
            if (skipped <= 0) {
                if (input.read() < 0) break
                remaining -= 1
            } else {
                remaining -= skipped
            }
        }
    }
}
