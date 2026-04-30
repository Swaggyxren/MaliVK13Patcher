package io.github.swaggyxren.malivk13patcher

import android.content.Intent
import android.net.Uri
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.foundation.background
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ExposedDropdownMenuBox
import androidx.compose.material3.ExposedDropdownMenuDefaults
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Slider
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TextFieldDefaults
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.lifecycle.lifecycleScope
import io.github.swaggyxren.malivk13patcher.core.PatchResult
import io.github.swaggyxren.malivk13patcher.core.Patcher
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/**
 * Two-theme palette (pure dark / pure light) matching the desktop GUI.
 * No Material You / dynamic color — keeps the look identical across devices.
 */
private val DarkPalette = darkColorScheme(
    primary = Color(0xFFFFFFFF),
    onPrimary = Color(0xFF0E0E10),
    secondary = Color(0xFFE6E6EA),
    onSecondary = Color(0xFF0E0E10),
    background = Color(0xFF0E0E10),
    onBackground = Color(0xFFF5F5F7),
    surface = Color(0xFF17171A),
    onSurface = Color(0xFFF5F5F7),
    surfaceVariant = Color(0xFF1C1C20),
    onSurfaceVariant = Color(0xFF8A8A91),
    outline = Color(0xFF2A2A2F),
)

private val LightPalette = lightColorScheme(
    primary = Color(0xFF111114),
    onPrimary = Color(0xFFFFFFFF),
    secondary = Color(0xFF1D1D1F),
    onSecondary = Color(0xFFFFFFFF),
    background = Color(0xFFFFFFFF),
    onBackground = Color(0xFF111114),
    surface = Color(0xFFF6F6F7),
    onSurface = Color(0xFF111114),
    surfaceVariant = Color(0xFFEAEAEE),
    onSurfaceVariant = Color(0xFF6B6B73),
    outline = Color(0xFFDCDCE0),
)

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            // Default follows system; user can override at runtime.
            var darkTheme by remember { mutableStateOf<Boolean?>(null) }
            val systemDark = isSystemInDarkTheme()
            val effectiveDark = darkTheme ?: systemDark
            MaterialTheme(colorScheme = if (effectiveDark) DarkPalette else LightPalette) {
                Surface(
                    modifier = Modifier.fillMaxSize(),
                    color = MaterialTheme.colorScheme.background,
                ) {
                    PatcherScreen(
                        isDark = effectiveDark,
                        onToggleTheme = { darkTheme = !effectiveDark },
                    )
                }
            }
        }
    }
}

