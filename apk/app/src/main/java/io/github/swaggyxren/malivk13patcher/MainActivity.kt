package io.github.swaggyxren.malivk13patcher

import android.app.Activity
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.unit.dp
import androidx.lifecycle.lifecycleScope
import io.github.swaggyxren.malivk13patcher.core.PatchResult
import io.github.swaggyxren.malivk13patcher.core.Patcher
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            MaterialTheme {
                Surface(
                    modifier = Modifier.fillMaxSize(),
                    color = MaterialTheme.colorScheme.background,
                ) {
                    PatcherScreen()
                }
            }
        }
    }
}

@Composable
private fun PatcherScreen() {
    val context = LocalContext.current
    val activity = context as ComponentActivity
    var inputUri by remember { mutableStateOf<Uri?>(null) }
    var outputUri by remember { mutableStateOf<Uri?>(null) }
    var inputName by remember { mutableStateOf("") }
    var outputName by remember { mutableStateOf("") }
    var busy by remember { mutableStateOf(false) }
    val logState = remember { MutableStateFlow("") }
    val log by logState.collectAsState()

    val pickInputLauncher =
        androidx.activity.compose.rememberLauncherForActivityResult(
            ActivityResultContracts.StartActivityForResult(),
        ) { result ->
            val uri = result.data?.data ?: return@rememberLauncherForActivityResult
            try {
                context.contentResolver.takePersistableUriPermission(
                    uri, Intent.FLAG_GRANT_READ_URI_PERMISSION,
                )
            } catch (_: SecurityException) {
                // Some pickers (file managers, Downloads provider) don't support persistable
                // permissions. We'll still be able to open the URI for the duration of the
                // process via the granted FLAG_GRANT_READ_URI_PERMISSION.
            }
            inputUri = uri
            inputName = displayName(context, uri)
        }

    val pickOutputLauncher =
        androidx.activity.compose.rememberLauncherForActivityResult(
            ActivityResultContracts.StartActivityForResult(),
        ) { result ->
            val uri = result.data?.data ?: return@rememberLauncherForActivityResult
            try {
                context.contentResolver.takePersistableUriPermission(
                    uri, Intent.FLAG_GRANT_WRITE_URI_PERMISSION,
                )
            } catch (_: SecurityException) {
                // See note above.
            }
            outputUri = uri
            outputName = displayName(context, uri)
        }

    Column(
        modifier =
            Modifier
                .fillMaxSize()
                .padding(16.dp)
                .verticalScroll(rememberScrollState()),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Text(
            text = context.getString(R.string.title),
            style = MaterialTheme.typography.titleLarge,
        )
        Text(
            text = context.getString(R.string.subtitle),
            style = MaterialTheme.typography.bodyMedium,
        )

        OutlinedTextField(
            value = inputName,
            onValueChange = {},
            readOnly = true,
            label = { Text("Input vendor.img") },
            modifier = Modifier.fillMaxWidth(),
        )
        Button(
            onClick = {
                val intent =
                    Intent(Intent.ACTION_OPEN_DOCUMENT).apply {
                        addCategory(Intent.CATEGORY_OPENABLE)
                        type = "*/*"
                        putExtra(Intent.EXTRA_MIME_TYPES, arrayOf("application/octet-stream", "*/*"))
                        addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
                        addFlags(Intent.FLAG_GRANT_PERSISTABLE_URI_PERMISSION)
                    }
                pickInputLauncher.launch(intent)
            },
            enabled = !busy,
            modifier = Modifier.fillMaxWidth(),
        ) { Text(context.getString(R.string.pick_input)) }

        OutlinedTextField(
            value = outputName,
            onValueChange = {},
            readOnly = true,
            label = { Text("Output location") },
            modifier = Modifier.fillMaxWidth(),
        )
        Button(
            onClick = {
                val intent =
                    Intent(Intent.ACTION_CREATE_DOCUMENT).apply {
                        addCategory(Intent.CATEGORY_OPENABLE)
                        type = "application/octet-stream"
                        putExtra(Intent.EXTRA_TITLE, "vendor_mali_vk13.img")
                        addFlags(Intent.FLAG_GRANT_WRITE_URI_PERMISSION)
                        addFlags(Intent.FLAG_GRANT_PERSISTABLE_URI_PERMISSION)
                    }
                pickOutputLauncher.launch(intent)
            },
            enabled = !busy,
            modifier = Modifier.fillMaxWidth(),
        ) { Text(context.getString(R.string.pick_output)) }

        Button(
            onClick = {
                val inUri = inputUri
                val outUri = outputUri
                if (inUri == null || outUri == null) {
                    logState.value = "Pick both input and output before patching."
                    return@Button
                }
                busy = true
                logState.value = ""
                activity.lifecycleScope.launch {
                    val result = withContext(Dispatchers.IO) {
                        runCatching {
                            Patcher(context).run(inUri, outUri) { line ->
                                logState.value = logState.value + line + "\n"
                            }
                        }
                    }
                    busy = false
                    result.onSuccess { meta: PatchResult ->
                        logState.value = logState.value +
                            "\nDONE — wrote ${meta.outputSize} bytes to output URI.\n" +
                            "Now flash with fastboot. Bootloader must be unlocked and " +
                            "vbmeta verity disabled. See README."
                    }.onFailure { e ->
                        logState.value = logState.value + "\nFAILED: ${e.message}"
                    }
                }
            },
            enabled = !busy && inputUri != null && outputUri != null,
            modifier = Modifier.fillMaxWidth(),
        ) {
            Text(if (busy) context.getString(R.string.patching) else context.getString(R.string.patch))
        }

        if (busy) {
            LinearProgressIndicator(modifier = Modifier.fillMaxWidth())
        }

        Text(
            text = log,
            fontFamily = FontFamily.Monospace,
            style = MaterialTheme.typography.bodySmall,
        )
    }

    LaunchedEffect(Unit) {
        // Pre-extract the bundled payload into the app cache once on first launch.
        // This avoids a long delay when the user clicks "Patch".
        Patcher(context).preparePayloadAsync()
    }
}

private fun displayName(context: android.content.Context, uri: Uri): String {
    return try {
        val cursor = context.contentResolver.query(uri, null, null, null, null)
        cursor?.use {
            val nameIdx = it.getColumnIndex(android.provider.OpenableColumns.DISPLAY_NAME)
            if (it.moveToFirst() && nameIdx >= 0) it.getString(nameIdx) else uri.toString()
        } ?: uri.toString()
    } catch (_: Exception) {
        uri.toString()
    }
}
