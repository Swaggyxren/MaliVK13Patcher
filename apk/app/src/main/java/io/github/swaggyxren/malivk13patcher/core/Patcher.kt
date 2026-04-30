package io.github.swaggyxren.malivk13patcher.core

import android.content.Context
import android.net.Uri
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import java.io.File
import java.io.FileNotFoundException
import java.io.IOException
import java.nio.file.Files
import java.nio.file.StandardCopyOption
import java.util.UUID

/**
 * Kotlin port of `core/patcher.py`.
 *
 * Pipeline:
 *   1. Copy the input vendor.img from a SAF Uri into the app's cache dir.
 *   2. Detect format (sparse vs raw, EROFS vs ext4).
 *   3. Unpack via the bundled native binaries (extract.erofs).
 *   4. Overlay Mali libs from the bundled assets/payload tree.
 *   5. Merge `system.prop` into `vendor/build.prop`.
 *   6. Repack via mkfs.erofs (or mke2fs + e2fsdroid for ext4).
 *   7. Stream the result back to the user's chosen output Uri.
 */
class Patcher(private val context: Context) {
    /** Persistent cache root used for staging input/output and the work dir. */
    private val workspace: File by lazy {
        File(context.cacheDir, "malivk13").apply { mkdirs() }
    }

    /** Where unpacked payload assets live (extracted on first run). */
    private val payloadDir: File by lazy { File(workspace, "payload") }

    /** Where renamed native binaries live (`applicationInfo.nativeLibraryDir`). */
    private val nativeBinDir: String
        get() = context.applicationInfo.nativeLibraryDir

    /** Optionally pre-extract assets in the background. Idempotent. */
    suspend fun preparePayloadAsync() {
        withContext(Dispatchers.IO) { extractPayloadAssets() }
    }

    /**
     * Run the full patch pipeline.
     *
     * @param input  SAF Uri of the stock vendor.img
     * @param output SAF Uri of the file to write the patched image to
     * @param log    callback to stream log lines to the UI
     */
    fun run(input: Uri, output: Uri, log: (String) -> Unit): PatchResult {
        log("workspace: ${workspace.absolutePath}")

        extractPayloadAssets()
        val manifest = loadManifest()
        log("payload: ${manifest.name} v${manifest.version}")

        val workdir = File(workspace, "work_${System.currentTimeMillis()}")
        workdir.mkdirs()
        try {
            val stagedInput = File(workdir, "vendor.img")
            log("[1/6] copying input from SAF -> ${stagedInput.absolutePath}")
            copyUriToFile(input, stagedInput)
            log("  ${stagedInput.length()} bytes copied")

            val info = detect(stagedInput)
            log("  sparse=${info.isSparse} fs=${info.fs}")

            var rawImage = stagedInput
            if (info.isSparse) {
                throw PatchException(
                    "Sparse vendor.img is not supported on Android (no simg2img " +
                        "on arm64 in this build). Convert to raw on PC first " +
                        "(simg2img vendor.img vendor.raw.img) and pick the raw image.",
                )
            }
            if (info.fs != "erofs" && info.fs != "ext4") {
                throw PatchException(
                    "Could not detect vendor filesystem (need EROFS or ext4 magic).",
                )
            }

            val unpackDir = File(workdir, "vendor_unpacked")
            unpackDir.mkdirs()
            log("[2/6] unpacking ${info.fs} into ${unpackDir.absolutePath}")
            when (info.fs) {
                "erofs" -> runTool(
                    "libextract_erofs.so",
                    listOf("-i", rawImage.absolutePath, "-x", "-f", "-s", "-o", unpackDir.absolutePath),
                    log,
                )
                "ext4" -> throw PatchException(
                    "ext4 vendor.img unpack not supported on Android (debugfs missing). " +
                        "Convert to EROFS on PC first or use the Windows version.",
                )
            }

            // Locate vendor root + fs_config + file_contexts produced by extract.erofs.
            val sourceBasename = rawImage.nameWithoutExtension
            val (vendorRoot, fsConfig, fileContexts) = normalizeLayout(unpackDir, sourceBasename, log)
            log("  vendor_root: ${vendorRoot.absolutePath}")
            fsConfig?.let { log("  fs_config: ${it.absolutePath}") }
            fileContexts?.let { log("  file_contexts: ${it.absolutePath}") }

            log("[3/6] overlaying Mali payload")
            val overlaid = overlayFiles(vendorRoot, manifest.vendorRelativeFiles, log)
            if (fsConfig != null) ensureFsConfigEntries(fsConfig, overlaid, log)

            log("[4/6] merging system.prop into vendor build.prop")
            val systemPropFile = File(payloadDir, manifest.systemPropSource.removePrefix("payload/"))
            val buildProp = File(vendorRoot, manifest.buildPropPath)
            mergeBuildProp(buildProp, parseProps(systemPropFile.readText()), log)

            log("[5/6] repacking ${info.fs}")
            val outRaw = File(workdir, "vendor_patched.raw.img")
            val uuid = UUID.randomUUID().toString()
            when (info.fs) {
                "erofs" -> runTool(
                    "libmkfs_erofs.so",
                    buildList {
                        add("-zlz4hc,9")
                        add("--mount-point=/vendor")
                        add("-U$uuid")
                        if (fsConfig != null) add("--fs-config-file=${fsConfig.absolutePath}")
                        if (fileContexts != null) add("--file-contexts=${fileContexts.absolutePath}")
                        add(outRaw.absolutePath)
                        add(vendorRoot.absolutePath)
                    },
                    log,
                )
                "ext4" -> throw PatchException("ext4 repack not supported on Android in this build.")
            }

            log("[6/6] writing output to user-chosen location")
            copyFileToUri(outRaw, output)
            val outSize = outRaw.length()
            return PatchResult(
                inputFs = info.fs,
                inputWasSparse = info.isSparse,
                inputSize = stagedInput.length(),
                outputSize = outSize,
                patchName = manifest.name,
                patchVersion = manifest.version,
            )
        } finally {
            workdir.deleteRecursively()
        }
    }

