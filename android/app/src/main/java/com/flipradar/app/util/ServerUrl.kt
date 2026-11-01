package com.flipradar.app.util

import java.net.URI
import java.net.URLEncoder

/**
 * Validation and normalisation of the user's FLIP RADAR server base URL.
 *
 * Pure JVM code (no android.*, no DNS lookups) so it is unit-testable and safe on the main thread.
 */
object ServerUrl {

    enum class Problem { EMPTY, BAD_SCHEME, NO_HOST, BAD_PORT, HAS_QUERY, HAS_CREDENTIALS, MALFORMED }

    sealed class Result {
        data class Valid(val url: String, val host: String, val https: Boolean) : Result()
        data class Invalid(val problem: Problem) : Result()
    }

    /**
     * Normalises user input into a base URL without trailing slash, e.g.
     * "192.168.1.20:8000" -> "http://192.168.1.20:8000", "https://radar.example.com/flip/" ->
     * "https://radar.example.com/flip".  Input without a scheme gets http:// for private/LAN hosts and
     * https:// for everything else.
     */
    fun normalize(input: String?): Result {
        val raw = input?.trim().orEmpty()
        if (raw.isEmpty()) return Result.Invalid(Problem.EMPTY)
        if (raw.any { it.isWhitespace() }) return Result.Invalid(Problem.MALFORMED)

        val withScheme = if (raw.contains("://")) raw else {
            val hostGuess = raw.substringBefore('/').substringBefore('?').let { hostPart ->
                when {
                    hostPart.startsWith("[") -> hostPart.substringBefore(']').removePrefix("[")
                    hostPart.count { it == ':' } == 1 -> hostPart.substringBefore(':')
                    else -> hostPart
                }
            }
            (if (isPrivateHost(hostGuess)) "http://" else "https://") + raw
        }

        if (withScheme.substringAfter("://").isEmpty()) return Result.Invalid(Problem.NO_HOST)
        val uri = try {
            URI(withScheme)
        } catch (e: Exception) {
            return Result.Invalid(Problem.MALFORMED)
        }
        val scheme = uri.scheme?.lowercase() ?: return Result.Invalid(Problem.BAD_SCHEME)
        if (scheme != "http" && scheme != "https") return Result.Invalid(Problem.BAD_SCHEME)
        if (uri.rawUserInfo != null) return Result.Invalid(Problem.HAS_CREDENTIALS)
        val host = uri.host
        if (host.isNullOrEmpty()) {
            // java.net.URI leaves host null for e.g. "http://bad_host:80"; distinguish a bad port.
            val authority = uri.rawAuthority
            if (authority != null && authority.substringAfterLast(':', "").let { p -> p.isNotEmpty() && p.toIntOrNull() == null }) {
                return Result.Invalid(Problem.BAD_PORT)
            }
            return Result.Invalid(Problem.NO_HOST)
        }
        if (uri.port != -1 && (uri.port < 1 || uri.port > 65535)) return Result.Invalid(Problem.BAD_PORT)
        if (uri.rawQuery != null || uri.rawFragment != null) return Result.Invalid(Problem.HAS_QUERY)

        val path = (uri.rawPath ?: "").trimEnd('/')
        val portPart = if (uri.port == -1) "" else ":${uri.port}"
        val hostPart = if (host.contains(':') && !host.startsWith("[")) "[$host]" else host
        val normalized = "$scheme://${hostPart.lowercase()}$portPart$path"
        return Result.Valid(normalized, host.removePrefix("[").removeSuffix("]").lowercase(), scheme == "https")
    }

    /**
     * True for hosts that are only reachable on a local/private network: loopback, RFC 1918, link-local,
     * CGNAT/Tailscale (100.64.0.0/10), IPv6 ULA/link-local, single-label names and common LAN suffixes.
     * Purely textual - never resolves DNS.
     */
    fun isPrivateHost(hostInput: String?): Boolean {
        val host = hostInput?.trim()?.lowercase()?.removePrefix("[")?.removeSuffix("]") ?: return false
        if (host.isEmpty()) return false
        if (host == "localhost" || host.endsWith(".localhost")) return true
        parseIpv4(host)?.let { o ->
            return when {
                o[0] == 10 -> true
                o[0] == 127 -> true
                o[0] == 172 && o[1] in 16..31 -> true
                o[0] == 192 && o[1] == 168 -> true
                o[0] == 169 && o[1] == 254 -> true
                o[0] == 100 && o[1] in 64..127 -> true
                else -> false
            }
        }
        if (host.contains(':')) { // IPv6 literal
            if (host == "::1") return true
            val first = host.substringBefore(':')
            val firstVal = first.toIntOrNull(16) ?: return false
            return (firstVal and 0xfe00) == 0xfc00 || (firstVal and 0xffc0) == 0xfe80
        }
        if (!host.contains('.')) return true // single-label LAN name, e.g. "raspberrypi"
        val lanSuffixes = listOf(".local", ".lan", ".home", ".home.arpa", ".internal", ".intranet", ".localdomain")
        return lanSuffixes.any { host.endsWith(it) }
    }

    private fun parseIpv4(host: String): IntArray? {
        val parts = host.split('.')
        if (parts.size != 4) return null
        val out = IntArray(4)
        for (i in 0 until 4) {
            val p = parts[i]
            if (p.isEmpty() || p.length > 3 || !p.all { it.isDigit() }) return null
            val v = p.toInt()
            if (v > 255) return null
            out[i] = v
        }
        return out
    }

    /** True when the URL uses cleartext HTTP to a host that is not on a private network (token exposed). */
    fun isInsecurePublic(url: String?): Boolean {
        val r = normalize(url)
        if (r !is Result.Valid) return false
        return !r.https && !isPrivateHost(r.host)
    }

    /**
     * Builds an absolute request URL from the normalised [base], an absolute API [path] and query
     * parameters.  Null or blank parameter values are skipped; values are UTF-8 form-encoded.
     */
    fun buildUrl(base: String, path: String, params: List<Pair<String, Any?>> = emptyList()): String {
        val sb = StringBuilder(base.trimEnd('/'))
        sb.append(if (path.startsWith("/")) path else "/$path")
        var first = true
        for ((key, value) in params) {
            val text = value?.toString() ?: continue
            if (text.isBlank()) continue
            sb.append(if (first) '?' else '&')
            first = false
            sb.append(encode(key)).append('=').append(encode(text))
        }
        return sb.toString()
    }

    /** Encodes a single path segment (spaces as %20, never '+'). */
    fun encodePathSegment(segment: String): String = encode(segment).replace("+", "%20")

    private fun encode(s: String): String = URLEncoder.encode(s, "UTF-8")
}