private val EROFS_COMPRESSORS = listOf("lz4", "lz4hc", "lzma", "deflate", "zstd")

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun PatcherScreen(
    isDark: Boolean,
    onToggleTheme: () -> Unit,
) {
    val context = LocalContext.current
    val activity = context as ComponentActivity
    var inputUri by remember { mutableStateOf<Uri?>(null) }
    var outputUri by remember { mutableStateOf<Uri?>(null) }
    var inputName by remember { mutableStateOf("") }
    var outputName by remember { mutableStateOf("") }
    var busy by remember { mutableStateOf(false) }
    val logState = remember { MutableStateFlow("") }
    val log by logState.collectAsState()

    // Pack options
    var advancedOpen by remember { mutableStateOf(false) }
    var compressionAlgo by remember { mutableStateOf("lz4hc") }
    var compressionLevel by remember { mutableStateOf(9f) }   // 0..9; 0 = mkfs default
    var utcInput by remember { mutableStateOf("") }            // blank = original timestamp
    var compressionMenuOpen by remember { mutableStateOf(false) }

    val pickInputLauncher =
        androidx.activity.compose.rememberLauncherForActivityResult(
            ActivityResultContracts.StartActivityForResult(),
        ) { result ->
            val uri = result.data?.data ?: return@rememberLauncherForActivityResult
            try {
                context.contentResolver.takePersistableUriPermission(
                    uri, Intent.FLAG_GRANT_READ_URI_PERMISSION,
                )
            } catch (_: SecurityException) { /* ignore */ }
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
            } catch (_: SecurityException) { /* ignore */ }
            outputUri = uri
            outputName = displayName(context, uri)
        }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .background(MaterialTheme.colorScheme.background)
            .padding(horizontal = 20.dp, vertical = 18.dp)
            .verticalScroll(rememberScrollState()),
        verticalArrangement = Arrangement.spacedBy(14.dp),
    ) {
        // --- Header ---------------------------------------------------
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(
                text = context.getString(R.string.title),
                style = MaterialTheme.typography.titleLarge,
                fontWeight = FontWeight.Bold,
                modifier = Modifier.weight(1f),
            )
            TextButton(onClick = onToggleTheme) {
                Text(if (isDark) "Light" else "Dark")
            }
        }
        Text(
            text = context.getString(R.string.subtitle),
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )

        // --- Files card ----------------------------------------------
        Card(
            colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
            shape = RoundedCornerShape(14.dp),
            elevation = CardDefaults.cardElevation(defaultElevation = 0.dp),
            modifier = Modifier.fillMaxWidth(),
        ) {
            Column(
                modifier = Modifier.padding(14.dp),
                verticalArrangement = Arrangement.spacedBy(10.dp),
            ) {
                FileRow(
                    label = "Input vendor.img",
                    value = inputName,
                    buttonText = "Browse",
                    enabled = !busy,
                    onClick = {
                        val intent = Intent(Intent.ACTION_OPEN_DOCUMENT).apply {
                            addCategory(Intent.CATEGORY_OPENABLE)
                            type = "*/*"
                            putExtra(Intent.EXTRA_MIME_TYPES, arrayOf("application/octet-stream", "*/*"))
                            addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
                            addFlags(Intent.FLAG_GRANT_PERSISTABLE_URI_PERMISSION)
                        }
                        pickInputLauncher.launch(intent)
                    },
                )
                FileRow(
                    label = "Output vendor.img",
                    value = outputName,
                    buttonText = "Save as",
                    enabled = !busy,
                    onClick = {
                        val intent = Intent(Intent.ACTION_CREATE_DOCUMENT).apply {
                            addCategory(Intent.CATEGORY_OPENABLE)
                            type = "application/octet-stream"
                            putExtra(Intent.EXTRA_TITLE, "vendor_mali_vk13.img")
                            addFlags(Intent.FLAG_GRANT_WRITE_URI_PERMISSION)
                            addFlags(Intent.FLAG_GRANT_PERSISTABLE_URI_PERMISSION)
                        }
                        pickOutputLauncher.launch(intent)
                    },
                )
            }
        }

        // --- Advanced toggle ------------------------------------------
        TextButton(
            onClick = { advancedOpen = !advancedOpen },
            enabled = !busy,
        ) {
            Text(
                text = (if (advancedOpen) "▾ " else "▸ ") + "Advanced pack options",
                color = MaterialTheme.colorScheme.onBackground,
            )
        }

        AnimatedVisibility(visible = advancedOpen) {
            Card(
                colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
                shape = RoundedCornerShape(14.dp),
                elevation = CardDefaults.cardElevation(defaultElevation = 0.dp),
                modifier = Modifier.fillMaxWidth(),
            ) {
                Column(
                    modifier = Modifier.padding(14.dp),
                    verticalArrangement = Arrangement.spacedBy(14.dp),
                ) {
                    // EROFS compressor dropdown
                    ExposedDropdownMenuBox(
                        expanded = compressionMenuOpen,
                        onExpandedChange = { compressionMenuOpen = !compressionMenuOpen },
                    ) {
                        OutlinedTextField(
                            value = compressionAlgo,
                            onValueChange = {},
                            readOnly = true,
                            label = { Text("EROFS compression") },
                            trailingIcon = {
                                ExposedDropdownMenuDefaults.TrailingIcon(
                                    expanded = compressionMenuOpen,
                                )
                            },
                            colors = ExposedDropdownMenuDefaults.outlinedTextFieldColors(),
                            modifier = Modifier
                                .menuAnchor()
                                .fillMaxWidth(),
                        )
                        androidx.compose.material3.ExposedDropdownMenu(
                            expanded = compressionMenuOpen,
                            onDismissRequest = { compressionMenuOpen = false },
                        ) {
                            EROFS_COMPRESSORS.forEach { algo ->
                                DropdownMenuItem(
                                    text = { Text(algo) },
                                    onClick = {
                                        compressionAlgo = algo
                                        compressionMenuOpen = false
                                    },
                                )
                            }
                        }
                    }

                    // Level slider
                    Column {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Text(
                                text = "Level",
                                style = MaterialTheme.typography.bodyMedium,
                                fontWeight = FontWeight.SemiBold,
                                modifier = Modifier.weight(1f),
                            )
                            Text(
                                text = compressionLevel.toInt().toString(),
                                style = MaterialTheme.typography.bodyMedium,
                            )
                        }
                        Slider(
                            value = compressionLevel,
                            onValueChange = { compressionLevel = it },
                            valueRange = 0f..9f,
                            steps = 8,
                        )
                        Text(
                            text = "0 = mkfs.erofs default (matches MIO-KITCHEN), 9 = max compression",
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }

                    // UTC timestamp row
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        OutlinedTextField(
                            value = utcInput,
                            onValueChange = { utcInput = it.filter { c -> c.isDigit() } },
                            label = { Text("Fixed UTC timestamp") },
                            modifier = Modifier.weight(1f),
                        )
                        Spacer(Modifier.width(8.dp))
                        OutlinedButton(
                            onClick = {
                                utcInput = (System.currentTimeMillis() / 1000).toString()
                            },
                        ) { Text("Now") }
                    }
                    Text(
                        text = "Blank = use original image timestamp",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
        }

        // --- Patch button --------------------------------------------
        Button(
            onClick = {
                val inUri = inputUri
                val outUri = outputUri
                if (inUri == null || outUri == null) {
                    logState.value = "Pick both input and output before patching."
                    return@Button
                }
                val ts = utcInput.trim().takeIf { it.isNotEmpty() }?.toLongOrNull()
                val pack = Patcher.PackOptions(
                    erofsCompression = compressionAlgo,
                    erofsLevel = compressionLevel.toInt().let { if (it == 0) null else it },
                    timestamp = ts,
                )
                busy = true
                logState.value = ""
                activity.lifecycleScope.launch {
                    val result = withContext(Dispatchers.IO) {
                        runCatching {
                            Patcher(context).run(inUri, outUri, pack) { line ->
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
            colors = ButtonDefaults.buttonColors(
                containerColor = MaterialTheme.colorScheme.primary,
                contentColor = MaterialTheme.colorScheme.onPrimary,
                disabledContainerColor = MaterialTheme.colorScheme.surface,
                disabledContentColor = MaterialTheme.colorScheme.onSurfaceVariant,
            ),
            shape = RoundedCornerShape(12.dp),
            modifier = Modifier
                .fillMaxWidth()
                .heightIn(min = 52.dp),
        ) {
            Text(
                text = if (busy) context.getString(R.string.patching)
                       else context.getString(R.string.patch),
                fontWeight = FontWeight.SemiBold,
            )
        }

        // --- Progress (only visible while busy) ----------------------
        Box(modifier = Modifier.height(6.dp)) {
            if (busy) {
                LinearProgressIndicator(
                    color = MaterialTheme.colorScheme.primary,
                    trackColor = MaterialTheme.colorScheme.surfaceVariant,
                    modifier = Modifier.fillMaxWidth(),
                )
            }
        }

        // --- Log ------------------------------------------------------
        Card(
            colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
            shape = RoundedCornerShape(12.dp),
            elevation = CardDefaults.cardElevation(defaultElevation = 0.dp),
            modifier = Modifier.fillMaxWidth(),
        ) {
            Text(
                text = if (log.isEmpty()) "Logs will appear here." else log,
                fontFamily = FontFamily.Monospace,
                style = MaterialTheme.typography.bodySmall,
                color = if (log.isEmpty())
                    MaterialTheme.colorScheme.onSurfaceVariant
                else
                    MaterialTheme.colorScheme.onSurface,
                modifier = Modifier.padding(12.dp),
            )
        }
    }

    LaunchedEffect(Unit) {
        // Pre-extract the bundled payload into the app cache once on first launch.
        // Avoids a long delay when the user clicks "Patch".
        Patcher(context).preparePayloadAsync()
    }
}

@Composable
private fun FileRow(
    label: String,
    value: String,
    buttonText: String,
    enabled: Boolean,
    onClick: () -> Unit,
) {
    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
        Text(
            text = label,
            style = MaterialTheme.typography.labelLarge,
            fontWeight = FontWeight.SemiBold,
            color = MaterialTheme.colorScheme.onSurface,
        )
        Row(verticalAlignment = Alignment.CenterVertically) {
            OutlinedTextField(
                value = value,
                onValueChange = {},
                readOnly = true,
                placeholder = { Text("Not selected") },
                colors = TextFieldDefaults.colors(
                    focusedContainerColor = MaterialTheme.colorScheme.surfaceVariant,
                    unfocusedContainerColor = MaterialTheme.colorScheme.surfaceVariant,
                    disabledContainerColor = MaterialTheme.colorScheme.surfaceVariant,
                ),
                modifier = Modifier.weight(1f),
            )
            Spacer(Modifier.width(8.dp))
            Button(
                onClick = onClick,
                enabled = enabled,
                colors = ButtonDefaults.buttonColors(
                    containerColor = MaterialTheme.colorScheme.surfaceVariant,
                    contentColor = MaterialTheme.colorScheme.onSurface,
                ),
                shape = RoundedCornerShape(10.dp),
            ) {
                Text(buttonText)
            }
        }
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
