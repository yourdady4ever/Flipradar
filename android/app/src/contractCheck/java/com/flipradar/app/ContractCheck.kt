package com.flipradar.app

import com.flipradar.app.data.AlertRuleIn
import com.flipradar.app.data.ApiClient
import com.flipradar.app.data.CalculatorIn
import com.flipradar.app.data.InventoryIn
import com.flipradar.app.data.OpportunityQuery
import com.flipradar.app.data.SaleIn
import com.flipradar.app.data.TrackedSearchIn

/**
 * Contract check: runs the app's REAL ApiClient + parsers on the JVM against a live FLIP RADAR server.
 * Every call must succeed and parse.  Usage: ContractCheck <baseUrl> <token> (creates/deletes test data:
 * use a throwaway or demo database).  See android/scripts/run-contract-check.sh.
 */
fun main(args: Array<String>) {
    require(args.size >= 2) { "usage: ContractCheck <baseUrl> <token>" }
    val api = ApiClient(args[0], args[1])
    var passed = 0
    val failed = mutableListOf<String>()
    fun step(name: String, block: () -> Any?) {
        try {
            val result = block()
            passed++
            println("PASS  $name${if (result is String) "  ($result)" else ""}")
        } catch (e: Throwable) {
            failed += name
            println("FAIL  $name  ${e::class.simpleName}: ${e.message}")
        }
    }

    step("health") { api.health().status }
    step("auth check") { api.authCheck().ok.toString() }
    step("sources") { "${api.sources().size} sources" }
    step("searches") { api.searches().size.toString() }
    var oppId = 0L
    step("opportunities") {
        val page = api.opportunities(OpportunityQuery(includeSuspicious = true), 50, 0)
        check(page.items.isNotEmpty()) { "no opportunities (seed demo data first)" }
        oppId = page.items.first().id
        "${page.total} total"
    }
    step("opportunity detail") {
        val d = api.opportunity(oppId)
        check(d.costBreakdown.sellingLines.isNotEmpty()) { "missing breakdown" }
        "profit ${d.costBreakdown.netProfitCents} cents, ${d.riskFlags.size} flags"
    }
    step("set status watching") { api.setOpportunityStatus(oppId, "watching").status }
    step("set status new") { api.setOpportunityStatus(oppId, "new").status }
    step("fee profiles") { "${api.feeProfiles().size} profiles" }
    step("calculator") {
        val out = api.calculateProfit(CalculatorIn(buyPriceCents = 450_000, salePriceCents = 620_000, feeProfileKey = "ml_clasica", targetRoiBp = 2000))
        "net ${out.result.netProfitCents}, break-even ${out.breakEvenSaleCents}"
    }
    var invId = 0L
    step("create inventory") {
        invId = api.createInventory(InventoryIn(title = "Contract check item", unitCostCents = 100_000, extraCostsCents = 5_000, condition = "used")).id
        invId.toString()
    }
    var saleId = 0L
    step("record sale") {
        val sale = api.recordSale(invId, SaleIn(unitPriceCents = 150_000, feeProfileKey = "facebook_local"))
        check(sale.profitCents == 45_000L) { "expected profit 45000, got ${sale.profitCents}" }
        saleId = sale.id
        "profit ${sale.profitCents}"
    }
    step("inventory list") { "${api.inventory(null, null, 50, 0).total} items" }
    step("inventory item") { api.inventoryItem(invId).status }
    step("delete sale") { api.deleteSale(saleId).ok.toString() }
    step("delete inventory") { api.deleteInventory(invId).ok.toString() }
    step("analytics summary") { "revenue ${api.analyticsSummary(30).revenueCents}" }
    step("timeseries") { "${api.timeseries(90, "week").points.size} points" }
    var alertId = 0L
    step("create alert") { alertId = api.createAlert(AlertRuleIn(name = "contract", minProfitCents = 100_000)).id; alertId.toString() }
    step("alerts") { api.alerts().size.toString() }
    step("toggle alert") { api.setAlertEnabled(alertId, false).enabled.toString() }
    step("delete alert") { api.deleteAlert(alertId).ok.toString() }
    var searchId = 0L
    step("create search") { searchId = api.createSearch(TrackedSearchIn(query = "nintendo switch oled")).id; searchId.toString() }
    step("run search") { "${api.runSearch(searchId).runs.size} runs" }
    step("delete search") { api.deleteSearch(searchId).ok.toString() }
    step("recompute") { "${api.recompute().productsAnalyzed} products" }
    step("notifications") { "${api.notifications(false).size} notifications" }
    step("mark all read") { api.markAllNotificationsRead().ok.toString() }

    println("\n$passed passed, ${failed.size} failed")
    failed.forEach { println("  - $it") }
    if (failed.isNotEmpty()) kotlin.system.exitProcess(1)
}
