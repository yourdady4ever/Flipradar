package com.flipradar.app.ui

import android.content.Context
import android.graphics.Typeface
import android.view.Gravity
import android.view.View
import android.widget.LinearLayout
import android.widget.TableLayout
import android.widget.TableRow
import com.flipradar.app.R
import com.flipradar.app.data.CostLineOut
import com.flipradar.app.data.ProfitResultOut
import com.flipradar.app.util.Money

/**
 * Full, deterministic cost breakdown table (as computed by the server's calculator):
 * sale price, acquisition lines, selling lines (each with its formula), totals, net profit, ROI, margin.
 */
fun Context.costBreakdownView(r: ProfitResultOut): View {
    val box = vbox()
    val table = TableLayout(this).apply { setColumnStretchable(0, true); setColumnShrinkable(0, true) }

    fun row(label: String, amount: String, formula: String? = null, bold: Boolean = false, color: Int? = null) {
        val tr = TableRow(this)
        val labelBox = vbox().apply {
            addView(text(label, 14f, bold = bold), lp())
            if (!formula.isNullOrBlank()) addView(muted(formula, 11f), lp())
        }
        tr.addView(labelBox, TableRow.LayoutParams(0, WRAP, 1f))
        tr.addView(
            text(amount, 14f, bold = bold, color = color).apply {
                gravity = Gravity.END
                setPadding(dp(8), 0, 0, 0)
                typeface = Typeface.create(Typeface.MONOSPACE, if (bold) Typeface.BOLD else Typeface.NORMAL)
            },
            TableRow.LayoutParams(WRAP, WRAP),
        )
        tr.setPadding(0, dp(3), 0, dp(3))
        table.addView(tr)
    }

    fun lines(header: String, items: List<CostLineOut>, total: Long, totalLabel: String) {
        val h = TableRow(this)
        h.addView(sectionHeader(header).apply { setPadding(0, dp(10), 0, dp(2)) })
        table.addView(h)
        if (items.isEmpty()) row(getString(R.string.breakdown_none), Money.formatCents(0))
        for (l in items) row(l.label, Money.formatCents(l.amountCents), l.formula)
        row(totalLabel, Money.formatCents(total), bold = true)
    }

    row(getString(R.string.breakdown_sale_price), Money.formatCents(r.salePriceCents), bold = true)
    lines(getString(R.string.breakdown_acquisition), r.acquisitionLines, r.totalAcquisitionCents, getString(R.string.breakdown_total_acquisition))
    lines(getString(R.string.breakdown_selling), r.sellingLines, r.totalSellingCostsCents, getString(R.string.breakdown_total_selling))
    table.addView(View(this).apply { setBackgroundColor(getColor(R.color.card_stroke)) }, TableLayout.LayoutParams(MATCH, dp(1)).apply { topMargin = dp(6) })
    row(getString(R.string.breakdown_net_proceeds), Money.formatCents(r.netProceedsCents))
    row(getString(R.string.breakdown_net_profit), Money.formatCents(r.netProfitCents), bold = true, color = profitColor(r.netProfitCents))
    row(getString(R.string.breakdown_roi), Money.formatBpOrDash(r.roiBp), bold = true)
    row(getString(R.string.breakdown_margin), Money.formatBpOrDash(r.marginBp))
    box.addView(table, lp())
    if (r.feeProfileKey.isNotBlank()) box.addView(muted(getString(R.string.breakdown_fee_profile, r.feeProfileKey), 12f), lp().apply { topMargin = dp(6) })
    if (r.assumptions.isNotEmpty()) {
        box.addView(text(getString(R.string.breakdown_assumptions), 13f, bold = true), lp().apply { topMargin = dp(8) })
        for (a in r.assumptions) box.addView(muted("• $a", 12f), lp())
    }
    return box
}

/** Small summary tile (label above a big value) used by the dashboard and detail headers. */
fun Context.tile(label: String, value: String, valueColor: Int? = null): LinearLayout = card().apply {
    addView(muted(label, 12f), lp())
    addView(text(value, 18f, bold = true, color = valueColor), lp())
}
