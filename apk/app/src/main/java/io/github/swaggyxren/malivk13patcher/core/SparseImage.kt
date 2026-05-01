package io.github.swaggyxren.malivk13patcher.core

import java.io.BufferedInputStream
import java.io.File
import java.io.FileInputStream
import java.io.RandomAccessFile
import java.nio.ByteBuffer
import java.nio.ByteOrder

/**
 * Pure-Kotlin Android sparse image decoder. Equivalent to running `simg2img`
 * but doesn't require an arm64 binary (we don't ship one). Only the decoder
 * is needed here — the encoder (raw -> sparse) is handled by the bundled
 * arm64 `img2simg` binary, since we already have one.
 *
 * Format reference: AOSP `system/core/libsparse/sparse_format.h`
 */
internal object SparseImage {
    private const val SPARSE_HEADER_MAGIC: Int = 0xED26FF3A.toInt()

    private const val CHUNK_TYPE_RAW: Int = 0xCAC1
    private const val CHUNK_TYPE_FILL: Int = 0xCAC2
    private const val CHUNK_TYPE_DONT_CARE: Int = 0xCAC3
    private const val CHUNK_TYPE_CRC32: Int = 0xCAC4

    /** Decode an Android sparse image into a flat raw image. */
    fun desparse(input: File, output: File, log: (String) -> Unit) {
        BufferedInputStream(FileInputStream(input)).use { ri ->
            val headerBytes = ByteArray(28)
            readFully(ri, headerBytes)
            val hdr = ByteBuffer.wrap(headerBytes).order(ByteOrder.LITTLE_ENDIAN)
            val magic = hdr.int
            if (magic != SPARSE_HEADER_MAGIC) {
                throw PatchException(
                    "Not a sparse image: magic=0x${java.lang.Integer.toHexString(magic)}",
                )
            }
            // All file-level uint32 fields per AOSP `sparse_format.h`. Read as
            // signed and mask to Long so values >= 2^31 don't go negative.
            val major = hdr.short.toInt() and 0xFFFF
            val minor = hdr.short.toInt() and 0xFFFF
            val fileHdrSz = hdr.short.toInt() and 0xFFFF
            val chunkHdrSz = hdr.short.toInt() and 0xFFFF
            val blkSz = hdr.int.toLong() and 0xFFFFFFFFL
            val totalBlks = hdr.int.toLong() and 0xFFFFFFFFL
            val totalChunks = hdr.int.toLong() and 0xFFFFFFFFL
            // val checksum = hdr.int  // intentionally unused

            if (fileHdrSz > 28) skipFully(ri, (fileHdrSz - 28).toLong())
            log("  sparse v$major.$minor blkSz=$blkSz totalBlks=$totalBlks chunks=$totalChunks")

            RandomAccessFile(output, "rw").use { ro ->
                ro.setLength(totalBlks * blkSz)
                ro.seek(0)

                val chunkHdr = ByteArray(12)
                val rawBuf = ByteArray(64 * 1024)
                val fillVal = ByteArray(4)

                var blocksWritten = 0L
                var i = 0L
                while (i < totalChunks) {
                    readFully(ri, chunkHdr)
                    val ch = ByteBuffer.wrap(chunkHdr).order(ByteOrder.LITTLE_ENDIAN)
                    val type = ch.short.toInt() and 0xFFFF
                    /* reserved */ ch.short
                    // chunk_sz and total_sz are uint32 in the spec; widen to Long.
                    val chunkBlks = ch.int.toLong() and 0xFFFFFFFFL
                    val totalSz = ch.int.toLong() and 0xFFFFFFFFL

                    if (chunkHdrSz > 12) skipFully(ri, (chunkHdrSz - 12).toLong())
                    val payloadSz = totalSz - chunkHdrSz.toLong()
                    val outBytes = chunkBlks * blkSz

                    when (type) {
                        CHUNK_TYPE_RAW -> {
                            var remaining = payloadSz
                            while (remaining > 0L) {
                                val n = minOf(remaining, rawBuf.size.toLong()).toInt()
                                readFully(ri, rawBuf, n)
                                ro.write(rawBuf, 0, n)
                                remaining -= n
                            }
                        }
                        CHUNK_TYPE_FILL -> {
                            readFully(ri, fillVal)
                            // Build a one-block pattern, then write it `chunkBlks` times.
                            val patternSz = blkSz.toInt()
                            val pattern = ByteArray(patternSz)
                            for (j in 0 until patternSz) pattern[j] = fillVal[j and 3]
                            var remaining = outBytes
                            while (remaining > 0L) {
                                val n = minOf(remaining, pattern.size.toLong()).toInt()
                                ro.write(pattern, 0, n)
                                remaining -= n
                            }
                        }
                        CHUNK_TYPE_DONT_CARE -> {
                            // No payload; the file was setLength'd to zero so just skip ahead.
                            ro.seek(ro.filePointer + outBytes)
                        }
                        CHUNK_TYPE_CRC32 -> {
                            // 4-byte payload, no output bytes.
                            skipFully(ri, payloadSz)
                        }
                        else -> throw PatchException(
                            "Unknown sparse chunk type 0x${java.lang.Integer.toHexString(type)}",
                        )
                    }
                    blocksWritten += chunkBlks
                    i++
                }
                log("  desparsed $blocksWritten blocks " +
                    "(${blocksWritten * blkSz} bytes)")
            }
        }
    }

    private fun readFully(src: java.io.InputStream, dst: ByteArray, len: Int = dst.size) {
        var off = 0
        while (off < len) {
            val n = src.read(dst, off, len - off)
            if (n < 0) throw PatchException("Unexpected EOF in sparse image")
            off += n
        }
    }

    private fun skipFully(src: java.io.InputStream, n: Long) {
        var remaining = n
        while (remaining > 0) {
            val skipped = src.skip(remaining)
            if (skipped <= 0) {
                if (src.read() < 0) throw PatchException("Unexpected EOF skipping in sparse image")
                remaining -= 1
            } else {
                remaining -= skipped
            }
        }
    }
}
