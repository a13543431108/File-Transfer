package com.p2p.filetransfer.ui

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.InsertDriveFile
import androidx.compose.material.icons.filled.Folder
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.p2p.filetransfer.model.TransferItem
import com.p2p.filetransfer.util.SizeFormatter
import java.io.File

@Composable
fun FileList(
    items: List<TransferItem>,
    onRemove: (Int) -> Unit,
    onMoveUp: (Int) -> Unit,
    onMoveDown: (Int) -> Unit,
    modifier: Modifier = Modifier
) {
    LazyColumn(modifier = modifier) {
        itemsIndexed(items) { index, item ->
            Card(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(vertical = 4.dp),
                colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceVariant)
            ) {
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(8.dp),
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Icon(
                        imageVector = if (item.isFolder) Icons.Filled.Folder else Icons.AutoMirrored.Filled.InsertDriveFile,
                        contentDescription = null,
                        modifier = Modifier.padding(end = 8.dp)
                    )
                    Column(modifier = Modifier.weight(1f)) {
                        Text(text = File(item.path).name, style = MaterialTheme.typography.bodyLarge)
                        val sizeText = if (item.isFolder) "文件夹" else SizeFormatter.humanSize(File(item.path).length())
                        Text(text = sizeText, style = MaterialTheme.typography.bodySmall)
                    }
                    TextButton(onClick = { onMoveUp(index) }) { Text("↑") }
                    TextButton(onClick = { onMoveDown(index) }) { Text("↓") }
                    TextButton(onClick = { onRemove(index) }) { Text("✕") }
                }
            }
        }
    }
}
