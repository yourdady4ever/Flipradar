package com.flipradar.app.image

import com.flipradar.app.AppInfo
import com.flipradar.app.util.CancelSignal
import java.io.ByteArrayOutputStream
import java.io.IOException
import java.io.InputStream
import java.net.HttpURLConnection
import java.net.URI

/** A failed image download.  [permanent] failures (4xx, too large, not an image) are not retried soon. */
class ImageFetchException(message: String, val permanent: Boolean = false, cause: Throwable? = null) : IOException(message, cause)

/**
 * Downloads product images (pure JVM; blocking, call it off the main thread).
 *
 * Image hosts are third parties, so a request carries NO Authorization header, no cookies and no
 * FLIP RADAR token: this class never sees the token and is separate from the API client on purpose.
 * Redirects are followed by hand (at most [ImagePolicy.MAX_REDIRECTS]) and every hop must pass
 * [isAllowed] again (HTTPS only, no LAN hosts).  The body is capped at [maxBytes], checked against
 * Content-Length first and again while streaming.  A cancelled task (util/CancelSignal.kt)
 * disconnects the connection, which aborts a blocked read.
 */
class ImageFetcher(
    private val maxBytes: Int = ImagePolicy.MAX_DOWNLOAD_BYTES,
    private val connectTimeoutMs: Int = 10_000,
    private val readTimeoutMs: Int = 15_000,
    private val isAllowed: (String) -> Boolean = ImagePolicy::isAllowedUrl,
) {
    fun fetch(url: String): ByteArray {
        var current = url.trim()
        for (hop in 0..ImagePolicy.MAX_REDIRECTS) {
            if (!isAllowed(current)) throw ImageFetchException("Image URL not allowed", permanent = true)
            val conn = try {
                URI(current).toURL().openConnection() as HttpURLConnection
            } catch (e: Exception) {
                throw ImageFetchException("Invalid image URL", permanent = true, cause = e)
            }
            val cancel = CancelSignal.current()
            try {
                if (cancel != null && !cancel.setAbort { conn.disconnect() }) throw ImageFetchException("Cancelled")
                conn.instanceFollowRedirects = false
                conn.useCaches = false // the app keeps its own disk cache
                conn.connectTimeout = connectTimeoutMs
                conn.readTimeout = readTimeoutMs
                conn.setRequestProperty("Accept", "image/webp,image/png,image/jpeg,image/*;q=0.8")
                conn.setRequestProperty("User-Agent", "FlipRadar-Android/${AppInfo.VERSION_NAME}")
                // Deliberately no Authorization / Cookie headers: image hosts never get credentials.
                val status = conn.responseCode
                if (status in 300..399) {
                    val location = conn.getHeaderField("Location")
                        ?: throw ImageFetchException("Redirect without Location", permanent = true)
                    current = try {
                        URI(current).resolve(location.trim()).toString()
                    } catch (e: Exception) {
                        throw ImageFetchException("Invalid redirect", permanent = true, cause = e)
                    }
                    continue
                }
                if (status != HttpURLConnection.HTTP_OK) {
                    throw ImageFetchException("HTTP $status", permanent = status in 400..499 && status != 408 && status != 429)
                }
                if (!ImagePolicy.isImageContentType(conn.contentType)) {
                    throw ImageFetchException("Not an image", permanent = true)
                }
                val length = conn.contentLengthLong
                if (length > maxBytes) throw ImageFetchException("Image too large", permanent = true)
                return conn.inputStream.use { readCapped(it) }
            } catch (e: ImageFetchException) {
                throw e
            } catch (e: IOException) {
                throw ImageFetchException("Network error: ${e.javaClass.simpleName}", cause = e)
            } finally {
                cancel?.setAbort(null)
                conn.disconnect()
            }
        }
        throw ImageFetchException("Too many redirects", permanent = true)
    }

    private fun readCapped(input: InputStream): ByteArray {
        val out = ByteArrayOutputStream()
        val buf = ByteArray(16 * 1024)
        var total = 0
        while (true) {
            val n = input.read(buf)
            if (n < 0) break
            total += n
            if (total > maxBytes) throw ImageFetchException("Image too large", permanent = true)
            out.write(buf, 0, n)
        }
        if (total == 0) throw ImageFetchException("Empty image", permanent = true)
        return out.toByteArray()
    }
}
