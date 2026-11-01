package com.flipradar.app.data

import com.flipradar.app.util.Formats
import org.json.JSONArray
import org.json.JSONObject

/*
 * Kotlin mirrors of the Pydantic models in backend/flipradar/schemas.py (the HTTP contract, API v1).
 *
 * Conventions: money = Long centavos (`*Cents`), percentages = Long basis points (`*Bp`), datetimes are
 * kept as the server's ISO-8601 strings and only formatted for display (util/Formats.kt).
 * Parsers are lenient: missing / null fields become null or neutral defaults, never exceptions.
 * When schemas.py changes, update the matching parser here and ApiModelsTest.
 */

const val SUPPORTED_API_VERSION = "1"

// ------------------------------------------------------------------------------------------------
// Generic
// ------------------------------------------------------------------------------------------------

data class Page<T>(val items: List<T>, val total: Long, val limit: Long, val offset: Long) {
    val hasMore: Boolean get() = offset + items.size < total

    companion object {
        fun <T> parse(o: JSONObject, item: (JSONObject) -> T): Page<T> {
            val items = o.arr("items").mapObjects(item)
            return Page(items, o.long("total", items.size.toLong()), o.long("limit", items.size.toLong()), o.long("offset"))
        }
    }
}

data class Message(val ok: Boolean, val message: String) {
    companion object {
        fun parse(o: JSONObject) = Message(o.bool("ok", true), o.str("message"))
    }
}

/** Extracts a human-readable error from a FastAPI error body (`{"detail": "..."}` or a 422 list). */
object ApiErrorParser {
    fun detail(body: String?): String? {
        if (body.isNullOrBlank()) return null
        val parsed = try {
            org.json.JSONTokener(body).nextValue()
        } catch (e: Exception) {
            return null
        }
        if (parsed !is JSONObject) return null
        if (parsed.isNull("detail")) return null
        return when (val d = parsed.opt("detail")) {
            is String -> d.ifBlank { null }
            is JSONArray -> {
                val parts = ArrayList<String>()
                for (i in 0 until d.length()) {
                    val e = d.optJSONObject(i)
                    if (e == null) {
                        if (!d.isNull(i)) parts.add(d.opt(i).toString())
                        continue
                    }
                    // FastAPI prefixes loc with the request part ("body", "query", ...); drop only that prefix
                    // so a field that happens to be named "query" is still reported.
                    val loc = e.arr("loc").strings().let { l ->
                        if (l.isNotEmpty() && l[0] in setOf("body", "query", "path", "header", "cookie")) l.drop(1) else l
                    }
                    val msg = e.str("msg", e.str("message", e.toString()))
                    parts.add(if (loc.isEmpty()) msg else loc.joinToString(".") + ": " + msg)
                }
                parts.joinToString("; ").ifBlank { null }
            }
            is JSONObject -> d.strOrNull("message") ?: d.toString()
            else -> d?.toString()
        }
    }
}

// ------------------------------------------------------------------------------------------------
// Meta / health / auth
// ------------------------------------------------------------------------------------------------

data class HealthOut(val status: String, val version: String, val apiVersion: String, val database: String, val time: String?) {
    companion object {
        fun parse(o: JSONObject) = HealthOut(
            o.str("status", "unknown"), o.str("version"), o.str("api_version", SUPPORTED_API_VERSION), o.str("database"), o.strOrNull("time"),
        )
    }
}

data class AuthCheckOut(val ok: Boolean, val tokenName: String?, val authRequired: Boolean) {
    companion object {
        fun parse(o: JSONObject) = AuthCheckOut(o.bool("ok"), o.strOrNull("token_name"), o.bool("auth_required", true))
    }
}

// ------------------------------------------------------------------------------------------------
// Sources, searches, runs
// ------------------------------------------------------------------------------------------------

data class SourceOut(
    val key: String,
    val name: String,
    val kind: String,
    val country: String,
    val currency: String,
    val enabled: Boolean,
    val defaultFeeProfileKey: String?,
    val configured: Boolean,
    val requiresCredentials: Boolean,
    val capabilities: List<String>,
    val docsUrl: String?,
    val notes: String,
    val lastRunAt: String?,
    val lastStatus: String?,
    val lastError: String?,
) {
    val canSearch: Boolean get() = capabilities.contains("search")

    companion object {
        fun parse(o: JSONObject) = SourceOut(
            key = o.str("key"),
            name = o.str("name", o.str("key")),
            kind = o.str("kind"),
            country = o.str("country"),
            currency = o.str("currency", "MXN"),
            enabled = o.bool("enabled"),
            defaultFeeProfileKey = o.strOrNull("default_fee_profile_key"),
            configured = o.bool("configured"),
            requiresCredentials = o.bool("requires_credentials"),
            capabilities = o.arr("capabilities").strings(),
            docsUrl = o.strOrNull("docs_url"),
            notes = o.str("notes"),
            lastRunAt = o.strOrNull("last_run_at"),
            lastStatus = o.strOrNull("last_status"),
            lastError = o.strOrNull("last_error"),
        )
    }
}

data class SourceTestOut(val key: String, val configured: Boolean, val ok: Boolean?, val message: String, val details: Map<String, Any?>) {
    companion object {
        fun parse(o: JSONObject) = SourceTestOut(o.str("key"), o.bool("configured"), o.boolOrNull("ok"), o.str("message"), o.mapOrEmpty("details"))
    }
}

