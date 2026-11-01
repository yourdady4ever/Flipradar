package com.flipradar.app.ui

import android.content.Context
import com.flipradar.app.R
import com.flipradar.app.data.ApiException
import com.flipradar.app.util.Formats

/** Localized labels for server enum values; unknown values fall back to a humanized key. */
object Labels {
    private fun pick(ctx: Context, key: String?, map: Map<String, Int>): String {
        val id = key?.lowercase()?.let { map[it] }
        return if (id != null) ctx.getString(id) else Formats.humanize(key)
    }

    private val OPP_STATUS = mapOf(
        "new" to R.string.status_new,
        "seen" to R.string.status_seen,
        "watching" to R.string.status_watching,
        "dismissed" to R.string.status_dismissed,
        "bought" to R.string.status_bought,
        "expired" to R.string.status_expired,
    )

    private val RISK = mapOf(
        "none" to R.string.risk_none,
        "info" to R.string.risk_info,
        "warning" to R.string.risk_warning,
        "critical" to R.string.risk_critical,
    )

    private val LEVEL = mapOf(
        "low" to R.string.level_low,
        "medium" to R.string.level_medium,
        "high" to R.string.level_high,
        "unknown" to R.string.level_unknown,
        "insufficient_data" to R.string.level_unknown,
    )

    private val INVENTORY_STATUS = mapOf(
        "purchased" to R.string.inv_status_purchased,
        "in_transit" to R.string.inv_status_in_transit,
        "in_stock" to R.string.inv_status_in_stock,
        "listed" to R.string.inv_status_listed,
        "sold" to R.string.inv_status_sold,
        "returned" to R.string.inv_status_returned,
        "written_off" to R.string.inv_status_written_off,
    )

    private val CONDITION = mapOf(
        "new" to R.string.condition_new,
        "used" to R.string.condition_used,
        "refurbished" to R.string.condition_refurbished,
        "for_parts" to R.string.condition_for_parts,
        "unknown" to R.string.condition_unknown,
    )

    private val SORT = mapOf(
        "score" to R.string.sort_score,
        "profit" to R.string.sort_profit,
        "roi" to R.string.sort_roi,
        "recent" to R.string.sort_recent,
    )

    fun opportunityStatus(ctx: Context, key: String?) = pick(ctx, key, OPP_STATUS)
    fun risk(ctx: Context, key: String?) = pick(ctx, key, RISK)
    fun level(ctx: Context, key: String?) = pick(ctx, key, LEVEL)
    fun inventoryStatus(ctx: Context, key: String?) = pick(ctx, key, INVENTORY_STATUS)
    fun condition(ctx: Context, key: String?) = pick(ctx, key, CONDITION)
    fun sort(ctx: Context, key: String?) = pick(ctx, key, SORT)

    /** Localized, user-facing description of an error (never includes the API token). */
    fun error(ctx: Context, t: Throwable): String {
        if (t !is ApiException) return ctx.getString(R.string.err_unexpected, t.message ?: t.javaClass.simpleName)
        val detail = t.detail ?: ""
        return when (t.kind) {
            ApiException.Kind.NOT_CONFIGURED -> ctx.getString(R.string.err_not_configured)
            ApiException.Kind.NETWORK -> ctx.getString(R.string.err_network, t.message ?: "")
            ApiException.Kind.TIMEOUT -> ctx.getString(R.string.err_timeout)
            ApiException.Kind.TLS -> ctx.getString(R.string.err_tls, t.message ?: "")
            ApiException.Kind.UNAUTHORIZED -> ctx.getString(R.string.err_unauthorized, t.httpStatus ?: 401, detail)
            ApiException.Kind.NOT_FOUND -> ctx.getString(R.string.err_not_found, detail)
            ApiException.Kind.REDIRECT -> ctx.getString(R.string.err_redirect, detail)
            ApiException.Kind.PARSE -> ctx.getString(R.string.err_parse, t.message ?: "")
            ApiException.Kind.HTTP -> ctx.getString(R.string.err_http, t.httpStatus ?: 0, detail)
        }
    }
}
