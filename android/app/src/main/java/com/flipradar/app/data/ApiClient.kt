package com.flipradar.app.data

import com.flipradar.app.AppInfo
import com.flipradar.app.util.CancelSignal
import com.flipradar.app.util.ServerUrl
import org.json.JSONArray
import org.json.JSONObject
import org.json.JSONTokener
import java.io.ByteArrayOutputStream
import java.io.IOException
import java.io.InputStream
import java.net.ConnectException
import java.net.HttpURLConnection
import java.net.NoRouteToHostException
import java.net.ProtocolException
import java.net.SocketTimeoutException
import java.net.URI
import java.net.UnknownHostException
import javax.net.ssl.SSLException

/** Error raised by [ApiClient]; [kind] lets the UI pick a localized message. Never contains the token. */
class ApiException(
    val kind: Kind,
    message: String,
    val httpStatus: Int? = null,
    val detail: String? = null,
    cause: Throwable? = null,
) : Exception(message, cause) {
    enum class Kind { NOT_CONFIGURED, NETWORK, TIMEOUT, TLS, UNAUTHORIZED, NOT_FOUND, HTTP, REDIRECT, PARSE }
}

/**
 * Minimal blocking HTTP client for the FLIP RADAR API (HttpURLConnection + org.json).
 *
 * All methods block: call them from a background thread (see util/Async.kt).  When the calling task is
 * cancelled (util/CancelSignal.kt) the connection is disconnected, which aborts a blocked read.  The
 * bearer token is sent only in the Authorization header and is never logged or placed in exception
 * messages.  Redirects are NOT followed so the token can never be forwarded to another host.
 */
