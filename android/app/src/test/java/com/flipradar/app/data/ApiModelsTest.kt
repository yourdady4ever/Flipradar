package com.flipradar.app.data

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** Parsing tests with JSON shaped exactly like backend/flipradar/schemas.py responses. */
class ApiModelsTest {

    companion object {
        val LISTING_JSON = """
            {
              "id": 55, "source_key": "mercadolibre", "external_id": "MLM123456789",
              "url": "https://articulo.mercadolibre.com.mx/MLM-123456789", "title": "Apple iPhone 13 128 GB Medianoche",
              "currency": "MXN", "price_cents": 1099900, "price_mxn_cents": 1099900, "shipping_cents": null,
              "free_shipping": true, "condition": "new", "brand": "Apple", "model": "iPhone 13", "category": "MLM1055",
              "gtin": null, "seller_name": "TIENDA_OFICIAL", "location": "Ciudad de México", "image_url": null,
              "available_quantity": 12, "sold_quantity": 250, "status": "active", "product_id": 4,
              "match_confidence": 0.93, "match_method": "model+storage", "is_accessory": false, "is_bundle": false,
              "is_suspicious": false, "risk_flags": [], "is_demo": false,
              "first_seen_at": "2026-10-01T10:00:00Z", "last_seen_at": "2026-10-31T09:00:00Z"
            }
        """.trimIndent()

        val OPPORTUNITY_DETAIL_JSON = """
            {
              "id": 17, "product_id": 4, "product_title": "Apple iPhone 13 128GB", "product_key": "apple|iphone 13|128gb",
              "category": null, "buy_listing_id": 120, "buy_title": "iPhone 13 128gb azul seminuevo",
              "buy_url": "https://articulo.mercadolibre.com.mx/MLM-120", "buy_source_key": "mercadolibre",
              "buy_condition": "used", "image_url": null, "fee_profile_key": "mercadolibre_classic",
              "buy_total_cents": 850000, "expected_sale_cents": 1099900, "net_profit_cents": 98765,
              "roi_bp": 1162, "margin_bp": 898, "score": 72.5, "confidence": "medium", "comparable_count": 14,
              "risk_level": "warning", "demand_level": "high", "status": "new", "is_demo": false,
              "created_at": "2026-10-30T12:00:00Z", "updated_at": "2026-10-31T08:15:30.123456Z",
              "price_stats": {
                "n": 14, "median_cents": 1099900, "p25_cents": 1050000, "p75_cents": 1149900, "min_cents": 999900,
                "max_cents": 1250000, "mad_cents": 45000, "iqr_cents": 99900, "dispersion": 0.0908,
                "source_count": 2, "seller_count": 11, "excluded": {"outliers": 2, "accessories": 3},
                "comparable_ids": [55, 56, 57], "window_days": 30
              },
              "cost_breakdown": {
                "sale_price_cents": 1099900,
                "acquisition_lines": [
                  {"key": "buy_price", "label": "Buy price", "amount_cents": 820000, "formula": "listing price"},
                  {"key": "buy_shipping", "label": "Buy shipping", "amount_cents": 30000, "formula": "listing shipping"}
                ],
                "selling_lines": [
                  {"key": "commission", "label": "Marketplace commission", "amount_cents": 131135, "formula": "11.92% x sale"},
                  {"key": "seller_shipping", "label": "Seller shipping", "amount_cents": 20000, "formula": "fixed"}
                ],
                "total_acquisition_cents": 850000, "total_selling_costs_cents": 151135,
                "net_proceeds_cents": 948765, "net_profit_cents": 98765, "roi_bp": 1162, "margin_bp": 898,
                "fee_profile_key": "mercadolibre_classic", "assumptions": ["Sale price includes IVA"]
              },
              "score_components": [
                {"key": "profit", "label": "Net profit", "raw_value": 98765, "points": 20.5, "max_points": 30, "explanation": "Profit vs. target"},
                {"key": "liquidity", "label": "Liquidity", "raw_value": null, "points": 0, "max_points": 10, "explanation": "No sold-quantity data"}
              ],
              "risk_flags": [
                {"code": "price_far_below_median", "severity": "warning", "message": "Price 25% below median",
                 "evidence": {"ratio": 0.75, "median_cents": 1099900}}
              ],
              "demand": {"level": "high", "confidence": "medium",
                         "signals": [{"source": "mercadolibre", "sold_quantity": 250, "listing_id": 55}],
                         "note": "Based on cumulative sold_quantity"},
              "explanation": "Buy at 8,500.00 and sell near the 14-listing median of 10,999.00.",
              "facts": {"net_profit_cents": 98765, "roi_bp": 1162, "nested": {"a": [1, 2]}},
              "ai_summary": null,
              "comparables": [$LISTING_JSON]
            }
        """.trimIndent()
    }

