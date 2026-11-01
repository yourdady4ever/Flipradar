package com.flipradar.app.ui

import android.os.Bundle
import android.widget.LinearLayout
import android.widget.Spinner
import com.flipradar.app.R
import com.flipradar.app.data.AnalyticsSummaryOut
import com.flipradar.app.data.TimeseriesOut
import com.flipradar.app.util.Formats
import com.flipradar.app.util.Money

/** Analytics dashboard: summary tiles, inventory aging, weekly profit chart, top products. */
class DashboardActivity : BaseActivity() {
    private lateinit var content: LinearLayout
    private lateinit var periodSpinner: Spinner
    private lateinit var chart: BarChartView
    private val periods = listOf(7, 30, 90, 365)

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        title = getString(R.string.nav_dashboard)
        val root = vbox(16)
        periodSpinner = spinner(periods.map { getString(R.string.dash_period_days, it) }, 1) { load() }
        root.addView(labeled(getString(R.string.dash_period), periodSpinner), lp())
        content = vbox()
        root.addView(content, lp())
        root.addView(sectionHeader(getString(R.string.dash_profit_chart)), lp())
        chart = BarChartView(this)
        root.addView(card().apply { addView(chart, lp()) }, cardLp())
        root.addView(muted(getString(R.string.dash_chart_note), 11f), lp())
        setScreen(root, scrollable = true)
        load()
    }

    private fun load() {
        val days = periods.getOrElse(periodSpinner.selectedItemPosition) { 30 }
        call({ api -> api.analyticsSummary(days) }) { render(it) }
        call({ api -> api.timeseries(maxOf(days, 84), "week") }) { renderChart(it) }
    }

    private fun row(vararg tiles: LinearLayout): LinearLayout = hbox().apply {
        tiles.forEachIndexed { i, t -> addView(t, lp(0, WRAP, 1f).apply { if (i > 0) marginStart = dp(6) }) }
    }

    private fun render(s: AnalyticsSummaryOut) {
        content.removeAllViews()
        content.addView(sectionHeader(getString(R.string.dash_sales, s.periodDays)), lp())
        content.addView(row(tile(getString(R.string.dash_revenue), Money.formatCents(s.revenueCents)), tile(getString(R.string.dash_realized_profit), Money.formatCents(s.realizedProfitCents), profitColor(s.realizedProfitCents))), cardLp())
        content.addView(row(tile(getString(R.string.dash_units_sold), s.unitsSold.toString()), tile(getString(R.string.dash_avg_roi), Money.formatBpOrDash(s.avgRoiBp))), cardLp())
        content.addView(row(tile(getString(R.string.dash_sales_count), s.salesCount.toString()), tile(getString(R.string.dash_avg_days_to_sell), s.avgDaysToSell?.let { Formats.score(it) } ?: Money.DASH)), cardLp())

        content.addView(sectionHeader(getString(R.string.dash_inventory)), lp())
        content.addView(row(tile(getString(R.string.dash_capital), Money.formatCents(s.inventoryCostCents)), tile(getString(R.string.dash_inventory_units), s.inventoryUnits.toString())), cardLp())
        content.addView(tile(getString(R.string.dash_inventory_target), Money.formatCentsOrDash(s.inventoryTargetValueCents)), cardLp())
        val aging = card()
        aging.addView(text(getString(R.string.dash_aging), 14f, bold = true), lp())
        val agingItems = s.orderedAging()
        if (agingItems.isEmpty()) aging.addView(muted(getString(R.string.none)), lp())
        for ((bucket, units) in agingItems) aging.addView(keyValue(getString(R.string.dash_aging_bucket, bucket), units.toString()), lp())
        content.addView(aging, cardLp())

        content.addView(sectionHeader(getString(R.string.dash_opportunities)), lp())
        content.addView(row(tile(getString(R.string.dash_open_opportunities), s.openOpportunities.toString()), tile(getString(R.string.dash_avg_score), Formats.score(s.avgOpenOpportunityScore))), cardLp())
        content.addView(tile(getString(R.string.dash_potential_profit), Money.formatCents(s.potentialProfitOpenCents), profitColor(s.potentialProfitOpenCents)), cardLp())
        if (s.opportunitiesBySource.isNotEmpty()) {
            val bySource = card()
            bySource.addView(text(getString(R.string.dash_by_source), 14f, bold = true), lp())
            for ((k, v) in s.opportunitiesBySource.entries.sortedByDescending { it.value }) bySource.addView(keyValue(k, v.toString()), lp())
            content.addView(bySource, cardLp())
        }
        if (s.topProductsByProfit.isNotEmpty()) {
            val top = card()
            top.addView(text(getString(R.string.dash_top_products), 14f, bold = true), lp())
            for (p in s.topProductsByProfit.take(10)) {
                val title = (p["title"] ?: p["product_title"] ?: p["name"] ?: p["product_id"])?.toString() ?: Money.DASH
                val profit = (p["profit_cents"] ?: p["realized_profit_cents"]) as? Number
                if (profit != null) {
                    top.addView(keyValue(title, Money.formatCents(profit.toLong()), profitColor(profit.toLong())), lp())
                } else {
                    top.addView(text("• " + Formats.anyValue(p), 13f), lp())
                }
            }
            content.addView(top, cardLp())
        }

        content.addView(sectionHeader(getString(R.string.dash_tracking)), lp())
        val tracking = card()
        tracking.addView(keyValue(getString(R.string.dash_listings_tracked), s.listingsTracked.toString()), lp())
        tracking.addView(keyValue(getString(R.string.dash_products_tracked), s.productsTracked.toString()), lp())
        tracking.addView(keyValue(getString(R.string.dash_last_collection), Formats.dateTime(s.lastCollectionAt)), lp())
        content.addView(tracking, cardLp())
    }

    private fun renderChart(t: TimeseriesOut) {
        chart.setData(t.points.map { BarChartView.Bar(Formats.shortPeriod(it.period), it.profitCents) })
    }
}