    // -----------------------------------------------------------------
    // Asset / payload bootstrap
    // -----------------------------------------------------------------

    private fun extractPayloadAssets() {
        if (File(payloadDir, ".extracted").exists()) return
        payloadDir.mkdirs()
        val am = context.assets
        // Walk the assets tree for everything that ends up under "payload/".
        copyAssetTree(am, "vendor", File(payloadDir, "vendor"))
        copyAssetFile(am, "system.prop", File(payloadDir, "system.prop"))
        copyAssetFile(am, "payload_manifest.json", File(payloadDir, "payload_manifest.json"))
        File(payloadDir, ".extracted").writeText(System.currentTimeMillis().toString())
    }

    private fun copyAssetTree(am: android.content.res.AssetManager, dirRel: String, dst: File) {
        val children = am.list(dirRel) ?: emptyArray()
        if (children.isEmpty()) {
            // It's a file
            try {
                val parent = dst.parentFile ?: return
                parent.mkdirs()
                am.open(dirRel).use { input -> dst.outputStream().use { input.copyTo(it) } }
            } catch (_: FileNotFoundException) {
                // ignore — list returned empty for a missing dir
            }
            return
        }
        dst.mkdirs()
        for (child in children) {
            val childRel = if (dirRel.isEmpty()) child else "$dirRel/$child"
            val out = File(dst, child)
            val grand = am.list(childRel) ?: emptyArray()
            if (grand.isEmpty()) {
                copyAssetFile(am, childRel, out)
            } else {
                copyAssetTree(am, childRel, out)
            }
        }
    }

    private fun copyAssetFile(am: android.content.res.AssetManager, rel: String, dst: File) {
        dst.parentFile?.mkdirs()
        try {
            am.open(rel).use { input -> dst.outputStream().use { input.copyTo(it) } }
        } catch (e: IOException) {
            throw PatchException("failed to copy bundled asset $rel: ${e.message}")
        }
    }

    private fun loadManifest(): PatcherManifest {
        val file = File(payloadDir, "payload_manifest.json")
        val text = file.readText()
        return Json { ignoreUnknownKeys = true }.decodeFromString(PatcherManifest.serializer(), text)
    }

    // -----------------------------------------------------------------
    // SAF I/O
    // -----------------------------------------------------------------

    private fun copyUriToFile(uri: Uri, dst: File) {
        context.contentResolver.openInputStream(uri).use { input ->
            requireNotNull(input) { "Could not open input URI" }
            dst.outputStream().use { input.copyTo(it) }
        }
    }

    private fun copyFileToUri(src: File, uri: Uri) {
        context.contentResolver.openOutputStream(uri, "w").use { output ->
            requireNotNull(output) { "Could not open output URI" }
            src.inputStream().use { it.copyTo(output) }
            output.flush()
        }
    }

    // -----------------------------------------------------------------
    // Format detection
    // -----------------------------------------------------------------