data class TrackedSearchIn(
    val query: String,
    val categoryHint: String? = null,
    val sourceKeys: List<String> = emptyList(),
    val maxResults: Int = 50,
    val intervalMinutes: Int = 180,
    val enabled: Boolean = true,
) {
    fun toJson(): JSONObject = JSONObject()
        .put("query", query)
        .putIfNotNull("category_hint", categoryHint?.ifBlank { null })
        .put("source_keys", JSONArray(sourceKeys))
        .put("max_results", maxResults)
        .put("interval_minutes", intervalMinutes)
        .put("enabled", enabled)
}

data class TrackedSearchOut(
    val id: Long,
    val query: String,
    val categoryHint: String?,
    val sourceKeys: List<String>,
    val maxResults: Int,
    val intervalMinutes: Int,
    val enabled: Boolean,
    val lastRunAt: String?,
    val createdAt: String?,
) {
    companion object {
        fun parse(o: JSONObject) = TrackedSearchOut(
            o.long("id"), o.str("query"), o.strOrNull("category_hint"), o.arr("source_keys").strings(),
            o.int("max_results", 50), o.int("interval_minutes", 180), o.bool("enabled", true),
            o.strOrNull("last_run_at"), o.strOrNull("created_at"),
        )
    }
}

data class CollectionRunOut(
    val id: Long,
    val sourceKey: String?,
    val query: String?,
    val startedAt: String?,
    val finishedAt: String?,
    val status: String,
    val itemsFetched: Long,
    val itemsNew: Long,
    val itemsUpdated: Long,
    val error: String?,
) {
    companion object {
        fun parse(o: JSONObject) = CollectionRunOut(
            o.long("id"), o.strOrNull("source_key"), o.strOrNull("query"), o.strOrNull("started_at"), o.strOrNull("finished_at"),
            o.str("status", "unknown"), o.long("items_fetched"), o.long("items_new"), o.long("items_updated"), o.strOrNull("error"),
        )
    }
}

data class CollectResult(
    val runs: List<CollectionRunOut>,
    val opportunitiesCreated: Long,
    val opportunitiesUpdated: Long,
    val notificationsCreated: Long,
) {
    companion object {
        fun parse(o: JSONObject) = CollectResult(
            o.arr("runs").mapObjects(CollectionRunOut::parse), o.long("opportunities_created"),
            o.long("opportunities_updated"), o.long("notifications_created"),
        )
    }
}

// ------------------------------------------------------------------------------------------------
// Listings
// ------------------------------------------------------------------------------------------------

data class RiskFlagOut(val code: String, val severity: String, val message: String, val evidence: Map<String, Any?>) {
    companion object {
        fun parse(o: JSONObject) = RiskFlagOut(o.str("code"), o.str("severity", "info"), o.str("message"), o.mapOrEmpty("evidence"))
    }
}

data class ListingOut(
    val id: Long,
    val sourceKey: String,
    val externalId: String,
    val url: String?,
    val title: String,
    val currency: String,
    val priceCents: Long,
    val priceMxnCents: Long?,
    val shippingCents: Long?,
    val freeShipping: Boolean?,
    val condition: String,
    val brand: String?,
    val model: String?,
    val category: String?,
    val gtin: String?,
    val sellerName: String?,
    val location: String?,
    val imageUrl: String?,
    val availableQuantity: Long?,
    val soldQuantity: Long?,
    val status: String,
    val productId: Long?,
    val matchConfidence: Double?,
    val matchMethod: String?,
    val isAccessory: Boolean,
    val isBundle: Boolean,
    val isSuspicious: Boolean,
    val riskFlags: List<RiskFlagOut>,
    val isDemo: Boolean,
    val firstSeenAt: String?,
    val lastSeenAt: String?,
) {
    companion object {
        fun parse(o: JSONObject) = ListingOut(
            id = o.long("id"),
            sourceKey = o.str("source_key"),
            externalId = o.str("external_id"),
            url = o.strOrNull("url"),
            title = o.str("title"),
            currency = o.str("currency", "MXN"),
            priceCents = o.long("price_cents"),
            priceMxnCents = o.longOrNull("price_mxn_cents"),
            shippingCents = o.longOrNull("shipping_cents"),
            freeShipping = o.boolOrNull("free_shipping"),
            condition = o.str("condition", "unknown"),
            brand = o.strOrNull("brand"),
            model = o.strOrNull("model"),
            category = o.strOrNull("category"),
            gtin = o.strOrNull("gtin"),
            sellerName = o.strOrNull("seller_name"),
            location = o.strOrNull("location"),
            imageUrl = o.strOrNull("image_url"),
            availableQuantity = o.longOrNull("available_quantity"),
            soldQuantity = o.longOrNull("sold_quantity"),
            status = o.str("status"),
            productId = o.longOrNull("product_id"),
            matchConfidence = o.doubleOrNull("match_confidence"),
            matchMethod = o.strOrNull("match_method"),
            isAccessory = o.bool("is_accessory"),
            isBundle = o.bool("is_bundle"),
            isSuspicious = o.bool("is_suspicious"),
            riskFlags = o.arr("risk_flags").mapObjects(RiskFlagOut::parse),
            isDemo = o.bool("is_demo"),
            firstSeenAt = o.strOrNull("first_seen_at"),
            lastSeenAt = o.strOrNull("last_seen_at"),
        )
    }
}