    @Test
    fun parsesOpportunityDetail() {
        val d = OpportunityDetailOut.parse(JSONObject(OPPORTUNITY_DETAIL_JSON))
        val o = d.summary
        assertEquals(17L, o.id)
        assertEquals("Apple iPhone 13 128GB", o.productTitle)
        assertNull("JSON null must map to Kotlin null, not \"null\"", o.category)
        assertNull(o.imageUrl)
        assertEquals(850_000L, o.buyTotalCents)
        assertEquals(1_099_900L, o.expectedSaleCents)
        assertEquals(98_765L, o.netProfitCents)
        assertEquals(1_162L, o.roiBp)
        assertEquals(72.5, o.score, 1e-9)
        assertEquals("warning", o.riskLevel)
        assertEquals("2026-10-31T08:15:30.123456Z", o.updatedAt)

        assertEquals(14L, d.priceStats.n)
        assertEquals(1_099_900L, d.priceStats.medianCents)
        assertEquals(mapOf("outliers" to 2L, "accessories" to 3L), d.priceStats.excluded)
        assertEquals(listOf(55L, 56L, 57L), d.priceStats.comparableIds)
        assertEquals(30L, d.priceStats.windowDays)

        val cb = d.costBreakdown
        assertEquals(2, cb.acquisitionLines.size)
        assertEquals("buy_shipping", cb.acquisitionLines[1].key)
        assertEquals(cb.totalAcquisitionCents, cb.acquisitionLines.sumOf { it.amountCents })
        assertEquals(cb.totalSellingCostsCents, cb.sellingLines.sumOf { it.amountCents })
        assertEquals(cb.salePriceCents - cb.totalSellingCostsCents, cb.netProceedsCents)
        assertEquals(cb.netProceedsCents - cb.totalAcquisitionCents, cb.netProfitCents)
        assertEquals(listOf("Sale price includes IVA"), cb.assumptions)

        assertEquals(2, d.scoreComponents.size)
        assertEquals(98_765.0, d.scoreComponents[0].rawValue!!, 1e-9)
        assertNull(d.scoreComponents[1].rawValue)
        assertEquals(10.0, d.scoreComponents[1].maxPoints, 1e-9)

        assertEquals(1, d.riskFlags.size)
        assertEquals("warning", d.riskFlags[0].severity)
        assertEquals(0.75, d.riskFlags[0].evidence["ratio"])
        assertEquals(1_099_900L, d.riskFlags[0].evidence["median_cents"])

        assertEquals("high", d.demand.level)
        assertEquals(250L, d.demand.signals[0]["sold_quantity"])
        assertNull(d.aiSummary)
        assertEquals(listOf(1L, 2L), (d.facts["nested"] as Map<*, *>)["a"])

        val c = d.comparables.single()
        assertEquals(55L, c.id)
        assertNull(c.shippingCents)
        assertEquals(true, c.freeShipping)
        assertNull(c.gtin)
        assertEquals(0.93, c.matchConfidence!!, 1e-9)
        assertEquals(250L, c.soldQuantity)
    }

    @Test
    fun parsesOpportunityPage() {
        val page = Page.parse(JSONObject("""{"items": [$OPPORTUNITY_DETAIL_JSON], "total": 120, "limit": 30, "offset": 30}""")) {
            OpportunityOut.parse(it)
        }
        assertEquals(1, page.items.size)
        assertEquals(120L, page.total)
        assertTrue(page.hasMore)
        val last = Page.parse(JSONObject("""{"items": [], "total": 0, "limit": 30, "offset": 0}"""), OpportunityOut::parse)
        assertFalse(last.hasMore)
    }

    @Test
    fun toleratesMissingAndNullFields() {
        val o = OpportunityOut.parse(JSONObject("""{"id": 3, "score": 61, "buy_url": null}"""))
        assertEquals(3L, o.id)
        assertEquals(61.0, o.score, 1e-9)
        assertNull(o.buyUrl)
        assertEquals("none", o.riskLevel)
        assertEquals("new", o.status)
        val d = OpportunityDetailOut.parse(JSONObject("""{"id": 3, "price_stats": null, "demand": null}"""))
        assertEquals(PriceStatsOut.EMPTY, d.priceStats)
        assertEquals(DemandOut.UNKNOWN, d.demand)
        assertTrue(d.costBreakdown.acquisitionLines.isEmpty())
    }

