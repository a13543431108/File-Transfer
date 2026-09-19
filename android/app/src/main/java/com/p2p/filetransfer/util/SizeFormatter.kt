package com.p2p.filetransfer.util

import java.util.Locale

/** 字节数 / 速度 / 时间的格式化工具 */
object SizeFormatter {

    /** 人类可读的文件大小，如 "1.23 MB" */
    fun humanSize(n: Long): String {
        var value = n.toDouble()
        val units = arrayOf("B", "KB", "MB", "GB", "TB")
        for (unit in units) {
            if (value < 1024) return String.format(Locale.US, "%.2f %s", value, unit)
            value /= 1024
        }
        return String.format(Locale.US, "%.2f PB", value / 1024)
    }

    /** 传输速度，如 "12.3 MB/s" */
    fun formatSpeed(bytesPerSec: Double): String {
        return when {
            bytesPerSec < 1024 -> String.format(Locale.US, "%.1f B/s", bytesPerSec)
            bytesPerSec < 1024.0 * 1024 -> String.format(Locale.US, "%.1f KB/s", bytesPerSec / 1024)
            bytesPerSec < 1024.0 * 1024 * 1024 -> String.format(Locale.US, "%.1f MB/s", bytesPerSec / (1024.0 * 1024))
            else -> String.format(Locale.US, "%.1f GB/s", bytesPerSec / (1024.0 * 1024 * 1024))
        }
    }

    /** 剩余时间，如 "1m 30s" / "2h 5m" */
    fun formatTime(seconds: Double): String {
        return when {
            seconds < 60 -> String.format(Locale.US, "%.0fs", seconds)
            seconds < 3600 -> String.format(Locale.US, "%.0fm %.0fs", seconds / 60, seconds % 60)
            else -> String.format(Locale.US, "%.0fh %.0fm", seconds / 3600, (seconds % 3600) / 60)
        }
    }
}