data class PriceStatsOut(
    val n: Long,
    val medianCents: Long?,
    val p25Cents: Long?,
    val p75Cents: Long?,
    val minCents: Long?,
    val maxCents: Long?,
    val madCents: Long?,
    val iqrCents: Long?,
    val dispersion: Double?,
    val sourceCount: Long,
    val sellerCount: Long,
    val excluded: Map<String, Long>,
    val comparableIds: List<Long>,
    val windowDays: Long?,
) {
    companion object {
        val EMPTY = PriceStatsOut(0, null, null, null, null, null, null, null, null, 0, 0, emptyMap(), emptyList(), null)

        fun parse(o: JSONObject?) = if (o == null) EMPTY else PriceStatsOut(
            o.long("n"), o.longOrNull("median_cents"), o.longOrNull("p25_cents"), o.longOrNull("p75_cents"),
            o.longOrNull("min_cents"), o.longOrNull("max_cents"), o.longOrNull("mad_cents"), o.longOrNull("iqr_cents"),
            o.doubleOrNull("dispersion"), o.long("source_count"), o.long("seller_count"), o.longMap("excluded"),
            o.arr("comparable_ids").longs(), o.longOrNull("window_days"),
        )
    }
}

// ------------------------------------------------------------------------------------------------
// Opportunities
// ------------------------------------------------------------------------------------------------

data class CostLineOut(val key: String, val label: String, val amountCents: Long, val formula: String) {
    companion object {
        fun parse(o: JSONObject) = CostLineOut(o.str("key"), o.str("label", o.str("key")), o.long("amount_cents"), o.str("formula"))
    }
}

data class ProfitResultOut(
    val salePriceCents: Long,
    val acquisitionLines: List<CostLineOut>,
    val sellingLines: List<CostLineOut>,
    val totalAcquisitionCents: Long,
    val totalSellingCostsCents: Long,
    val netProceedsCents: Long,
    val netProfitCents: Long,
    val roiBp: Long?,
    val marginBp: Long?,
    val feeProfileKey: String,
    val assumptions: List<String>,
) {
    companion object {
        val EMPTY = ProfitResultOut(0, emptyList(), emptyList(), 0, 0, 0, 0, null, null, "", emptyList())

        fun parse(o: JSONObject?) = if (o == null) EMPTY else ProfitResultOut(
            salePriceCents = o.long("sale_price_cents"),
            acquisitionLines = o.arr("acquisition_lines").mapObjects(CostLineOut::parse),
            sellingLines = o.arr("selling_lines").mapObjects(CostLineOut::parse),
            totalAcquisitionCents = o.long("total_acquisition_cents"),
            totalSellingCostsCents = o.long("total_selling_costs_cents"),
            netProceedsCents = o.long("net_proceeds_cents"),
            netProfitCents = o.long("net_profit_cents"),
            roiBp = o.longOrNull("roi_bp"),
            marginBp = o.longOrNull("margin_bp"),
            feeProfileKey = o.str("fee_profile_key"),
            assumptions = o.arr("assumptions").strings(),
        )
    }
}

data class ScoreComponentOut(val key: String, val label: String, val rawValue: Double?, val points: Double, val maxPoints: Double, val explanation: String) {
    companion object {
        fun parse(o: JSONObject) = ScoreComponentOut(
            o.str("key"), o.str("label", o.str("key")), o.doubleOrNull("raw_value"), o.double("points"), o.double("max_points"), o.str("explanation"),
        )
    }
}

data class DemandOut(val level: String, val confidence: String?, val signals: List<Map<String, Any?>>, val note: String) {
    companion object {
        val UNKNOWN = DemandOut("unknown", null, emptyList(), "")

        fun parse(o: JSONObject?) = if (o == null) UNKNOWN else DemandOut(o.str("level", "unknown"), o.strOrNull("confidence"), o.listOfMaps("signals"), o.str("note"))
    }
}

