package com.p2p.filetransfer

import android.app.Application
import android.app.NotificationChannel
import android.app.NotificationManager
import android.os.Build

class P2PApplication : Application() {

    override fun onCreate() {
        super.onCreate()
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
