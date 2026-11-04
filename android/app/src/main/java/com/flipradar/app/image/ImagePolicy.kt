package com.flipradar.app.image

import com.flipradar.app.util.ServerUrl
import java.net.URI
import java.security.MessageDigest

/**
 * Rules for product images (pure JVM, unit-tested): which URLs may be fetched, how large a download
 * may be, how far a bitmap is downsampled, and the cache key of a URL.
 *
 * Images come from marketplace listings, i.e. from third parties, so the rules are strict: HTTPS only,
 * no credentials in the URL, no hosts on the phone's local network (a listing must not make the phone
 * call a LAN device), and never the FLIP RADAR API token or any other auth header (see [ImageFetcher]).
 */
object ImagePolicy {
    const val MAX_URL_LENGTH = 2048

    /** Download cap per image; product photos from marketplace CDNs are far smaller. */
    const val MAX_DOWNLOAD_BYTES = 3 * 1024 * 1024

    const val MAX_REDIRECTS = 3

    /** Upper bound of decoded pixels (4 MP = 16 MB as ARGB_8888) whatever the requested size. */
    const val MAX_DECODED_PIXELS = 4L * 1024 * 1024

    /**
     * True for an absolute `https://` URL with a public-looking host, no user-info and a sane length.
     * Purely textual (no DNS lookups), safe on the main thread.
     */
    fun isAllowedUrl(url: String?): Boolean {
        val s = url?.trim() ?: return false
        if (s.isEmpty() || s.length > MAX_URL_LENGTH || s.any { it.isWhitespace() || it.code < 0x20 }) return false
        val uri = try {
            URI(s)
        } catch (e: Exception) {
            return false
        }
        if (uri.scheme?.lowercase() != "https") return false
        if (uri.rawUserInfo != null) return false
        val host = uri.host?.lowercase()?.removePrefix("[")?.removeSuffix("]") ?: return false
        if (host.isEmpty()) return false
        if (uri.port != -1 && uri.port !in 1..65535) return false
        if (ServerUrl.isPrivateHost(host)) return false
        if (host == "0.0.0.0" || host.startsWith("0.") || host == "::" || host == "0:0:0:0:0:0:0:0") return false
        return true
    }

    /**
     * True when a response's Content-Type may hold an image.  A missing type and the generic
     * `application/octet-stream` are accepted (the decoder has the last word); HTML error pages are not.
     */
    fun isImageContentType(contentType: String?): Boolean {
        val t = contentType?.substringBefore(';')?.trim()?.lowercase() ?: return true
        return t.isEmpty() || t.startsWith("image/") || t == "application/octet-stream" || t == "binary/octet-stream"
    }

    /**
     * BitmapFactory.Options.inSampleSize for a [srcWidth] x [srcHeight] image shown at about
     * [reqWidth] x [reqHeight]: the largest power of two that keeps both sides at least the requested
     * size, then doubled further while the decoded image would exceed [maxPixels].  Always >= 1.
     */
    fun sampleSize(srcWidth: Int, srcHeight: Int, reqWidth: Int, reqHeight: Int, maxPixels: Long = MAX_DECODED_PIXELS): Int {
        if (srcWidth <= 0 || srcHeight <= 0) return 1
        val rw = maxOf(1, reqWidth)
        val rh = maxOf(1, reqHeight)
        var sample = 1
        if (srcWidth > rw || srcHeight > rh) {
            val halfW = srcWidth / 2
            val halfH = srcHeight / 2
            while (halfW / sample >= rw && halfH / sample >= rh) sample *= 2
        }
        val limit = maxOf(1L, maxPixels)
        while ((srcWidth / sample).toLong() * (srcHeight / sample).toLong() > limit && sample < (1 shl 16)) sample *= 2
        return sample
    }

    /** Stable file-name-safe key of a URL: lowercase hex SHA-256 of its UTF-8 bytes (64 chars). */
    fun cacheKey(url: String): String {
        val digest = MessageDigest.getInstance("SHA-256").digest(url.toByteArray(Charsets.UTF_8))
        val sb = StringBuilder(digest.size * 2)
        for (b in digest) {
            val v = b.toInt() and 0xff
            sb.append(HEX[v ushr 4]).append(HEX[v and 0x0f])
        }
        return sb.toString()
    }

    private val HEX = "0123456789abcdef".toCharArray()
}
