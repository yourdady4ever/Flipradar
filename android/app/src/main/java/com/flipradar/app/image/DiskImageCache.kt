package com.flipradar.app.image

import java.io.File
import java.io.IOException

/**
 * Size-bounded disk cache of downloaded image bytes (pure JVM, unit-tested).  On Android it lives in
 * `cacheDir/images`, which the system may also clear when storage runs low.
 *
 * One file per URL, named by [ImagePolicy.cacheKey] (the URL never becomes a path).  Writes go to a
 * temporary file first and are renamed into place, so a reader never sees half a file.  The file's
 * modification time is its last use; [put] then deletes the least recently used files until the total
 * fits [maxBytes].  I/O errors are swallowed: the cache is an optimisation only.
 */
class DiskImageCache(
    private val dir: File,
    private val maxBytes: Long,
    private val maxEntryBytes: Long = ImagePolicy.MAX_DOWNLOAD_BYTES.toLong(),
    private val clock: () -> Long = System::currentTimeMillis,
) {
    private fun fileFor(url: String) = File(dir, ImagePolicy.cacheKey(url) + SUFFIX)

    /** Cached bytes of [url] (marks them used), or null. */
    fun get(url: String): ByteArray? {
        val f = fileFor(url)
        return try {
            if (!f.isFile || f.length() <= 0 || f.length() > maxEntryBytes) return null
            val bytes = f.readBytes()
            f.setLastModified(clock())
            bytes
        } catch (e: IOException) {
            null
        } catch (e: SecurityException) {
            null
        }
    }

    /** Stores [bytes] for [url] and trims the cache; returns false when nothing was stored. */
    @Synchronized
    fun put(url: String, bytes: ByteArray): Boolean {
        if (bytes.isEmpty() || bytes.size > maxEntryBytes || bytes.size > maxBytes) return false
        val target = fileFor(url)
        var tmp: File? = null
        return try {
            if (!dir.isDirectory && !dir.mkdirs()) return false
            tmp = File.createTempFile("img", TMP_SUFFIX, dir)
            tmp.writeBytes(bytes)
            if (!tmp.renameTo(target)) {
                target.delete()
                if (!tmp.renameTo(target)) return false
            }
            target.setLastModified(clock())
            trim()
            true
        } catch (e: IOException) {
            false
        } catch (e: SecurityException) {
            false
        } finally {
            tmp?.takeIf { it.exists() }?.delete()
        }
    }

    fun remove(url: String) {
        fileFor(url).delete()
    }

    /** Total bytes of the cached images. */
    fun sizeBytes(): Long = entries().sumOf { it.length() }

    /** Deletes least recently used files until the total fits [maxBytes]; also drops stale temp files. */
    @Synchronized
    fun trim() {
        dir.listFiles()?.filter { it.name.endsWith(TMP_SUFFIX) && clock() - it.lastModified() > STALE_TMP_MS }?.forEach { it.delete() }
        val files = entries().sortedBy { it.lastModified() }
        var total = files.sumOf { it.length() }
        for (f in files) {
            if (total <= maxBytes) break
            val len = f.length()
            if (f.delete()) total -= len
        }
    }

    @Synchronized
    fun clear() {
        dir.listFiles()?.forEach { it.delete() }
    }

    private fun entries(): List<File> = dir.listFiles()?.filter { it.isFile && it.name.endsWith(SUFFIX) } ?: emptyList()

    private companion object {
        const val SUFFIX = ".img"
        const val TMP_SUFFIX = ".tmp"
        const val STALE_TMP_MS = 60_000L
    }
}