    @Test
    fun parsesInventoryWithSales() {
        val json = """
            {"id": 9, "title": "Nintendo Switch OLED", "sku": null, "product_id": 2, "opportunity_id": 17,
             "condition": "used", "quantity": 3, "quantity_available": 2, "unit_cost_cents": 520000,
             "extra_costs_cents": 15000, "total_cost_cents": 1575000, "purchase_source": "facebook",
             "purchase_url": null, "purchased_at": "2026-10-20T00:00:00Z", "status": "listed",
             "target_sale_cents": 699900, "listed_channel": "mercadolibre", "location": null, "notes": null,
             "days_held": 11, "realized_profit_cents": 98000,
             "sales": [{"id": 1, "inventory_item_id": 9, "quantity": 1, "unit_price_cents": 699900, "channel": "mercadolibre",
                        "fee_profile_key": "mercadolibre_classic", "fees_cents": 83988, "shipping_cents": 0,
                        "taxes_withheld_cents": 0, "other_costs_cents": 0, "cost_basis_cents": 525000,
                        "net_proceeds_cents": 615912, "profit_cents": 90912, "sold_at": "2026-10-28T15:00:00Z", "notes": null}],
             "created_at": "2026-10-20T00:00:00Z", "updated_at": "2026-10-28T15:00:00Z"}
        """.trimIndent()
        val i = InventoryOut.parse(JSONObject(json))
        assertEquals(2L, i.quantityAvailable)
        assertNull(i.sku)
        assertEquals(17L, i.opportunityId)
        assertEquals(699_900L, i.targetSaleCents)
        val s = i.sales.single()
        assertEquals(90_912L, s.profitCents)
        assertEquals(s.netProceedsCents - s.costBasisCents, s.profitCents)
        assertNull(s.notes)
    }

    @Test
    fun parsesAnalyticsSummaryAndTimeseries() {
        val s = AnalyticsSummaryOut.parse(
            JSONObject(
                """
                {"period_days": 30, "revenue_cents": 2099700, "realized_profit_cents": 250000, "units_sold": 3,
                 "sales_count": 3, "avg_roi_bp": null, "avg_days_to_sell": 6.5, "inventory_units": 4,
                 "inventory_cost_cents": 1800000, "inventory_target_value_cents": null,
                 "aging": {"90+": 1, "0-7": 2, "8-30": 1, "31-90": 0},
                 "open_opportunities": 12, "avg_open_opportunity_score": 64.2, "potential_profit_open_cents": 450000,
                 "opportunities_by_source": {"mercadolibre": 10, "csv": 2},
                 "top_products_by_profit": [{"product_id": 4, "title": "iPhone 13", "profit_cents": 150000}],
                 "listings_tracked": 340, "products_tracked": 25, "last_collection_at": null}
                """.trimIndent(),
            ),
        )
        assertNull(s.avgRoiBp)
        assertEquals(6.5, s.avgDaysToSell!!, 1e-9)
        assertNull(s.inventoryTargetValueCents)
        assertEquals(listOf("0-7" to 2L, "8-30" to 1L, "31-90" to 0L, "90+" to 1L), s.orderedAging())
        assertEquals(10L, s.opportunitiesBySource["mercadolibre"])
        assertEquals(150_000L, s.topProductsByProfit[0]["profit_cents"])
        assertNull(s.lastCollectionAt)

        val t = TimeseriesOut.parse(
            JSONObject(
                """{"bucket": "week", "points": [
                   {"period": "2026-10-05", "revenue_cents": 0, "profit_cents": -12000, "units": 0, "purchases_cents": 500000},
                   {"period": "2026-10-12", "revenue_cents": 699900, "profit_cents": 90912, "units": 1, "purchases_cents": 0}]}""",
            ),
        )
        assertEquals("week", t.bucket)
        assertEquals(-12_000L, t.points[0].profitCents)
        assertEquals("2026-10-12", t.points[1].period)
    }