class ApiClient(
    baseUrl: String,
    private val token: String?,
    private val connectTimeoutMs: Int = 10_000,
    private val readTimeoutMs: Int = 30_000,
) {
    private val base: String = baseUrl.trimEnd('/')

    companion object {
        private const val MAX_BODY_BYTES = 8 * 1024 * 1024
        /** Collection runs, recompute and AI summaries can take a while on small self-hosted servers. */
        const val LONG_TIMEOUT_MS = 180_000
        const val API = "/api/v1"
    }

    // --------------------------------------------------------------------------------------------
    // Low level
    // --------------------------------------------------------------------------------------------

    fun request(
        method: String,
        path: String,
        params: List<Pair<String, Any?>> = emptyList(),
        body: Any? = null,
        auth: Boolean = true,
        readTimeout: Int = readTimeoutMs,
    ): Any? {
        val url = ServerUrl.buildUrl(base, path, params)
        val conn = try {
            URI(url).toURL().openConnection() as HttpURLConnection
        } catch (e: Exception) {
            throw ApiException(ApiException.Kind.NOT_CONFIGURED, "Invalid server URL", cause = e)
        }
        // Thread.interrupt() cannot unblock a socket read; cancelling the task disconnects instead.
        val cancel = CancelSignal.current()
        try {
            if (cancel != null && !cancel.setAbort { conn.disconnect() }) {
                throw ApiException(ApiException.Kind.NETWORK, "Request cancelled")
            }
            setMethod(conn, method)
            conn.connectTimeout = connectTimeoutMs
            conn.readTimeout = readTimeout
            conn.instanceFollowRedirects = false
            conn.useCaches = false
            conn.setRequestProperty("Accept", "application/json")
            conn.setRequestProperty("User-Agent", "FlipRadar-Android/${AppInfo.VERSION_NAME}")
            if (auth && !token.isNullOrEmpty()) conn.setRequestProperty("Authorization", "Bearer $token")
            if (body != null) {
                val bytes = body.toString().toByteArray(Charsets.UTF_8)
                conn.doOutput = true
                conn.setRequestProperty("Content-Type", "application/json; charset=utf-8")
                conn.setFixedLengthStreamingMode(bytes.size)
                conn.outputStream.use { it.write(bytes) }
            }
            val status = conn.responseCode
            val stream: InputStream? = if (status >= 400) conn.errorStream else conn.inputStream
            val text = stream?.use { readLimited(it) } ?: ""
            if (status in 300..399) {
                val location = conn.getHeaderField("Location") ?: "?"
                throw ApiException(ApiException.Kind.REDIRECT, "HTTP $status redirect to $location", status, location)
            }
            if (status >= 400) {
                val detail = ApiErrorParser.detail(text) ?: text.take(200).ifBlank { null }
                val kind = when (status) {
                    401, 403 -> ApiException.Kind.UNAUTHORIZED
                    404 -> ApiException.Kind.NOT_FOUND
                    else -> ApiException.Kind.HTTP
                }
                throw ApiException(kind, "HTTP $status" + (detail?.let { ": $it" } ?: ""), status, detail)
            }
            if (text.isBlank()) return null
            return try {
                JSONTokener(text).nextValue()
            } catch (e: Exception) {
                throw ApiException(ApiException.Kind.PARSE, "Response is not valid JSON (HTTP $status)", status, cause = e)
            }
        } catch (e: ApiException) {
            throw e
        } catch (e: SocketTimeoutException) {
            throw ApiException(ApiException.Kind.TIMEOUT, "Request timed out", cause = e)
        } catch (e: SSLException) {
            throw ApiException(ApiException.Kind.TLS, "TLS error: ${e.message}", cause = e)
        } catch (e: UnknownHostException) {
            throw ApiException(ApiException.Kind.NETWORK, "Unknown host: ${e.message}", cause = e)
        } catch (e: ConnectException) {
            throw ApiException(ApiException.Kind.NETWORK, "Connection refused or unreachable", cause = e)
        } catch (e: NoRouteToHostException) {
            throw ApiException(ApiException.Kind.NETWORK, "No route to host", cause = e)
        } catch (e: IOException) {
            throw ApiException(ApiException.Kind.NETWORK, "Network error: ${e.message ?: e.javaClass.simpleName}", cause = e)
        } finally {
            cancel?.setAbort(null)
            conn.disconnect()
        }
    }

    private fun setMethod(conn: HttpURLConnection, method: String) {
        try {
            conn.requestMethod = method
        } catch (e: ProtocolException) {
            // Some HttpURLConnection implementations reject PATCH; set the protected field directly.
            val field = HttpURLConnection::class.java.getDeclaredField("method")
            field.isAccessible = true
            field.set(conn, method)
        }
    }

    private fun readLimited(input: InputStream): String {
        val out = ByteArrayOutputStream()
        val buf = ByteArray(16 * 1024)
        var total = 0
        while (true) {
            val n = input.read(buf)
            if (n < 0) break
            total += n
            if (total > MAX_BODY_BYTES) throw ApiException(ApiException.Kind.PARSE, "Response too large")
            out.write(buf, 0, n)
        }
        return out.toString("UTF-8")
    }

    private fun <T> parse(value: Any?, parser: (Any?) -> T): T = try {
        parser(value)
    } catch (e: ApiException) {
        throw e
    } catch (e: Exception) {
        throw ApiException(ApiException.Kind.PARSE, "Unexpected response format: ${e.javaClass.simpleName}", cause = e)
    }

    private fun obj(value: Any?): JSONObject = value as? JSONObject
        ?: throw ApiException(ApiException.Kind.PARSE, "Expected a JSON object")

    private fun arr(value: Any?): JSONArray = value as? JSONArray
        ?: throw ApiException(ApiException.Kind.PARSE, "Expected a JSON array")

    private fun get(path: String, params: List<Pair<String, Any?>> = emptyList(), auth: Boolean = true) =
        request("GET", path, params, auth = auth)

    private fun seg(s: String) = ServerUrl.encodePathSegment(s)

    // --------------------------------------------------------------------------------------------
    // Meta
    // --------------------------------------------------------------------------------------------

    fun health(): HealthOut = parse(get("/health", auth = false)) { HealthOut.parse(obj(it)) }

    fun authCheck(): AuthCheckOut = parse(get("$API/auth/check")) { AuthCheckOut.parse(obj(it)) }

    // --------------------------------------------------------------------------------------------
    // Sources & searches
    // --------------------------------------------------------------------------------------------

    fun sources(): List<SourceOut> = parse(get("$API/sources")) { parseList(arr(it), SourceOut::parse) }

    fun setSourceEnabled(key: String, enabled: Boolean): SourceOut =
        parse(request("PATCH", "$API/sources/${seg(key)}", body = JSONObject().put("enabled", enabled))) { SourceOut.parse(obj(it)) }

    fun testSource(key: String): SourceTestOut =
        parse(request("POST", "$API/sources/${seg(key)}/test", readTimeout = LONG_TIMEOUT_MS)) { SourceTestOut.parse(obj(it)) }

    fun searches(): List<TrackedSearchOut> = parse(get("$API/searches")) { parseList(arr(it), TrackedSearchOut::parse) }

    fun createSearch(input: TrackedSearchIn): TrackedSearchOut =
        parse(request("POST", "$API/searches", body = input.toJson())) { TrackedSearchOut.parse(obj(it)) }

    fun setSearchEnabled(id: Long, enabled: Boolean): TrackedSearchOut =
        parse(request("PATCH", "$API/searches/$id", body = JSONObject().put("enabled", enabled))) { TrackedSearchOut.parse(obj(it)) }

    fun deleteSearch(id: Long): Message = parse(request("DELETE", "$API/searches/$id")) { Message.parse(it as? JSONObject ?: JSONObject()) }

    fun runSearch(id: Long): CollectResult =
        parse(request("POST", "$API/searches/$id/run", readTimeout = LONG_TIMEOUT_MS)) { CollectResult.parse(obj(it)) }

    // --------------------------------------------------------------------------------------------
    // Opportunities
    // --------------------------------------------------------------------------------------------

    fun opportunities(query: OpportunityQuery, limit: Int, offset: Int): Page<OpportunityOut> =
        parse(get("$API/opportunities", query.toParams(limit, offset))) { Page.parse(obj(it), OpportunityOut::parse) }

    fun opportunity(id: Long): OpportunityDetailOut = parse(get("$API/opportunities/$id")) { OpportunityDetailOut.parse(obj(it)) }

    fun setOpportunityStatus(id: Long, status: String): OpportunityOut =
        parse(request("PATCH", "$API/opportunities/$id", body = JSONObject().put("status", status))) { OpportunityOut.parse(obj(it)) }

    fun buyOpportunity(id: Long, input: OpportunityBuyIn): InventoryOut =
        parse(request("POST", "$API/opportunities/$id/buy", body = input.toJson())) { InventoryOut.parse(obj(it)) }

    fun aiSummary(id: Long): AiSummaryOut =
        parse(request("POST", "$API/opportunities/$id/ai-summary", readTimeout = LONG_TIMEOUT_MS)) { AiSummaryOut.parse(obj(it)) }

    fun recompute(): RecomputeResult =
        parse(request("POST", "$API/analyze/recompute", readTimeout = LONG_TIMEOUT_MS)) { RecomputeResult.parse(obj(it)) }

    // --------------------------------------------------------------------------------------------
    // Calculator
    // --------------------------------------------------------------------------------------------

    fun feeProfiles(): List<FeeProfileOut> = parse(get("$API/fee-profiles")) { parseList(arr(it), FeeProfileOut::parse) }

    fun calculateProfit(input: CalculatorIn): CalculatorOut =
        parse(request("POST", "$API/calculator/profit", body = input.toJson())) { CalculatorOut.parse(obj(it)) }

    // --------------------------------------------------------------------------------------------
    // Inventory & sales
    // --------------------------------------------------------------------------------------------

    fun inventory(status: String?, q: String?, limit: Int, offset: Int): Page<InventoryOut> =
        parse(get("$API/inventory", listOf("status" to status, "q" to q, "limit" to limit, "offset" to offset))) {
            Page.parse(obj(it), InventoryOut::parse)
        }

    fun inventoryItem(id: Long): InventoryOut = parse(get("$API/inventory/$id")) { InventoryOut.parse(obj(it)) }

    fun createInventory(input: InventoryIn): InventoryOut =
        parse(request("POST", "$API/inventory", body = input.toJson())) { InventoryOut.parse(obj(it)) }

    fun updateInventory(id: Long, update: InventoryUpdate): InventoryOut =
        parse(request("PATCH", "$API/inventory/$id", body = update.toJson())) { InventoryOut.parse(obj(it)) }

    fun deleteInventory(id: Long): Message = parse(request("DELETE", "$API/inventory/$id")) { Message.parse(it as? JSONObject ?: JSONObject()) }

    fun recordSale(inventoryId: Long, input: SaleIn): SaleOut =
        parse(request("POST", "$API/inventory/$inventoryId/sales", body = input.toJson())) { SaleOut.parse(obj(it)) }

    fun deleteSale(id: Long): Message = parse(request("DELETE", "$API/sales/$id")) { Message.parse(it as? JSONObject ?: JSONObject()) }

    // --------------------------------------------------------------------------------------------
    // Analytics
    // --------------------------------------------------------------------------------------------

    fun analyticsSummary(days: Int): AnalyticsSummaryOut =
        parse(get("$API/analytics/summary", listOf("days" to days))) { AnalyticsSummaryOut.parse(obj(it)) }

    fun timeseries(days: Int, bucket: String): TimeseriesOut =
        parse(get("$API/analytics/timeseries", listOf("days" to days, "bucket" to bucket))) { TimeseriesOut.parse(obj(it)) }

    // --------------------------------------------------------------------------------------------
    // Alerts & notifications
    // --------------------------------------------------------------------------------------------

    fun alerts(): List<AlertRuleOut> = parse(get("$API/alerts")) { parseList(arr(it), AlertRuleOut::parse) }

    fun createAlert(input: AlertRuleIn): AlertRuleOut =
        parse(request("POST", "$API/alerts", body = input.toJson())) { AlertRuleOut.parse(obj(it)) }

    fun setAlertEnabled(id: Long, enabled: Boolean): AlertRuleOut =
        parse(request("PATCH", "$API/alerts/$id", body = JSONObject().put("enabled", enabled))) { AlertRuleOut.parse(obj(it)) }

    fun deleteAlert(id: Long): Message = parse(request("DELETE", "$API/alerts/$id")) { Message.parse(it as? JSONObject ?: JSONObject()) }

    fun notifications(unreadOnly: Boolean, sinceId: Long = 0, limit: Int = 50): List<NotificationOut> =
        parse(get("$API/notifications", listOf("unread_only" to unreadOnly, "since_id" to sinceId, "limit" to limit))) {
            parseList(arr(it), NotificationOut::parse)
        }

    fun markNotificationRead(id: Long): Message =
        parse(request("POST", "$API/notifications/$id/read")) { Message.parse(it as? JSONObject ?: JSONObject()) }

    fun markAllNotificationsRead(): Message =
        parse(request("POST", "$API/notifications/read-all")) { Message.parse(it as? JSONObject ?: JSONObject()) }
}