data class OpportunityOut(
    val id: Long,
    val productId: Long,
    val productTitle: String,
    val productKey: String,
    val category: String?,
    val buyListingId: Long,
    val buyTitle: String,
    val buyUrl: String?,
    val buySourceKey: String,
    val buyCondition: String,
    val imageUrl: String?,
    val feeProfileKey: String,
    val buyTotalCents: Long,
    val expectedSaleCents: Long,
    val netProfitCents: Long,
    val roiBp: Long,
    val marginBp: Long,
    val score: Double,
    val confidence: String,
    val comparableCount: Long,
    val riskLevel: String,
    val demandLevel: String,
    val status: String,
    val isDemo: Boolean,
    val createdAt: String?,
    val updatedAt: String?,
) {
    companion object {
        val STATUSES = listOf("new", "seen", "watching", "dismissed", "bought", "expired")
        val SORTS = listOf("score", "profit", "roi", "recent")

        /** Status filter value for every status.  Sending no status gets the server's default open set. */
        const val STATUS_ALL = "all"

        /**
         * Status filter choices for the opportunity list: null (no parameter: the server's default open
         * statuses new/seen/watching/bought), [STATUS_ALL] (also dismissed and expired), then each status.
         */
        val STATUS_FILTERS: List<String?> = listOf(null, STATUS_ALL) + STATUSES

        /** Status the "Restore to new" action sends, to undo a "Mark bought" made by mistake. */
        const val STATUS_RESTORE = "new"

        /**
         * Target statuses the detail screen offers.  Expired gets none (a later recompute reopens it).  Bought gets
         * only [STATUS_RESTORE]: the server refuses it (409) while an inventory item references the opportunity,
         * so a real purchase never counts as open potential profit again.  Unknown statuses get none.
         */
        fun statusActions(status: String): List<String> = when (status) {
            "new", "seen", "watching", "dismissed" -> listOf("watching", "dismissed")
            "bought" -> listOf(STATUS_RESTORE)
            else -> emptyList()
        }

        /** True when the server refused to reopen a bought opportunity: an inventory item still references it. */
        fun isRestoreRefused(fromStatus: String, error: Throwable): Boolean =
            fromStatus == "bought" && error is ApiException && error.httpStatus == 409

        fun parse(o: JSONObject) = OpportunityOut(
            id = o.long("id"),
            productId = o.long("product_id"),
            productTitle = o.str("product_title"),
            productKey = o.str("product_key"),
            category = o.strOrNull("category"),
            buyListingId = o.long("buy_listing_id"),
            buyTitle = o.str("buy_title", o.str("product_title")),
            buyUrl = o.strOrNull("buy_url"),
            buySourceKey = o.str("buy_source_key"),
            buyCondition = o.str("buy_condition", "unknown"),
            imageUrl = o.strOrNull("image_url"),
            feeProfileKey = o.str("fee_profile_key"),
            buyTotalCents = o.long("buy_total_cents"),
            expectedSaleCents = o.long("expected_sale_cents"),
            netProfitCents = o.long("net_profit_cents"),
            roiBp = o.long("roi_bp"),
            marginBp = o.long("margin_bp"),
            score = o.double("score"),
            confidence = o.str("confidence", "low"),
            comparableCount = o.long("comparable_count"),
            riskLevel = o.str("risk_level", "none"),
            demandLevel = o.str("demand_level", "unknown"),
            status = o.str("status", "new"),
            isDemo = o.bool("is_demo"),
            createdAt = o.strOrNull("created_at"),
            updatedAt = o.strOrNull("updated_at"),
        )
    }
}

data class OpportunityDetailOut(
    val summary: OpportunityOut,
    val priceStats: PriceStatsOut,
    val costBreakdown: ProfitResultOut,
    val scoreComponents: List<ScoreComponentOut>,
    val riskFlags: List<RiskFlagOut>,
    val demand: DemandOut,
    val explanation: String,
    val facts: Map<String, Any?>,
    val aiSummary: String?,
    val comparables: List<ListingOut>,
) {
    companion object {
        fun parse(o: JSONObject) = OpportunityDetailOut(
            summary = OpportunityOut.parse(o),
            priceStats = PriceStatsOut.parse(o.objOrNull("price_stats")),
            costBreakdown = ProfitResultOut.parse(o.objOrNull("cost_breakdown")),
            scoreComponents = o.arr("score_components").mapObjects(ScoreComponentOut::parse),
            riskFlags = o.arr("risk_flags").mapObjects(RiskFlagOut::parse),
            demand = DemandOut.parse(o.objOrNull("demand")),
            explanation = o.str("explanation"),
            facts = o.mapOrEmpty("facts"),
            aiSummary = o.strOrNull("ai_summary"),
            comparables = o.arr("comparables").mapObjects(ListingOut::parse),
        )
    }
}

/** Query parameters for GET /api/v1/opportunities. Null = let the server use its default. */
data class OpportunityQuery(
    val status: String? = null,
    val q: String? = null,
    val minScore: Long? = null,
    val minProfitCents: Long? = null,
    val minRoiBp: Long? = null,
    val includeSuspicious: Boolean = false,
    val sort: String = "score",
) {
    fun toParams(limit: Int, offset: Int): List<Pair<String, Any?>> = listOf(
        "status" to status,
        "q" to q?.trim(),
        "min_score" to minScore,
        "min_profit_cents" to minProfitCents,
        "min_roi_bp" to minRoiBp,
        "include_suspicious" to includeSuspicious,
        "sort" to sort,
        "limit" to limit,
        "offset" to offset,
    )
}

data class OpportunityBuyIn(val quantity: Int = 1, val unitCostCents: Long? = null, val extraCostsCents: Long? = null, val notes: String? = null) {
    fun toJson(): JSONObject = JSONObject()
        .put("quantity", quantity)
        .putIfNotNull("unit_cost_cents", unitCostCents)
        .putIfNotNull("extra_costs_cents", extraCostsCents)
        .putIfNotNull("notes", notes?.ifBlank { null })
}

data class AiSummaryOut(val opportunityId: Long, val provider: String, val summary: String?, val validated: Boolean, val message: String) {
    companion object {
        fun parse(o: JSONObject) = AiSummaryOut(o.long("opportunity_id"), o.str("provider", "none"), o.strOrNull("summary"), o.bool("validated"), o.str("message"))
    }
}

