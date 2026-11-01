package com.flipradar.app.ui

import android.content.Intent
import android.os.Bundle
import android.view.Menu
import android.view.MenuItem
import android.view.View
import android.view.ViewGroup
import android.widget.Button
import android.widget.CheckBox
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ListView
import android.widget.Spinner
import android.widget.TextView
import com.flipradar.app.R
import com.flipradar.app.data.OpportunityOut
import com.flipradar.app.data.OpportunityQuery
import com.flipradar.app.util.Formats
import com.flipradar.app.util.Money

/** Paged, filterable opportunity list. */
class OpportunitiesActivity : BaseActivity() {
    private val adapter = OpportunityAdapter()
    private lateinit var list: ListView
    private lateinit var empty: TextView
    private lateinit var filters: LinearLayout
    private lateinit var minScore: EditText
    private lateinit var minProfit: EditText
    private lateinit var search: EditText
    private lateinit var sortSpinner: Spinner
    private lateinit var statusSpinner: Spinner
    private lateinit var suspicious: CheckBox
    private lateinit var loadMore: Button
    private lateinit var summary: TextView
    private var total = 0L
    private var loading = false
    private var generation = 0

    private val statusKeys: List<String?> = listOf(null) + OpportunityOut.STATUSES

    companion object {
        const val PAGE = 30
        private const val REQ_DETAIL = 7
        private const val MENU_FILTERS = 1
        private const val MENU_REFRESH = 2
        private const val MENU_RECOMPUTE = 3
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        title = getString(R.string.nav_opportunities)
        val root = vbox()

        filters = vbox(12)
        search = input(getString(R.string.opp_search_hint))
        filters.addView(search, lp())
        val row1 = hbox()
        minScore = intInput(getString(R.string.opp_min_score_hint))
        minProfit = moneyInput(getString(R.string.opp_min_profit_hint))
        row1.addView(labeled(getString(R.string.opp_min_score), minScore), lp(0, WRAP, 1f))
        row1.addView(labeled(getString(R.string.opp_min_profit), minProfit), lp(0, WRAP, 1f).apply { marginStart = dp(8) })
        filters.addView(row1, lp())
        val row2 = hbox()
        sortSpinner = spinner(OpportunityOut.SORTS.map { Labels.sort(this, it) })
        statusSpinner = spinner(statusKeys.map { if (it == null) getString(R.string.any_status) else Labels.opportunityStatus(this, it) })
        row2.addView(labeled(getString(R.string.opp_sort), sortSpinner), lp(0, WRAP, 1f))
        row2.addView(labeled(getString(R.string.opp_status), statusSpinner), lp(0, WRAP, 1f).apply { marginStart = dp(8) })
        filters.addView(row2, lp())
        suspicious = checkbox(getString(R.string.opp_include_suspicious))
        filters.addView(suspicious, lp())
        filters.addView(button(getString(R.string.action_apply)) { reload() }, lp())
        root.addView(filters, lp())

        summary = muted("").apply { setPadding(dp(12), dp(4), dp(12), dp(4)) }
        root.addView(summary, lp())

        list = ListView(this).apply {
            divider = null
            setPadding(dp(8), 0, dp(8), 0)
            clipToPadding = false
        }
        loadMore = button(getString(R.string.action_load_more)) { loadPage(append = true) }
        list.addFooterView(loadMore, null, false)
        list.adapter = adapter
        list.setOnItemClickListener { _, _, position, _ ->
            if (position < adapter.count) {
                val item = adapter.getItem(position)
                startActivityForResult(
                    Intent(this, OpportunityDetailActivity::class.java).putExtra(OpportunityDetailActivity.EXTRA_ID, item.id),
                    REQ_DETAIL,
                )
            }
        }
        empty = muted(getString(R.string.opp_empty), 15f).apply {
            setPadding(dp(24), dp(24), dp(24), dp(24))
            visibility = View.GONE
        }
        root.addView(empty, lp())
        root.addView(list, lp(MATCH, 0, 1f))
        setScreen(root)
        reload()
    }

    private fun currentQuery(): OpportunityQuery = OpportunityQuery(
        status = statusKeys.getOrNull(statusSpinner.selectedItemPosition),
        q = search.text.toString().trim().ifEmpty { null },
        minScore = minScore.text.toString().trim().toLongOrNull()?.coerceIn(0, 100),
        minProfitCents = Money.parsePesosToCents(minProfit.text.toString()),
        includeSuspicious = suspicious.isChecked,
        sort = OpportunityOut.SORTS.getOrElse(sortSpinner.selectedItemPosition) { "score" },
    )

    private fun reload() {
        if (minProfit.text.isNotBlank() && Money.parsePesosToCents(minProfit.text.toString()) == null) {
            minProfit.error = getString(R.string.err_invalid_amount)
            return
        }
        generation++
        loading = false
        adapter.replace(emptyList())
        loadPage(append = false)
    }