    private fun detect(image: File): ImageInfo {
        val head = ByteArray(4096)
        image.inputStream().use { it.read(head) }
        val isSparse = head.size >= 4 && head[0] == 0x3a.toByte() &&
            head[1] == 0xff.toByte() && head[2] == 0x26.toByte() && head[3] == 0xed.toByte()
        var fs = "unknown"
        if (!isSparse) {
            val erofsOff = 1024
            if (head.size >= erofsOff + 4 &&
                head[erofsOff] == 0xe2.toByte() && head[erofsOff + 1] == 0xe1.toByte() &&
                head[erofsOff + 2] == 0xf5.toByte() && head[erofsOff + 3] == 0xe0.toByte()
            ) {
                fs = "erofs"
            } else {
                val extOff = 1080
                if (head.size >= extOff + 2 && head[extOff] == 0x53.toByte() &&
                    head[extOff + 1] == 0xef.toByte()
                ) {
                    fs = "ext4"
                }
            }
        }
        return ImageInfo(isSparse, fs, image.length())
    }

    // -----------------------------------------------------------------
    // Native binary execution
    // -----------------------------------------------------------------

    private fun runTool(libName: String, args: List<String>, log: (String) -> Unit) {
        val bin = File(nativeBinDir, libName)
        if (!bin.exists()) {
            throw PatchException("Bundled native binary missing: ${bin.absolutePath}")
        }
        val cmd = listOf(bin.absolutePath) + args
        log("$ ${cmd.joinToString(" ")}")
        val pb = ProcessBuilder(cmd).redirectErrorStream(true)
        pb.environment().putAll(System.getenv())
        val proc = try {
            pb.start()
        } catch (e: IOException) {
            throw PatchException("failed to launch ${bin.name}: ${e.message}")
        }
        proc.inputStream.bufferedReader().use { reader ->
            reader.lineSequence().forEach { line -> log("  $line") }
        }
        val rc = proc.waitFor()
        if (rc != 0) throw PatchException("${bin.name} exited with status $rc")
    }

    // -----------------------------------------------------------------
    // Layout normalization (mirrors core/patcher.py:normalize_vendor_layout)
    // -----------------------------------------------------------------

    private fun normalizeLayout(
        unpackDir: File,
        sourceBasename: String,
        log: (String) -> Unit,
    ): Triple<File, File?, File?> {
        val srcRoot = File(unpackDir, sourceBasename)
        val canonical = File(unpackDir, "vendor")
        if (srcRoot.exists() && srcRoot != canonical) {
            if (canonical.exists()) canonical.deleteRecursively()
            srcRoot.renameTo(canonical)
            log("  normalized vendor root: $sourceBasename -> vendor")
        }
        val vendorRoot = if (canonical.exists()) canonical else srcRoot

        val configDir = File(unpackDir, "config")
        var fsConfig: File? = null
        var fileContexts: File? = null
        if (configDir.isDirectory) {
            for (p in configDir.listFiles().orEmpty()) {
                when {
                    p.name.endsWith("_fs_config") -> fsConfig = p
                    p.name.endsWith("_file_contexts") -> fileContexts = p
                }
            }
        }

        if (fsConfig != null) {
            val text = fsConfig!!.readText()
            val rewritten =
                text.lineSequence().map { line ->
                    val prefix = "$sourceBasename/"
                    val dirEntry = "$sourceBasename "
                    when {
                        line.startsWith(prefix) -> "vendor/" + line.removePrefix(prefix)
                        line.startsWith(dirEntry) -> "vendor " + line.removePrefix(dirEntry)
                        else -> line
                    }
                }.joinToString("\n")
            val newPath = File(configDir, "vendor_fs_config")
            newPath.writeText(rewritten + "\n")
            if (newPath != fsConfig) fsConfig!!.delete()
            fsConfig = newPath
        }
        if (fileContexts != null) {
            val newPath = File(configDir, "vendor_file_contexts")
            if (newPath != fileContexts) {
                Files.copy(
                    fileContexts!!.toPath(), newPath.toPath(),
                    StandardCopyOption.REPLACE_EXISTING,
                )
                fileContexts!!.delete()
            }
            fileContexts = newPath
        }

        return Triple(vendorRoot, fsConfig, fileContexts)
    }

    // -----------------------------------------------------------------
    // Overlay + fs_config maintenance
    // -----------------------------------------------------------------

