package com.p2p.filetransfer.ui

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.p2p.filetransfer.model.TransferProgress

@Composable
fun ProgressView(
    title: String,
    progress: TransferProgress,
    modifier: Modifier = Modifier
) {
    Column(modifier = modifier.fillMaxWidth().padding(4.dp)) {
        Row(modifier = Modifier.fillMaxWidth()) {
            Text(text = title, style = MaterialTheme.typography.labelLarge)
            Text(
                text = "  " + String.format("%.1f%%", progress.percent),
                style = MaterialTheme.typography.labelLarge
            )
        }
        LinearProgressIndicator(
            progress = { (progress.percent / 100f).coerceIn(0f, 1f) },
            modifier = Modifier.fillMaxWidth().padding(vertical = 4.dp)
        )
        if (progress.statusText.isNotEmpty()) {
            Text(text = progress.statusText, style = MaterialTheme.typography.bodySmall)
        }
        if (progress.speedText.isNotEmpty()) {
            Text(
                text = progress.speedText + "  剩余 " + progress.remainText,
                style = MaterialTheme.typography.bodySmall
            )
        }
    }
}