data class RecomputeResult(
    val productsAnalyzed: Long,
    val opportunitiesCreated: Long,
    val opportunitiesUpdated: Long,
    val opportunitiesExpired: Long,
    val notificationsCreated: Long,
) {
    companion object {
        fun parse(o: JSONObject) = RecomputeResult(
            o.long("products_analyzed"), o.long("opportunities_created"), o.long("opportunities_updated"),
            o.long("opportunities_expired"), o.long("notifications_created"),
        )
    }
}

// ------------------------------------------------------------------------------------------------
// Calculator & fee profiles
// ------------------------------------------------------------------------------------------------

data class FixedFeeTier(val maxPriceCents: Long?, val feeCents: Long) {
    companion object {
        fun parse(o: JSONObject) = FixedFeeTier(o.longOrNull("max_price_cents"), o.long("fee_cents"))
    }
}

data class FeeProfileOut(
    val key: String,
    val name: String,
    val channel: String,
    val commissionBp: Long,
    val fixedFeeTiers: List<FixedFeeTier>,
    val paymentFeeBp: Long,
    val paymentFixedCents: Long,
    val ivaOnFees: Boolean,
    val ivaRateBp: Long,
    val sellerShippingCents: Long,
    val freeShippingThresholdCents: Long?,
    val isrWithholdingBp: Long,
    val ivaWithholdingBp: Long,
    val sourceUrl: String?,
    val asOf: String?,
    val notes: String?,
    val isBuiltin: Boolean,
    val updatedAt: String?,
) {
    companion object {
        fun parse(o: JSONObject) = FeeProfileOut(
            key = o.str("key"),
            name = o.str("name", o.str("key")),
            channel = o.str("channel", "custom"),
            commissionBp = o.long("commission_bp"),
            fixedFeeTiers = o.arr("fixed_fee_tiers").mapObjects(FixedFeeTier::parse),
            paymentFeeBp = o.long("payment_fee_bp"),
            paymentFixedCents = o.long("payment_fixed_cents"),
            ivaOnFees = o.bool("iva_on_fees"),
            ivaRateBp = o.long("iva_rate_bp", 1600),
            sellerShippingCents = o.long("seller_shipping_cents"),
            freeShippingThresholdCents = o.longOrNull("free_shipping_threshold_cents"),
            isrWithholdingBp = o.long("isr_withholding_bp"),
            ivaWithholdingBp = o.long("iva_withholding_bp"),
            sourceUrl = o.strOrNull("source_url"),
            asOf = o.strOrNull("as_of"),
            notes = o.strOrNull("notes"),
            isBuiltin = o.bool("is_builtin"),
            updatedAt = o.strOrNull("updated_at"),
        )
    }
}

data class CalculatorIn(
    val buyPriceCents: Long,
    val salePriceCents: Long,
    val feeProfileKey: String? = null,
    val buyShippingCents: Long = 0,
    val importCostsCents: Long = 0,
    val otherBuyCostsCents: Long = 0,
    val otherSaleCostsCents: Long = 0,
    val sellerShippingOverrideCents: Long? = null,
    val salePriceIncludesIva: Boolean = true,
    val treatWithholdingsAsCost: Boolean = true,
    val targetRoiBp: Long? = null,
) {
    fun toJson(): JSONObject = JSONObject()
        .put("buy_price_cents", buyPriceCents)
        .put("sale_price_cents", salePriceCents)
        .putIfNotNull("fee_profile_key", feeProfileKey)
        .put("buy_shipping_cents", buyShippingCents)
        .put("import_costs_cents", importCostsCents)
        .put("other_buy_costs_cents", otherBuyCostsCents)
        .put("other_sale_costs_cents", otherSaleCostsCents)
        .putIfNotNull("seller_shipping_override_cents", sellerShippingOverrideCents)
        .put("sale_price_includes_iva", salePriceIncludesIva)
        .put("treat_withholdings_as_cost", treatWithholdingsAsCost)
        .putIfNotNull("target_roi_bp", targetRoiBp)
}

data class CalculatorOut(val result: ProfitResultOut, val breakEvenSaleCents: Long?, val maxBuyForTargetCents: Long?, val targetRoiBp: Long?) {
    companion object {
        fun parse(o: JSONObject) = CalculatorOut(
            ProfitResultOut.parse(o.objOrNull("result")), o.longOrNull("break_even_sale_cents"),
            o.longOrNull("max_buy_for_target_cents"), o.longOrNull("target_roi_bp"),
        )
    }
}

// ------------------------------------------------------------------------------------------------
// Inventory & sales
// ------------------------------------------------------------------------------------------------

object InventoryStatus {
    /** Values accepted by InventoryIn (creation). */
    val CREATE = listOf("purchased", "in_transit", "in_stock", "listed")
    /** Values accepted by InventoryUpdate. */
    val UPDATE = listOf("purchased", "in_transit", "in_stock", "listed", "returned", "written_off")
    /** Values used for list filtering (server may also report "sold"). */
    val FILTER = listOf("purchased", "in_transit", "in_stock", "listed", "sold", "returned", "written_off")
    val CONDITIONS = listOf("new", "used", "refurbished", "for_parts", "unknown")

    /**
     * Status choices when editing an item that is in [current]: the [UPDATE] values, with [current] first
     * when it is not one of them (e.g. "sold", which only the server sets), so the form shows the real
     * status instead of silently preselecting another one.
     */
    fun editChoices(current: String): List<String> = if (current in UPDATE) UPDATE else listOf(current) + UPDATE
}

