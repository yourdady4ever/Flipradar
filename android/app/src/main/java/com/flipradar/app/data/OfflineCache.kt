package com.flipradar.app.data

import com.flipradar.app.util.ServerUrl
import org.json.JSONObject
import java.io.File
import java.io.IOException
import java.util.TreeMap

/**
 * Last successful server responses kept for offline use (pure JVM, unit-tested): the first page of the
 * opportunity list and the analytics summary.  On Android the files live in the app's private files dir
 * (`filesDir/offline_cache`, excluded from backups like all app data).
 *
 * Each slot is one JSON envelope:
 * `{"format": 1, "server": "<base url>", "saved_at_ms": <epoch ms>, "params": {...}, "data": <server JSON>}`.
 * The server's JSON is stored as received and parsed with the normal ApiModels parsers when it is used,
 * so the cache never drifts from the parsers.  It belongs to one server: an entry saved for another
 * server URL is never returned.  `params` are the request's filters; a cached entry is still shown for
 * other filters, flagged by [Loaded.sameParams].  The API token is never stored here.
 */
class OfflineCache(private val dir: File, private val clock: () -> Long = System::currentTimeMillis) {

    enum class Slot(val fileName: String) {
        OPPORTUNITIES("opportunities_first_page.json"),
        ANALYTICS_SUMMARY("analytics_summary.json"),
    }

    class Entry(val savedAtMs: Long, val server: String, val params: Map<String, String>, val data: JSONObject)

    companion object {
        const val FORMAT = 1
        const val DIR_NAME = "offline_cache"
        /** Refuse to read anything bigger: a first page is a few dozen KB. */
        private const val MAX_FILE_BYTES = 4L * 1024 * 1024

        /** Request parameters as a sorted string map; null and blank values are dropped. */
        fun params(vararg pairs: Pair<String, Any?>): Map<String, String> = params(pairs.asList())

        fun params(pairs: List<Pair<String, Any?>>): Map<String, String> {
            val out = TreeMap<String, String>()
            for ((k, v) in pairs) {
                val s = v?.toString() ?: continue
                if (s.isNotBlank()) out[k] = s
            }
            return out
        }
    }

    private fun file(slot: Slot) = File(dir, slot.fileName)

    /** Atomically replaces [slot] with [data]; false (nothing changed) on an I/O error. */
    fun save(slot: Slot, server: String, params: Map<String, String>, data: JSONObject): Boolean {
        val envelope = JSONObject()
            .put("format", FORMAT)
            .put("server", server)
            .put("saved_at_ms", clock())
            .put("params", JSONObject().also { o -> params.forEach { (k, v) -> o.put(k, v) } })
            .put("data", data)
        var tmp: File? = null
        return try {
            if (!dir.isDirectory && !dir.mkdirs()) return false
            tmp = File.createTempFile(slot.name.lowercase(), ".tmp", dir)
            tmp.writeText(envelope.toString(), Charsets.UTF_8)
            val target = file(slot)
            if (!tmp.renameTo(target)) {
                target.delete()
                if (!tmp.renameTo(target)) return false
            }
            true
        } catch (e: IOException) {
            false
        } catch (e: SecurityException) {
            false
        } finally {
            tmp?.takeIf { it.exists() }?.delete()
        }
    }

    /** The entry saved for [server] in [slot], or null (none, another server, unreadable, other format). */
    fun load(slot: Slot, server: String): Entry? {
        val f = file(slot)
        return try {
            if (!f.isFile || f.length() > MAX_FILE_BYTES) return null
            val o = JSONObject(f.readText(Charsets.UTF_8))
            if (o.optInt("format", -1) != FORMAT) return null
            val savedServer = o.strOrNull("server") ?: return null
            if (!ServerUrl.sameServer(savedServer, server)) return null
            val savedAt = o.longOrNull("saved_at_ms") ?: return null
            val data = o.objOrNull("data") ?: return null
            val p = o.obj("params")
            val params = TreeMap<String, String>()
            val keys = p.keys()
            while (keys.hasNext()) {
                val k = keys.next()
                p.strOrNull(k)?.let { params[k] = it }
            }
            Entry(savedAt, savedServer, params, data)
        } catch (e: Exception) {
            null // corrupt or unreadable: behave as if nothing was cached
        }
    }

    fun clear(slot: Slot) {
        file(slot).delete()
    }

    fun clearAll() {
        dir.listFiles()?.forEach { it.delete() }
    }

    /**
     * Runs [fetch] (a blocking API call returning the raw response) and parses it with [parse].  On
     * success the raw response is saved to [slot] (best effort).  When the call fails with an
     * [ApiException] and [slot] holds an entry for [server], that entry is returned instead, together
     * with the error and its save time; otherwise the error is rethrown.  Other exceptions (bugs) are
     * never hidden.
     */
    fun <T> fetchOrCached(
        slot: Slot,
        server: String,
        params: Map<String, String>,
        fetch: () -> JSONObject,
        parse: (JSONObject) -> T,
    ): Loaded<T> {
        val raw: JSONObject
        val value: T
        try {
            raw = fetch()
            value = parseOrThrow(raw, parse)
        } catch (e: ApiException) {
            val cached = load(slot, server) ?: throw e
            val parsed = try {
                parse(cached.data)
            } catch (pe: Exception) {
                throw e // unusable cache: report the real failure
            }
            return Loaded(parsed, cached.savedAtMs, e, sameParams = cached.params == params)
        }
        save(slot, server, params, raw)
        return Loaded(value)
    }

    private fun <T> parseOrThrow(raw: JSONObject, parse: (JSONObject) -> T): T = try {
        parse(raw)
    } catch (e: ApiException) {
        throw e
    } catch (e: Exception) {
        throw ApiException(ApiException.Kind.PARSE, "Unexpected response format: ${e.javaClass.simpleName}", cause = e)
    }
}

/**
 * A value from the server, or from the offline cache when the request failed: then [cachedAtMs] is the
 * time it was saved, [error] the failure, and [sameParams] whether it was saved for the same filters.
 */
data class Loaded<T>(
    val value: T,
    val cachedAtMs: Long? = null,
    val error: Throwable? = null,
    val sameParams: Boolean = true,
) {
    val fromCache: Boolean get() = cachedAtMs != null
}