    private fun overlayFiles(vendorRoot: File, files: List<String>, log: (String) -> Unit): List<String> {
        val copied = mutableListOf<String>()
        val srcRoot = File(payloadDir, "vendor")
        for (rel in files) {
            val src = File(srcRoot, rel)
            val dst = File(vendorRoot, rel)
            if (!src.exists()) {
                log("  WARNING: payload file missing, skipping: $rel")
                continue
            }
            dst.parentFile?.mkdirs()
            Files.copy(src.toPath(), dst.toPath(), StandardCopyOption.REPLACE_EXISTING)
            log("  overlay: $rel  (${src.length()} bytes)")
            copied += rel
        }
        return copied
    }

    private fun ensureFsConfigEntries(
        fsConfig: File,
        overlaidRelpaths: List<String>,
        log: (String) -> Unit,
    ) {
        val text = fsConfig.readText()
        val existing = text.lineSequence()
            .map { it.trim() }
            .filter { it.isNotEmpty() && !it.startsWith("#") }
            .map { it.substringBefore(" ").trimEnd('/') }
            .toMutableSet()

        val needed = mutableListOf<Pair<String, Boolean>>()
        for (rel in overlaidRelpaths) {
            val parts = rel.split("/")
            for (i in 1 until parts.size) {
                needed += "vendor/" + parts.subList(0, i).joinToString("/") to true
            }
            needed += "vendor/$rel" to false
        }

        val additions = mutableListOf<String>()
        val seen = mutableSetOf<String>()
        for ((key, isDir) in needed) {
            if (key in seen) continue
            seen += key
            if (key in existing) continue
            additions += if (isDir) "$key 0 2000 0755" else "$key 0 0 0644"
        }
        if (additions.isEmpty()) return

        val keptLines = text.lineSequence()
            .filter { it.isNotBlank() && !it.trimStart().startsWith("#") }
            .toList()
        fsConfig.writeText((keptLines + additions).joinToString("\n") + "\n")
        log("  added ${additions.size} fs_config entries for overlay")
    }

    // -----------------------------------------------------------------
    // Build.prop merge
    // -----------------------------------------------------------------

    private fun parseProps(text: String): List<Pair<String, String>> {
        val out = mutableListOf<Pair<String, String>>()
        for (line in text.lines()) {
            val s = line.trim()
            if (s.isEmpty() || s.startsWith("#") || !s.contains("=")) continue
            val (k, v) = s.split("=", limit = 2)
            out += k.trim() to v.trim()
        }
        return out
    }

    private fun mergeBuildProp(buildProp: File, props: List<Pair<String, String>>, log: (String) -> Unit) {
        val existing = if (buildProp.exists()) buildProp.readLines().toMutableList() else mutableListOf()
        val toSet = props.toMap()
        val written = mutableSetOf<String>()
        val newLines = mutableListOf<String>()
        for (line in existing) {
            val s = line.trim()
            if (s.isEmpty() || s.startsWith("#") || !s.contains("=")) {
                newLines += line
                continue
            }
            val k = s.substringBefore("=").trim()
            if (toSet.containsKey(k)) {
                newLines += "$k=${toSet[k]}"
                written += k
            } else {
                newLines += line
            }
        }
        val appended = props.filter { (k, _) -> k !in written }
        if (appended.isNotEmpty()) {
            if (newLines.isNotEmpty() && newLines.last().isNotBlank()) newLines += ""
            newLines += "# Mali-G57 MC2 / Vulkan 1.3 patch (MaliVK13Patcher)"
            for ((k, v) in appended) newLines += "$k=$v"
        }
        buildProp.writeText(newLines.joinToString("\n") + "\n")
        log("  merged ${props.size} prop key(s); replaced ${written.size}, appended ${appended.size}")
    }
}

class PatchException(msg: String) : RuntimeException(msg)

private data class ImageInfo(val isSparse: Boolean, val fs: String, val size: Long)

data class PatchResult(
    val inputFs: String,
    val inputWasSparse: Boolean,
    val inputSize: Long,
    val outputSize: Long,
    val patchName: String,
    val patchVersion: String,
)

@Serializable
private data class PatcherManifest(
    val name: String,
    val version: String,
    val source_module: String,
    val overlay_root: String,
    val vendor_relative_files: List<String>,
    val build_prop_path: String,
    val system_prop_source: String,
    val build_prop_merge_strategy: String,
) {
    val sourceModule: String get() = source_module
    val overlayRoot: String get() = overlay_root
    val vendorRelativeFiles: List<String> get() = vendor_relative_files
    val buildPropPath: String get() = build_prop_path
    val systemPropSource: String get() = system_prop_source
}