    @Test
    fun parsesFeeProfilesSourcesSearchesAlertsNotifications() {
        val fp = parseList(
            JSONArray(
                """[{"key": "mercadolibre_classic", "name": "Mercado Libre Clásica", "channel": "mercadolibre",
                    "commission_bp": 1250, "fixed_fee_tiers": [{"max_price_cents": 29900, "fee_cents": 2500}, {"max_price_cents": null, "fee_cents": 0}],
                    "payment_fee_bp": 0, "payment_fixed_cents": 0, "iva_on_fees": true, "iva_rate_bp": 1600,
                    "seller_shipping_cents": 0, "free_shipping_threshold_cents": 29900, "isr_withholding_bp": 250,
                    "iva_withholding_bp": 800, "source_url": "https://www.mercadolibre.com.mx/ayuda", "as_of": "2026-10",
                    "notes": null, "is_builtin": true, "updated_at": null}]""",
            ),
            FeeProfileOut::parse,
        )
        assertEquals(1_250L, fp[0].commissionBp)
        assertNull(fp[0].fixedFeeTiers[1].maxPriceCents)
        assertTrue(fp[0].isBuiltin)

        val src = SourceOut.parse(
            JSONObject(
                """{"key": "mercadolibre", "name": "Mercado Libre", "kind": "api", "country": "MX", "currency": "MXN",
                   "enabled": true, "default_fee_profile_key": "mercadolibre_classic", "configured": false,
                   "requires_credentials": true, "capabilities": ["search", "item"], "docs_url": null, "notes": "",
                   "last_run_at": null, "last_status": null, "last_error": null}""",
            ),
        )
        assertTrue(src.canSearch)
        assertFalse(src.configured)
        assertNull(src.docsUrl)

        val search = TrackedSearchOut.parse(
            JSONObject(
                """{"id": 2, "query": "switch oled", "category_hint": null, "source_keys": [], "max_results": 50,
                   "interval_minutes": 180, "enabled": true, "last_run_at": null, "created_at": "2026-10-01T00:00:00Z"}""",
            ),
        )
        assertEquals(emptyList<String>(), search.sourceKeys)
        assertNull(search.categoryHint)

        val run = CollectResult.parse(
            JSONObject(
                """{"runs": [{"id": 8, "source_key": "mercadolibre", "query": "switch oled", "started_at": "2026-11-01T00:00:00Z",
                   "finished_at": null, "status": "error", "items_fetched": 0, "items_new": 0, "items_updated": 0,
                   "error": "HTTP 403 from API"}], "opportunities_created": 0, "opportunities_updated": 1, "notifications_created": 0}""",
            ),
        )
        assertEquals("HTTP 403 from API", run.runs[0].error)
        assertNull(run.runs[0].finishedAt)
        assertEquals(1L, run.opportunitiesUpdated)

        val alert = AlertRuleOut.parse(
            JSONObject(
                """{"id": 1, "name": "Consolas", "keywords": "switch", "brand": null, "category": null,
                   "min_profit_cents": 50000, "min_roi_bp": 1500, "min_score": 60.0, "max_buy_cents": null,
                   "min_confidence": null, "exclude_suspicious": true, "channels": ["in_app"], "enabled": true,
                   "created_at": "2026-10-01T00:00:00Z"}""",
            ),
        )
        assertEquals(50_000L, alert.minProfitCents)
        assertNull(alert.maxBuyCents)
        assertEquals(60.0, alert.minScore!!, 1e-9)

        val notes = parseList(
            JSONArray(
                """[{"id": 12, "rule_id": 1, "opportunity_id": 17, "kind": "opportunity", "title": "Nueva oportunidad",
                     "body": "iPhone 13: +987.65", "created_at": "2026-11-01T01:00:00Z", "read_at": null},
                    {"id": 11, "rule_id": null, "opportunity_id": null, "kind": "system", "title": "Run failed",
                     "body": "x", "created_at": "2026-11-01T00:00:00Z", "read_at": "2026-11-01T00:30:00Z"}]""",
            ),
            NotificationOut::parse,
        )
        assertFalse(notes[0].isRead)
        assertTrue(notes[1].isRead)
        assertNull(notes[1].opportunityId)
    }

    @Test
    fun selectsOnlyNewUnreadNotificationsOldestFirst() {
        fun n(id: Long, read: Boolean = false) = NotificationOut(id, null, id, "opportunity", "t$id", "b", null, if (read) "x" else null)
        val newestFirst = listOf(n(20), n(19, read = true), n(18), n(17), n(16), n(15), n(14), n(10))
        val picked = NotificationOut.newSince(newestFirst, lastSeenId = 12, max = 3)
        assertEquals(listOf(17L, 18L, 20L), picked.map { it.id })
        assertTrue(NotificationOut.newSince(newestFirst, lastSeenId = 20, max = 5).isEmpty())
    }