data class InventoryIn(
    val title: String,
    val quantity: Int = 1,
    val unitCostCents: Long,
    val extraCostsCents: Long = 0,
    val condition: String = "unknown",
    val sku: String? = null,
    val purchaseSource: String? = null,
    val purchaseUrl: String? = null,
    val status: String = "purchased",
    val targetSaleCents: Long? = null,
    val listedChannel: String? = null,
    val location: String? = null,
    val notes: String? = null,
) {
    fun toJson(): JSONObject = JSONObject()
        .put("title", title)
        .put("quantity", quantity)
        .put("unit_cost_cents", unitCostCents)
        .put("extra_costs_cents", extraCostsCents)
        .put("condition", condition)
        .putIfNotNull("sku", sku?.ifBlank { null })
        .putIfNotNull("purchase_source", purchaseSource?.ifBlank { null })
        .putIfNotNull("purchase_url", purchaseUrl?.ifBlank { null })
        .put("status", status)
        .putIfNotNull("target_sale_cents", targetSaleCents)
        .putIfNotNull("listed_channel", listedChannel?.ifBlank { null })
        .putIfNotNull("location", location?.ifBlank { null })
        .putIfNotNull("notes", notes?.ifBlank { null })
}

/**
 * Partial update: only non-null fields are sent, plus the JSON keys named in [clear], which are sent as
 * explicit nulls so the server clears them (a missing key leaves the stored value unchanged).
 */
data class InventoryUpdate(
    val status: String? = null,
    val targetSaleCents: Long? = null,
    val listedChannel: String? = null,
    val location: String? = null,
    val notes: String? = null,
    val clear: Set<String> = emptySet(),
) {
    fun toJson(): JSONObject {
        val o = JSONObject()
            .putIfNotNull("status", status)
            .putIfNotNull(TARGET_SALE, targetSaleCents)
            .putIfNotNull(LISTED_CHANNEL, listedChannel)
            .putIfNotNull("location", location)
            .putIfNotNull("notes", notes)
        for (key in clear) if (!o.has(key)) o.put(key, JSONObject.NULL)
        return o
    }

    /** True when there is nothing to send. */
    val isEmpty: Boolean get() = toJson().length() == 0

    companion object {
        const val TARGET_SALE = "target_sale_cents"
        const val LISTED_CHANNEL = "listed_channel"

        /**
         * The PATCH for the "Change status" form of [item], containing only what the user changed.  The
         * status is sent only when another one was picked: an item the server marked "sold" keeps it
         * (re-sending an open status for it is rejected with 409).  A target price or channel the user
         * emptied is sent as null so the server clears it instead of keeping the old value.
         */
        fun fromStatusForm(item: InventoryOut, status: String?, targetSaleCents: Long?, listedChannel: String?): InventoryUpdate {
            val channel = listedChannel?.trim()?.ifEmpty { null }
            val clear = LinkedHashSet<String>()
            if (targetSaleCents == null && item.targetSaleCents != null) clear += TARGET_SALE
            if (channel == null && item.listedChannel != null) clear += LISTED_CHANNEL
            return InventoryUpdate(
                status = status?.takeIf { it != item.status },
                targetSaleCents = targetSaleCents?.takeIf { it != item.targetSaleCents },
                listedChannel = channel?.takeIf { it != item.listedChannel },
                clear = clear,
            )
        }
    }
}

data class SaleIn(
    val quantity: Int = 1,
    val unitPriceCents: Long,
    val channel: String? = null,
    val feeProfileKey: String? = null,
    val feesCents: Long? = null,
    val shippingCents: Long = 0,
    val taxesWithheldCents: Long? = null,
    val otherCostsCents: Long = 0,
    val notes: String? = null,
) {
    fun toJson(): JSONObject = JSONObject()
        .put("quantity", quantity)
        .put("unit_price_cents", unitPriceCents)
        .putIfNotNull("channel", channel?.ifBlank { null })
        .putIfNotNull("fee_profile_key", feeProfileKey)
        .putIfNotNull("fees_cents", feesCents)
        .put("shipping_cents", shippingCents)
        .putIfNotNull("taxes_withheld_cents", taxesWithheldCents)
        .put("other_costs_cents", otherCostsCents)
        .putIfNotNull("notes", notes?.ifBlank { null })
}

data class SaleOut(
    val id: Long,
    val inventoryItemId: Long,
    val quantity: Long,
    val unitPriceCents: Long,
    val channel: String?,
    val feeProfileKey: String?,
    val feesCents: Long,
    val shippingCents: Long,
    val taxesWithheldCents: Long,
    val otherCostsCents: Long,
    val costBasisCents: Long,
    val netProceedsCents: Long,
    val profitCents: Long,
    val soldAt: String?,
    val notes: String?,
) {
    companion object {
        fun parse(o: JSONObject) = SaleOut(
            o.long("id"), o.long("inventory_item_id"), o.long("quantity", 1), o.long("unit_price_cents"), o.strOrNull("channel"),
            o.strOrNull("fee_profile_key"), o.long("fees_cents"), o.long("shipping_cents"), o.long("taxes_withheld_cents"),
            o.long("other_costs_cents"), o.long("cost_basis_cents"), o.long("net_proceeds_cents"), o.long("profit_cents"),
            o.strOrNull("sold_at"), o.strOrNull("notes"),
        )
    }
}