    private fun loadPage(append: Boolean) {
        if (loading) return
        loading = true
        val gen = generation
        val query = currentQuery()
        val offset = if (append) adapter.count else 0
        loadMore.isEnabled = false
        call({ api -> api.opportunities(query, PAGE, offset) }, onError = { e ->
            if (gen == generation) loading = false
            loadMore.isEnabled = true
            showError(e)
        }) { page ->
            if (gen != generation) return@call
            loading = false
            clearError()
            if (append) adapter.append(page.items) else adapter.replace(page.items)
            total = page.total
            updateFooter()
        }
    }

    private fun updateFooter() {
        val hasMore = adapter.count < total
        loadMore.visibility = if (hasMore) View.VISIBLE else View.GONE
        loadMore.isEnabled = true
        empty.visibility = if (adapter.count == 0) View.VISIBLE else View.GONE
        summary.text = getString(R.string.opp_count, adapter.count, total)
    }

    @Deprecated("Framework Activity result API")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        if (requestCode == REQ_DETAIL && resultCode == RESULT_OK) reload()
    }

    override fun onCreateOptionsMenu(menu: Menu): Boolean {
        menu.add(0, MENU_REFRESH, 0, R.string.action_refresh).setShowAsAction(MenuItem.SHOW_AS_ACTION_IF_ROOM)
        menu.add(0, MENU_FILTERS, 1, R.string.action_toggle_filters)
        menu.add(0, MENU_RECOMPUTE, 2, R.string.action_recompute)
        return true
    }

    override fun onOptionsItemSelected(item: MenuItem): Boolean = when (item.itemId) {
        MENU_REFRESH -> {
            reload(); true
        }
        MENU_FILTERS -> {
            filters.visibility = if (filters.visibility == View.VISIBLE) View.GONE else View.VISIBLE; true
        }
        MENU_RECOMPUTE -> {
            call({ api -> api.recompute() }, toastErrors = true) { r ->
                toast(getString(R.string.recompute_result, r.productsAnalyzed, r.opportunitiesCreated, r.opportunitiesUpdated, r.opportunitiesExpired))
                reload()
            }
            true
        }
        else -> super.onOptionsItemSelected(item)
    }

    /** Row: title, profit/ROI/score line, badges (confidence, risk, source, status, demo). */
    private inner class OpportunityAdapter : SimpleAdapter<OpportunityOut>() {
        private inner class Holder(
            val title: TextView,
            val numbers: TextView,
            val score: TextView,
            val badges: LinearLayout,
            val meta: TextView,
        )

        override fun createView(parent: ViewGroup): View {
            val ctx = this@OpportunitiesActivity
            val card = ctx.card()
            val top = ctx.hbox()
            val title = ctx.text("", 15f, bold = true).clampLines(2)
            val score = ctx.text("", 20f, bold = true, color = getColor(R.color.brand_primary_text)).apply { setPadding(dp(8), 0, 0, 0) }
            top.addView(title, lp(0, WRAP, 1f))
            top.addView(score, lp(WRAP, WRAP))
            card.addView(top, lp())
            val numbers = ctx.text("", 14f)
            card.addView(numbers, lp().apply { topMargin = dp(4) })
            val badges = ctx.hbox()
            card.addView(badges, lp().apply { topMargin = dp(6) })
            val meta = ctx.muted("", 12f)
            card.addView(meta, lp().apply { topMargin = dp(4) })
            val wrapper = LinearLayout(ctx).apply { orientation = LinearLayout.VERTICAL }
            wrapper.addView(card, ctx.cardLp())
            wrapper.tag = Holder(title, numbers, score, badges, meta)
            return wrapper
        }

        override fun bind(view: View, item: OpportunityOut, position: Int) {
            val h = view.tag as Holder
            val ctx = this@OpportunitiesActivity
            h.title.text = item.productTitle.ifBlank { item.buyTitle }
            h.score.text = Formats.score(item.score)
            val profit = Money.formatSignedCents(item.netProfitCents)
            h.numbers.text = getString(R.string.opp_row_numbers, profit, Money.formatBp(item.roiBp), Money.formatCents(item.buyTotalCents), Money.formatCents(item.expectedSaleCents))
            h.numbers.setTextColor(ctx.profitColor(item.netProfitCents))
            h.badges.removeAllViews()
            val badgeList = listOfNotNull(
                ctx.badge(getString(R.string.badge_confidence, Labels.level(ctx, item.confidence)), ctx.confidenceColor(item.confidence)),
                ctx.badge(getString(R.string.badge_risk, Labels.risk(ctx, item.riskLevel)), ctx.severityColor(item.riskLevel)),
                ctx.badge(item.buySourceKey, getColor(R.color.severity_none)),
                if (item.status != "new") ctx.badge(Labels.opportunityStatus(ctx, item.status), getColor(R.color.brand_primary)) else null,
                if (item.isDemo) ctx.badge(getString(R.string.badge_demo), getColor(R.color.severity_warning)) else null,
            )
            for (b in badgeList) h.badges.addView(b, lp(WRAP, WRAP).apply { marginEnd = dp(6) })
            h.meta.text = getString(
                R.string.opp_row_meta, Labels.condition(ctx, item.buyCondition), item.comparableCount,
                Labels.level(ctx, item.demandLevel), Formats.dateTime(item.updatedAt),
            )
        }
    }
}