    @Test
    fun serializesRequestBodiesOmittingNulls() {
        val calc = CalculatorIn(buyPriceCents = 850_000, salePriceCents = 1_099_900, buyShippingCents = 30_000).toJson()
        assertEquals(850_000L, calc.getLong("buy_price_cents"))
        assertFalse(calc.has("fee_profile_key"))
        assertFalse(calc.has("target_roi_bp"))
        assertFalse(calc.has("seller_shipping_override_cents"))
        assertTrue(calc.getBoolean("sale_price_includes_iva"))

        val withTarget = CalculatorIn(1, 2, feeProfileKey = "mercadolibre_classic", targetRoiBp = 2_500).toJson()
        assertEquals("mercadolibre_classic", withTarget.getString("fee_profile_key"))
        assertEquals(2_500L, withTarget.getLong("target_roi_bp"))

        val alert = AlertRuleIn(name = "Consolas", keywords = "  ", minProfitCents = 50_000, minScore = 60.0).toJson()
        assertFalse(alert.has("keywords"))
        assertEquals(50_000L, alert.getLong("min_profit_cents"))
        assertEquals("in_app", alert.getJSONArray("channels").getString(0))
        assertTrue(alert.getBoolean("exclude_suspicious"))

        val sale = SaleIn(quantity = 1, unitPriceCents = 699_900, feeProfileKey = "mercadolibre_classic", channel = "").toJson()
        assertFalse(sale.has("fees_cents"))
        assertFalse(sale.has("channel"))
        assertEquals(0L, sale.getLong("shipping_cents"))

        val buy = OpportunityBuyIn(quantity = 2).toJson()
        assertEquals(2, buy.getInt("quantity"))
        assertFalse(buy.has("unit_cost_cents"))

        val inv = InventoryIn(title = "Switch", unitCostCents = 520_000, purchaseSource = "", targetSaleCents = null).toJson()
        assertFalse(inv.has("purchase_source"))
        assertFalse(inv.has("target_sale_cents"))
        assertEquals("purchased", inv.getString("status"))

        val search = TrackedSearchIn(query = "switch oled", sourceKeys = listOf("mercadolibre")).toJson()
        assertEquals("mercadolibre", search.getJSONArray("source_keys").getString(0))
        assertFalse(search.has("category_hint"))

        val update = InventoryUpdate(status = "listed").toJson()
        assertEquals(setOf("status"), update.keys().asSequence().toSet())
    }

    @Test
    fun buildsOpportunityQueryParams() {
        val params = OpportunityQuery(q = " iphone ", minScore = 60, includeSuspicious = true, sort = "profit").toParams(30, 60)
        val map = params.toMap()
        assertEquals("iphone", map["q"])
        assertEquals(60L, map["min_score"])
        assertNull(map["status"])
        assertEquals(true, map["include_suspicious"])
        assertEquals(30, map["limit"])
        assertEquals(60, map["offset"])
    }

    @Test
    fun extractsFastApiErrorDetails() {
        assertEquals("Invalid or missing API token", ApiErrorParser.detail("""{"detail": "Invalid or missing API token"}"""))
        assertEquals(
            "query: String should have at least 2 characters; max_results: Input should be less than or equal to 200",
            ApiErrorParser.detail(
                """{"detail": [
                    {"type": "string_too_short", "loc": ["body", "query"], "msg": "String should have at least 2 characters", "input": "a"},
                    {"type": "less_than_equal", "loc": ["body", "max_results"], "msg": "Input should be less than or equal to 200"}]}""",
            ),
        )
        assertNull(ApiErrorParser.detail("Internal Server Error"))
        assertNull(ApiErrorParser.detail("""{"detail": null}"""))
        assertNull(ApiErrorParser.detail(""))
        assertNull(ApiErrorParser.detail("""["not", "an", "object"]"""))
    }

    @Test
    fun parsesHealthAndAuth() {
        val h = HealthOut.parse(JSONObject("""{"status": "ok", "version": "0.1.0", "api_version": "1", "database": "sqlite", "time": "2026-11-01T00:00:00Z"}"""))
        assertEquals(SUPPORTED_API_VERSION, h.apiVersion)
        val a = AuthCheckOut.parse(JSONObject("""{"ok": true, "token_name": null, "auth_required": false}"""))
        assertTrue(a.ok)
        assertNull(a.tokenName)
        assertFalse(a.authRequired)
    }
}
