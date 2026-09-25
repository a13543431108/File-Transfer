package com.p2p.filetransfer

import android.app.Application
import android.app.NotificationChannel
import android.app.NotificationManager
import android.os.Build

class P2PApplication : Application() {

    override fun onCreate() {
        super.onCreate()
        // 强制 JVM 使用 IPv4 Socket，避免 IPv6 双栈导致 connect IPv4 服务器
        // 时报 EADDRNOTAVAIL（内核在 IPv6 地址空间找不到源地址）。
        // 必须在任何网络操作之前设置。
        try {
            System.setProperty("java.net.preferIPv4Stack", "true")
            System.setProperty("java.net.preferIPv6Addresses", "false")
        } catch (_: Throwable) {}
        createNotificationChannels()
    }

    private fun createNotificationChannels() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
        val nm = getSystemService(NotificationManager::class.java) ?: return

        nm.createNotificationChannel(
            NotificationChannel(
                CHANNEL_STATUS,
                getString(R.string.notification_channel_status),
                NotificationManager.IMPORTANCE_LOW
            )
        )
        nm.createNotificationChannel(
            NotificationChannel(
                CHANNEL_PROGRESS,
                getString(R.string.notification_channel_progress),
                NotificationManager.IMPORTANCE_LOW
            )
        )
        nm.createNotificationChannel(
            NotificationChannel(
                CHANNEL_REQUEST,
                getString(R.string.notification_channel_request),
                NotificationManager.IMPORTANCE_HIGH
            )
        )
    }

    companion object {
        const val CHANNEL_STATUS = "app_status"
        const val CHANNEL_PROGRESS = "transfer_progress"
        const val CHANNEL_REQUEST = "receiver_request"
    }
}
