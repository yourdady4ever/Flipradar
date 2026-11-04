package com.flipradar.app.image

import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.view.View
import android.widget.ImageView
import com.flipradar.app.util.Async
import com.flipradar.app.util.TaskRunner
import java.io.File
import java.util.concurrent.Semaphore

/**
 * Loads product images into ImageViews: memory LRU of decoded bitmaps -> disk cache of downloaded bytes
 * (`cacheDir/images`) -> HTTPS download ([ImageFetcher]: no token, no auth header, size-capped).
 * Decoding is downsampled with BitmapFactory.Options.inSampleSize to about the view size.
 *
 * All public methods run on the main thread.  Work runs on the app's TaskRunner (util/Async.kt); at most
 * [MAX_PARALLEL_DOWNLOADS] downloads run at once.  Loading another URL into a view, [cancel] (list rows
 * moved to the recycler's scrap heap) and [cancelFor] (Activity destroyed) cancel the view's previous
 * task, which also disconnects its download.  A result only reaches the view it was requested for.
 */
object ImageLoader {
    private const val MAX_PARALLEL_DOWNLOADS = 4
    private const val DISK_CACHE_BYTES = 25L * 1024 * 1024
    private const val MEMORY_CACHE_MAX_BYTES = 16L * 1024 * 1024
    /** Permanent failures (404, too large, not an image) are not retried for this long. */
    private const val FAILURE_TTL_MS = 10 * 60 * 1000L

    private val memory = SizedLruCache<String, Bitmap>(memoryBudget()) { it.allocationByteCount.toLong() }
    private val failed = SizedLruCache<String, Long>(256) { 1L } // url -> time of a permanent failure
    private val downloads = Semaphore(MAX_PARALLEL_DOWNLOADS)
    private val fetcher = ImageFetcher()
    private val pending = HashMap<ImageView, Pending>() // main thread only
    @Volatile private var disk: DiskImageCache? = null

    private class Pending(val key: String, val task: TaskRunner.Task)

    /** A small slice of the heap: 1/16 of the max heap, at most 16 MB. */
    private fun memoryBudget(): Long = minOf(Runtime.getRuntime().maxMemory() / 16, MEMORY_CACHE_MAX_BYTES).coerceAtLeast(1L * 1024 * 1024)

    private fun diskCache(context: Context): DiskImageCache =
        disk ?: synchronized(this) {
            disk ?: DiskImageCache(File(context.applicationContext.cacheDir, "images"), DISK_CACHE_BYTES).also { disk = it }
        }

    /**
     * Shows [url] in [view], decoded for about [reqWidthPx] x [reqHeightPx].  A missing or disallowed URL
     * (not https, LAN host, ...) shows nothing: the view is hidden when [hideWhenMissing], else left empty.
     */
    fun load(view: ImageView, url: String?, reqWidthPx: Int, reqHeightPx: Int, hideWhenMissing: Boolean = true) {
        val clean = url?.trim()
        if (clean == null || !ImagePolicy.isAllowedUrl(clean)) {
            cancel(view)
            view.setImageDrawable(null)
            view.visibility = if (hideWhenMissing) View.GONE else View.VISIBLE
            return
        }
        view.visibility = View.VISIBLE
        val key = "$clean#${reqWidthPx}x$reqHeightPx"
        if (pending[view]?.key == key) return // already loading exactly this
        cancel(view)
        memory.get(key)?.let {
            view.setImageBitmap(it)
            return
        }
        view.setImageDrawable(null) // the placeholder background shows until the bitmap arrives
        val failedAt = failed.get(clean)
        if (failedAt != null && System.currentTimeMillis() - failedAt < FAILURE_TTL_MS) return
        val cache = diskCache(view.context)
        var task: TaskRunner.Task? = null
        task = Async.run(
            { fetchAndDecode(cache, clean, reqWidthPx, reqHeightPx) },
            { bitmap ->
                if (pending[view]?.task === task) {
                    pending.remove(view)
                    memory.put(key, bitmap)
                    view.setImageBitmap(bitmap)
                }
            },
            { error ->
                if (pending[view]?.task === task) pending.remove(view)
                if (error is ImageFetchException && error.permanent) failed.put(clean, System.currentTimeMillis())
            },
        )
        pending[view] = Pending(key, task)
    }

    /** Cancels the load in progress for [view], if any (e.g. its list row was recycled). */
    fun cancel(view: ImageView) {
        pending.remove(view)?.task?.cancel()
    }

    /** Cancels every load whose view belongs to [context] (call from Activity.onDestroy). */
    fun cancelFor(context: Context) {
        val it = pending.entries.iterator()
        while (it.hasNext()) {
            val e = it.next()
            if (e.key.context === context) {
                it.remove()
                e.value.task.cancel()
            }
        }
    }

    /** Background thread: disk cache or download, then a downsampled decode. */
    private fun fetchAndDecode(cache: DiskImageCache, url: String, reqWidth: Int, reqHeight: Int): Bitmap {
        val cached = cache.get(url)
        val bytes = cached ?: run {
            downloads.acquire() // interruptible: a cancelled task stops waiting here
            try {
                fetcher.fetch(url)
            } finally {
                downloads.release()
            }
        }
        val bitmap = decode(bytes, reqWidth, reqHeight)
        if (bitmap == null) {
            if (cached != null) cache.remove(url)
            throw ImageFetchException("Not a decodable image", permanent = true)
        }
        if (cached == null) cache.put(url, bytes)
        return bitmap
    }

    private fun decode(bytes: ByteArray, reqWidth: Int, reqHeight: Int): Bitmap? {
        val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        BitmapFactory.decodeByteArray(bytes, 0, bytes.size, bounds)
        if (bounds.outWidth <= 0 || bounds.outHeight <= 0) return null
        val options = BitmapFactory.Options().apply {
            inSampleSize = ImagePolicy.sampleSize(bounds.outWidth, bounds.outHeight, reqWidth, reqHeight)
        }
        return try {
            BitmapFactory.decodeByteArray(bytes, 0, bytes.size, options)
        } catch (e: OutOfMemoryError) {
            null
        }
    }
}