data class InventoryOut(
    val id: Long,
    val title: String,
    val sku: String?,
    val productId: Long?,
    val opportunityId: Long?,
    val condition: String,
    val quantity: Long,
    val quantityAvailable: Long,
    val unitCostCents: Long,
    val extraCostsCents: Long,
    val totalCostCents: Long,
    val purchaseSource: String?,
    val purchaseUrl: String?,
    val purchasedAt: String?,
    val status: String,
    val targetSaleCents: Long?,
    val listedChannel: String?,
    val location: String?,
    val notes: String?,
    val daysHeld: Long,
    val realizedProfitCents: Long,
    val sales: List<SaleOut>,
    val createdAt: String?,
    val updatedAt: String?,
) {
    companion object {
        fun parse(o: JSONObject) = InventoryOut(
            id = o.long("id"),
            title = o.str("title"),
            sku = o.strOrNull("sku"),
            productId = o.longOrNull("product_id"),
            opportunityId = o.longOrNull("opportunity_id"),
            condition = o.str("condition", "unknown"),
            quantity = o.long("quantity", 1),
            quantityAvailable = o.long("quantity_available"),
            unitCostCents = o.long("unit_cost_cents"),
            extraCostsCents = o.long("extra_costs_cents"),
            totalCostCents = o.long("total_cost_cents"),
            purchaseSource = o.strOrNull("purchase_source"),
            purchaseUrl = o.strOrNull("purchase_url"),
            purchasedAt = o.strOrNull("purchased_at"),
            status = o.str("status", "purchased"),
            targetSaleCents = o.longOrNull("target_sale_cents"),
            listedChannel = o.strOrNull("listed_channel"),
            location = o.strOrNull("location"),
            notes = o.strOrNull("notes"),
            daysHeld = o.long("days_held"),
            realizedProfitCents = o.long("realized_profit_cents"),
            sales = o.arr("sales").mapObjects(SaleOut::parse),
            createdAt = o.strOrNull("created_at"),
            updatedAt = o.strOrNull("updated_at"),
        )
    }
}

// ------------------------------------------------------------------------------------------------
// Analytics
// ------------------------------------------------------------------------------------------------

data class AnalyticsSummaryOut(
    val periodDays: Long,
    val revenueCents: Long,
    val realizedProfitCents: Long,
    val unitsSold: Long,
    val salesCount: Long,
    val avgRoiBp: Long?,
    val avgDaysToSell: Double?,
    val inventoryUnits: Long,
    val inventoryCostCents: Long,
    val inventoryTargetValueCents: Long?,
    val aging: Map<String, Long>,
    val openOpportunities: Long,
    val avgOpenOpportunityScore: Double?,
    val potentialProfitOpenCents: Long,
    val opportunitiesBySource: Map<String, Long>,
    val topProductsByProfit: List<Map<String, Any?>>,
    val listingsTracked: Long,
    val productsTracked: Long,
    val lastCollectionAt: String?,
) {
    /** Aging buckets in their natural order, followed by any extra buckets the server adds. */
    fun orderedAging(): List<Pair<String, Long>> {
        val order = listOf("0-7", "8-30", "31-90", "90+")
        val known = order.mapNotNull { k -> aging[k]?.let { k to it } }
        val extra = aging.filterKeys { it !in order }.toList()
        return known + extra
    }

    companion object {
        fun parse(o: JSONObject) = AnalyticsSummaryOut(
            periodDays = o.long("period_days", 30),
            revenueCents = o.long("revenue_cents"),
            realizedProfitCents = o.long("realized_profit_cents"),
            unitsSold = o.long("units_sold"),
            salesCount = o.long("sales_count"),
            avgRoiBp = o.longOrNull("avg_roi_bp"),
            avgDaysToSell = o.doubleOrNull("avg_days_to_sell"),
            inventoryUnits = o.long("inventory_units"),
            inventoryCostCents = o.long("inventory_cost_cents"),
            inventoryTargetValueCents = o.longOrNull("inventory_target_value_cents"),
            aging = o.longMap("aging"),
            openOpportunities = o.long("open_opportunities"),
            avgOpenOpportunityScore = o.doubleOrNull("avg_open_opportunity_score"),
            potentialProfitOpenCents = o.long("potential_profit_open_cents"),
            opportunitiesBySource = o.longMap("opportunities_by_source"),
            topProductsByProfit = o.listOfMaps("top_products_by_profit"),
            listingsTracked = o.long("listings_tracked"),
            productsTracked = o.long("products_tracked"),
            lastCollectionAt = o.strOrNull("last_collection_at"),
        )
    }
}

data class TimeseriesPoint(val period: String, val revenueCents: Long, val profitCents: Long, val units: Long, val purchasesCents: Long) {
    companion object {
        fun parse(o: JSONObject) = TimeseriesPoint(o.str("period"), o.long("revenue_cents"), o.long("profit_cents"), o.long("units"), o.long("purchases_cents"))
    }
}

data class TimeseriesOut(val bucket: String, val points: List<TimeseriesPoint>) {
    companion object {
        fun parse(o: JSONObject) = TimeseriesOut(o.str("bucket", "week"), o.arr("points").mapObjects(TimeseriesPoint::parse))
    }
}

