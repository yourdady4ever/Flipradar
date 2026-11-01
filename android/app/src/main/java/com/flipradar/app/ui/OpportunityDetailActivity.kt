package com.flipradar.app.ui

import android.content.Intent
import android.os.Bundle
import android.widget.LinearLayout
import android.widget.ProgressBar
import android.widget.TextView
import com.flipradar.app.R
import com.flipradar.app.data.AiSummaryOut
import com.flipradar.app.data.OpportunityBuyIn
import com.flipradar.app.data.OpportunityDetailOut
import com.flipradar.app.util.Async
import com.flipradar.app.util.Formats
import com.flipradar.app.util.Money

/** Everything the server knows about one opportunity, with actions (open, watch, dismiss, buy, AI). */
class OpportunityDetailActivity : BaseActivity() {
    companion object {
        const val EXTRA_ID = "opportunity_id"
        const val EXTRA_NOTIFICATION_ID = "notification_id"
    }

    private var id: Long = 0
    private lateinit var content: LinearLayout
    private var detail: OpportunityDetailOut? = null
    private var aiBox: LinearLayout? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        title = getString(R.string.opp_detail_title)
        id = intent.getLongExtra(EXTRA_ID, 0)
        content = vbox(16)
        setScreen(content, scrollable = true)
        if (id <= 0) {
            content.addView(muted(getString(R.string.err_missing_id)))
            return
        }
        // Opened from a system notification: mark it read on the server (best effort).
        val notificationId = intent.getLongExtra(EXTRA_NOTIFICATION_ID, 0)
        if (notificationId > 0) settings.apiClient()?.let { api -> Async.background { api.markNotificationRead(notificationId) } }
        load()
    }

    private fun load() {
        call({ api -> api.opportunity(id) }) { d ->
            detail = d
            render(d)
        }
    }

    private fun render(d: OpportunityDetailOut) {
        val o = d.summary
        content.removeAllViews()

        // ---- Header ------------------------------------------------------------------------------
        content.addView(heading(o.productTitle.ifBlank { o.buyTitle }), lp())
        content.addView(
            badgeRow(
                badge(getString(R.string.badge_score, Formats.score(o.score)), getColor(R.color.brand_primary)),
                badge(getString(R.string.badge_confidence, Labels.level(this, o.confidence)), confidenceColor(o.confidence)),
                badge(getString(R.string.badge_risk, Labels.risk(this, o.riskLevel)), severityColor(o.riskLevel)),
                badge(Labels.opportunityStatus(this, o.status), getColor(R.color.severity_none)),
                if (o.isDemo) badge(getString(R.string.badge_demo), getColor(R.color.severity_warning)) else null,
            ),
            lp().apply { topMargin = dp(4) },
        )
        val tiles = hbox()
        tiles.addView(tile(getString(R.string.detail_net_profit), Money.formatCents(o.netProfitCents), profitColor(o.netProfitCents)), lp(0, WRAP, 1f))
        tiles.addView(tile(getString(R.string.detail_roi), Money.formatBp(o.roiBp)), lp(0, WRAP, 1f).apply { marginStart = dp(6) })
        tiles.addView(tile(getString(R.string.detail_margin), Money.formatBp(o.marginBp)), lp(0, WRAP, 1f).apply { marginStart = dp(6) })
        content.addView(tiles, lp().apply { topMargin = dp(8) })

        // ---- Actions -------------------------------------------------------------------------------
        val actions1 = hbox()
        actions1.addView(button(getString(R.string.action_open_listing)) { openUrl(o.buyUrl) }, lp(0, WRAP, 1f))
        actions1.addView(button(getString(R.string.action_mark_bought)) { showBuyDialog(d) }, lp(0, WRAP, 1f))
        content.addView(actions1, lp().apply { topMargin = dp(8) })
        val actions2 = hbox()
        actions2.addView(button(getString(R.string.action_watch)) { setStatus("watching") }, lp(0, WRAP, 1f))
        actions2.addView(button(getString(R.string.action_dismiss)) { setStatus("dismissed") }, lp(0, WRAP, 1f))
        content.addView(actions2, lp())

        // ---- Buy listing -------------------------------------------------------------------------
        content.addView(sectionHeader(getString(R.string.detail_buy_listing)), lp())
        val buy = card()
        buy.addView(text(o.buyTitle, 14f, bold = true), lp())
        buy.addView(keyValue(getString(R.string.detail_source), o.buySourceKey), lp())
        buy.addView(keyValue(getString(R.string.detail_condition), Labels.condition(this, o.buyCondition)), lp())
        buy.addView(keyValue(getString(R.string.detail_buy_total), Money.formatCents(o.buyTotalCents), bold = true), lp())
        o.category?.let { buy.addView(keyValue(getString(R.string.detail_category), it), lp()) }
        buy.addView(keyValue(getString(R.string.detail_fee_profile), o.feeProfileKey), lp())
        buy.addView(keyValue(getString(R.string.detail_updated), Formats.dateTime(o.updatedAt)), lp())
        if (!o.buyUrl.isNullOrBlank()) buy.addView(muted(o.buyUrl, 11f).apply { setTextIsSelectable(true) }, lp())
        content.addView(buy, cardLp())

        // ---- Expected sale price & derivation -------------------------------------------------------
        content.addView(sectionHeader(getString(R.string.detail_expected_sale)), lp())
        val st = d.priceStats
        val sale = card()
        sale.addView(keyValue(getString(R.string.detail_expected_sale_price), Money.formatCents(o.expectedSaleCents), bold = true), lp())
        sale.addView(
            muted(getString(R.string.detail_sale_derivation, st.n, Money.formatCentsOrDash(st.medianCents), st.sourceCount, st.sellerCount), 12f),
            lp(),
        )
        sale.addView(keyValue(getString(R.string.detail_p25_p75), Money.formatCentsOrDash(st.p25Cents) + " – " + Money.formatCentsOrDash(st.p75Cents)), lp())
        sale.addView(keyValue(getString(R.string.detail_min_max), Money.formatCentsOrDash(st.minCents) + " – " + Money.formatCentsOrDash(st.maxCents)), lp())
        st.madCents?.let { sale.addView(keyValue(getString(R.string.detail_mad), Money.formatCents(it)), lp()) }
        st.dispersion?.let { sale.addView(keyValue(getString(R.string.detail_dispersion), Formats.fractionPercent(it)), lp()) }
        st.windowDays?.let { sale.addView(keyValue(getString(R.string.detail_window_days), it.toString()), lp()) }
        if (st.excluded.isNotEmpty()) {
            sale.addView(muted(getString(R.string.detail_excluded, st.excluded.entries.joinToString(", ") { "${it.key}: ${it.value}" }), 12f), lp())
        }
        content.addView(sale, cardLp())

        // ---- Cost breakdown ------------------------------------------------------------------------
        content.addView(sectionHeader(getString(R.string.detail_cost_breakdown)), lp())
        content.addView(card().apply { addView(costBreakdownView(d.costBreakdown), lp()) }, cardLp())

        // ---- Score components ------------------------------------------------------------------------
        content.addView(sectionHeader(getString(R.string.detail_score_components, Formats.score(o.score))), lp())
        val score = card()
        if (d.scoreComponents.isEmpty()) score.addView(muted(getString(R.string.none)), lp())
        for (c in d.scoreComponents) {
            score.addView(keyValue(c.label, Formats.score(c.points) + " / " + Formats.score(c.maxPoints), bold = true), lp())
            val bar = ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal).apply {
                max = 1000
                progress = if (c.maxPoints > 0) ((c.points / c.maxPoints).coerceIn(0.0, 1.0) * 1000).toInt() else 0
            }
            score.addView(bar, lp())
            if (c.explanation.isNotBlank()) score.addView(muted(c.explanation, 12f), lp().apply { bottomMargin = dp(6) })
        }
        content.addView(score, cardLp())

        // ---- Risk flags --------------------------------------------------------------------------
        content.addView(sectionHeader(getString(R.string.detail_risk_flags)), lp())
        val risks = card()
        if (d.riskFlags.isEmpty()) risks.addView(muted(getString(R.string.detail_no_risk_flags)), lp())
        for (f in d.riskFlags) {
            val row = hbox()
            row.addView(badge(Labels.risk(this, f.severity), severityColor(f.severity)), lp(WRAP, WRAP).apply { marginEnd = dp(8) })
            row.addView(text(f.message.ifBlank { f.code }, 14f), lp(0, WRAP, 1f))
            risks.addView(row, lp().apply { topMargin = dp(4) })
            if (f.evidence.isNotEmpty()) risks.addView(muted(Formats.anyValue(f.evidence), 11f), lp())
        }
        content.addView(risks, cardLp())

        // ---- Demand -------------------------------------------------------------------------------
        content.addView(sectionHeader(getString(R.string.detail_demand)), lp())
        val demand = card()
        demand.addView(keyValue(getString(R.string.detail_demand_level), Labels.level(this, d.demand.level), bold = true), lp())
        demand.addView(keyValue(getString(R.string.detail_demand_confidence), Labels.level(this, d.demand.confidence)), lp())
        if (d.demand.note.isNotBlank()) demand.addView(muted(d.demand.note, 12f), lp())
        if (d.demand.signals.isEmpty()) demand.addView(muted(getString(R.string.detail_no_demand_signals), 12f), lp())
        for (s in d.demand.signals) demand.addView(text("• " + Formats.anyValue(s), 13f), lp())
        content.addView(demand, cardLp())

        // ---- Explanation --------------------------------------------------------------------------
        content.addView(sectionHeader(getString(R.string.detail_explanation)), lp())
        content.addView(card().apply {
            addView(text(d.explanation.ifBlank { getString(R.string.none) }, 14f).apply { setTextIsSelectable(true) }, lp())
        }, cardLp())

        // ---- AI summary ---------------------------------------------------------------------------
        content.addView(sectionHeader(getString(R.string.detail_ai_summary)), lp())
        val ai = card()
        aiBox = ai
        if (d.aiSummary.isNullOrBlank()) {
            ai.addView(muted(getString(R.string.detail_ai_none), 12f), lp())
        } else {
            ai.addView(text(d.aiSummary, 14f).apply { setTextIsSelectable(true) }, lp())
        }
        ai.addView(muted(getString(R.string.detail_ai_disclaimer), 11f), lp().apply { topMargin = dp(4) })
        ai.addView(button(getString(R.string.action_ai_summary)) { requestAiSummary() }, lp())
        content.addView(ai, cardLp())

        // ---- Comparables --------------------------------------------------------------------------
        if (d.comparables.isNotEmpty()) {
            content.addView(sectionHeader(getString(R.string.detail_comparables, d.comparables.size)), lp())
            val comp = card()
            for (c in d.comparables.take(25)) {
                val row = vbox().apply { setPadding(0, dp(4), 0, dp(4)) }
                row.addView(keyValue(c.title, Money.formatCentsOrDash(c.priceMxnCents ?: c.priceCents)).apply {
                    (getChildAt(0) as TextView).clampLines(2)
                }, lp())
                row.addView(muted(listOfNotNull(c.sourceKey, Labels.condition(this, c.condition), c.sellerName, Formats.date(c.lastSeenAt)).joinToString(" · "), 11f), lp())
                if (!c.url.isNullOrBlank()) row.setOnClickListener { openUrl(c.url) }
                comp.addView(row, lp())
            }
            content.addView(comp, cardLp())
        }

        // ---- Facts (deterministic inputs used for the AI prompt) -------------------------------------
        if (d.facts.isNotEmpty()) {
            content.addView(sectionHeader(getString(R.string.detail_facts)), lp())
            val facts = card()
            for ((k, v) in d.facts) facts.addView(keyValue(k, Formats.anyValue(v)).apply { (getChildAt(1) as TextView).clampLines(3) }, lp())
            content.addView(facts, cardLp())
        }
    }

    private fun setStatus(status: String) {
        call({ api -> api.setOpportunityStatus(id, status) }, toastErrors = true) { updated ->
            toast(getString(R.string.status_changed, Labels.opportunityStatus(this, updated.status)))
            setResult(RESULT_OK)
            load()
        }
    }

    private fun requestAiSummary() {
        call({ api -> api.aiSummary(id) }, toastErrors = true) { r -> showAi(r) }
    }

    private fun showAi(r: AiSummaryOut) {
        val box = aiBox ?: return
        box.removeAllViews()
        if (r.summary.isNullOrBlank()) {
            box.addView(muted(r.message.ifBlank { getString(R.string.detail_ai_unavailable) }, 13f), lp())
        } else {
            box.addView(text(r.summary, 14f).apply { setTextIsSelectable(true) }, lp())
        }
        val validated = if (r.validated) getString(R.string.ai_validated) else getString(R.string.ai_not_validated)
        box.addView(
            badgeRow(
                badge(validated, getColor(if (r.validated) R.color.profit_badge else R.color.severity_warning)),
                badge(r.provider, getColor(R.color.severity_none)),
            ),
            lp().apply { topMargin = dp(6) },
        )
        if (r.message.isNotBlank() && !r.summary.isNullOrBlank()) box.addView(muted(r.message, 12f), lp())
        box.addView(muted(getString(R.string.detail_ai_disclaimer), 11f), lp().apply { topMargin = dp(4) })
        box.addView(button(getString(R.string.action_ai_summary)) { requestAiSummary() }, lp())
    }

    private fun showBuyDialog(d: OpportunityDetailOut) {
        val form = vbox()
        val qty = intInput(getString(R.string.buy_quantity), "1")
        val unitCost = moneyInput(getString(R.string.buy_unit_cost_hint))
        val extra = moneyInput(getString(R.string.buy_extra_costs_hint))
        val notes = input(getString(R.string.notes))
        form.addView(labeled(getString(R.string.buy_quantity), qty), lp())
        form.addView(labeled(getString(R.string.buy_unit_cost), unitCost), lp())
        form.addView(labeled(getString(R.string.buy_extra_costs), extra), lp())
        form.addView(labeled(getString(R.string.notes), notes), lp())
        form.addView(muted(getString(R.string.buy_defaults_note, Money.formatCents(d.summary.buyTotalCents)), 12f), lp())
        formDialog(getString(R.string.action_mark_bought), form, getString(R.string.action_save)) {
            val q = qty.text.toString().trim().toIntOrNull()
            if (q == null || q < 1 || q > 1000) {
                qty.error = getString(R.string.err_invalid_quantity)
                return@formDialog false
            }
            val unit = optionalMoney(unitCost) ?: return@formDialog false
            val ext = optionalMoney(extra) ?: return@formDialog false
            val body = OpportunityBuyIn(q, unit.value, ext.value, notes.text.toString().trim().ifEmpty { null })
            call({ api -> api.buyOpportunity(id, body) }, toastErrors = true) { item ->
                toast(getString(R.string.buy_done, item.title))
                setResult(RESULT_OK)
                startActivity(Intent(this, InventoryDetailActivity::class.java).putExtra(InventoryDetailActivity.EXTRA_ID, item.id))
                load()
            }
            true
        }
    }
}