// ------------------------------------------------------------------------------------------------
// Alerts & notifications
// ------------------------------------------------------------------------------------------------

data class AlertRuleIn(
    val name: String,
    val keywords: String? = null,
    val brand: String? = null,
    val category: String? = null,
    val minProfitCents: Long? = null,
    val minRoiBp: Long? = null,
    val minScore: Double? = null,
    val maxBuyCents: Long? = null,
    val minConfidence: String? = null,
    val excludeSuspicious: Boolean = true,
    val channels: List<String> = listOf("in_app"),
    val enabled: Boolean = true,
) {
    fun toJson(): JSONObject = JSONObject()
        .put("name", name)
        .putIfNotNull("keywords", keywords?.ifBlank { null })
        .putIfNotNull("brand", brand?.ifBlank { null })
        .putIfNotNull("category", category?.ifBlank { null })
        .putIfNotNull("min_profit_cents", minProfitCents)
        .putIfNotNull("min_roi_bp", minRoiBp)
        .putIfNotNull("min_score", minScore)
        .putIfNotNull("max_buy_cents", maxBuyCents)
        .putIfNotNull("min_confidence", minConfidence)
        .put("exclude_suspicious", excludeSuspicious)
        .put("channels", JSONArray(channels))
        .put("enabled", enabled)
}

data class AlertRuleOut(
    val id: Long,
    val name: String,
    val keywords: String?,
    val brand: String?,
    val category: String?,
    val minProfitCents: Long?,
    val minRoiBp: Long?,
    val minScore: Double?,
    val maxBuyCents: Long?,
    val minConfidence: String?,
    val excludeSuspicious: Boolean,
    val channels: List<String>,
    val enabled: Boolean,
    val createdAt: String?,
) {
    companion object {
        fun parse(o: JSONObject) = AlertRuleOut(
            id = o.long("id"),
            name = o.str("name"),
            keywords = o.strOrNull("keywords"),
            brand = o.strOrNull("brand"),
            category = o.strOrNull("category"),
            minProfitCents = o.longOrNull("min_profit_cents"),
            minRoiBp = o.longOrNull("min_roi_bp"),
            minScore = o.doubleOrNull("min_score"),
            maxBuyCents = o.longOrNull("max_buy_cents"),
            minConfidence = o.strOrNull("min_confidence"),
            excludeSuspicious = o.bool("exclude_suspicious", true),
            channels = o.arr("channels").strings(),
            enabled = o.bool("enabled", true),
            createdAt = o.strOrNull("created_at"),
        )
    }
}

data class NotificationOut(
    val id: Long,
    val ruleId: Long?,
    val opportunityId: Long?,
    val kind: String,
    val title: String,
    val body: String,
    val createdAt: String?,
    val readAt: String?,
) {
    val isRead: Boolean get() = readAt != null

    companion object {
        fun parse(o: JSONObject) = NotificationOut(
            o.long("id"), o.longOrNull("rule_id"), o.longOrNull("opportunity_id"), o.str("kind"), o.str("title"), o.str("body"),
            o.strOrNull("created_at"), o.strOrNull("read_at"),
        )

        /**
         * Unread notifications newer than [lastSeenId], oldest first (the API returns newest first), capped
         * at the [max] most recent.  Id-only form of [NotificationCursor.unseen].
         */
        fun newSince(items: List<NotificationOut>, lastSeenId: Long, max: Int): List<NotificationOut> =
            NotificationCursor(lastSeenId).unseen(items).takeLast(max)
    }
}

/**
 * The newest notification the user has already been shown (posted by the background poller or listed
 * in the app): its id and the server's created_at.
 *
 * "Newer" compares created_at, and the id only breaks ties.  An id alone is not a safe cursor: the
 * server reuses ids once its newest rows are deleted (clear-demo; SQLite rowids without AUTOINCREMENT)
 * and a reset database starts again at 1, so a stale high id would hide every new notification.  When
 * either timestamp is missing or unparseable, ids are compared instead.
 */
data class NotificationCursor(val id: Long = 0L, val createdAt: String? = null) {

    /** True when the notification ([otherId], created at [otherCreatedAt]) came after this cursor. */
    fun isBefore(otherId: Long, otherCreatedAt: String?): Boolean {
        val mine = Formats.parseInstant(createdAt)
        val theirs = Formats.parseInstant(otherCreatedAt)
        if (mine == null || theirs == null) return otherId > id
        val byTime = theirs.compareTo(mine)
        return byTime > 0 || (byTime == 0 && otherId > id)
    }

    fun isBefore(n: NotificationOut): Boolean = isBefore(n.id, n.createdAt)

    /** Unread notifications in [items] that came after this cursor, oldest first. */
    fun unseen(items: List<NotificationOut>): List<NotificationOut> =
        items.filter { !it.isRead && isBefore(it) }.sortedBy { it.id }

    /** This cursor moved to the newest of [items]; it never moves backwards. */
    fun advancedTo(items: List<NotificationOut>): NotificationCursor =
        items.fold(this) { cursor, n -> if (cursor.isBefore(n)) NotificationCursor(n.id, n.createdAt) else cursor }
}

/** Parses a JSON array body into a list using [item]. */
fun <T> parseList(a: JSONArray, item: (JSONObject) -> T): List<T> = a.mapObjects(item)
