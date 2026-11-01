package com.flipradar.app.ui

import android.os.Bundle
import android.widget.ArrayAdapter
import android.widget.CheckBox
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.Spinner
import com.flipradar.app.R
import com.flipradar.app.data.CalculatorIn
import com.flipradar.app.data.CalculatorOut
import com.flipradar.app.data.FeeProfileOut
import com.flipradar.app.util.Money

/** Profit calculator backed by POST /calculator/profit (all money math happens on the server). */
class CalculatorActivity : BaseActivity() {
    private lateinit var buyPrice: EditText
    private lateinit var buyShipping: EditText
    private lateinit var importCosts: EditText
    private lateinit var otherBuy: EditText
    private lateinit var otherSale: EditText
    private lateinit var salePrice: EditText
    private lateinit var targetRoi: EditText
    private lateinit var profileSpinner: Spinner
    private lateinit var includesIva: CheckBox
    private lateinit var withholdingsAsCost: CheckBox
    private lateinit var output: LinearLayout
    private var profiles: List<FeeProfileOut> = emptyList()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        title = getString(R.string.nav_calculator)
        val root = vbox(16)

        buyPrice = moneyInput(getString(R.string.amount_hint))
        buyShipping = moneyInput(getString(R.string.amount_zero_hint))
        importCosts = moneyInput(getString(R.string.amount_zero_hint))
        otherBuy = moneyInput(getString(R.string.amount_zero_hint))
        salePrice = moneyInput(getString(R.string.amount_hint))
        otherSale = moneyInput(getString(R.string.amount_zero_hint))
        targetRoi = decimalInput(getString(R.string.calc_target_roi_hint))
        profileSpinner = spinner(listOf(getString(R.string.calc_profile_default)))
        includesIva = checkbox(getString(R.string.calc_sale_includes_iva), true)
        withholdingsAsCost = checkbox(getString(R.string.calc_withholdings_as_cost), true)

        root.addView(sectionHeader(getString(R.string.calc_buy_section)), lp())
        root.addView(pair(labeled(getString(R.string.calc_buy_price), buyPrice), labeled(getString(R.string.calc_buy_shipping), buyShipping)), lp())
        root.addView(pair(labeled(getString(R.string.calc_import_costs), importCosts), labeled(getString(R.string.calc_other_buy), otherBuy)), lp())
        root.addView(sectionHeader(getString(R.string.calc_sell_section)), lp())
        root.addView(pair(labeled(getString(R.string.calc_sale_price), salePrice), labeled(getString(R.string.calc_other_sale), otherSale)), lp())
        root.addView(labeled(getString(R.string.calc_fee_profile), profileSpinner), lp())
        root.addView(includesIva, lp())
        root.addView(withholdingsAsCost, lp())
        root.addView(labeled(getString(R.string.calc_target_roi), targetRoi), lp())
        root.addView(button(getString(R.string.action_calculate)) { calculate() }, lp().apply { topMargin = dp(8) })

        output = vbox()
        root.addView(output, lp().apply { topMargin = dp(8) })
        root.addView(muted(getString(R.string.calc_disclaimer), 11f), lp().apply { topMargin = dp(12) })
        setScreen(root, scrollable = true)
        loadProfiles()
    }

    private fun pair(a: LinearLayout, b: LinearLayout): LinearLayout = hbox().apply {
        addView(a, lp(0, WRAP, 1f))
        addView(b, lp(0, WRAP, 1f).apply { marginStart = dp(8) })
    }

    private fun loadProfiles() {
        call({ api -> api.feeProfiles() }) { list ->
            profiles = list
            val labels = listOf(getString(R.string.calc_profile_default)) + list.map { "${it.name} (${it.key})" }
            val adapter = ArrayAdapter(this, android.R.layout.simple_spinner_item, labels)
            adapter.setDropDownViewResource(android.R.layout.simple_spinner_dropdown_item)
            profileSpinner.adapter = adapter
        }
    }

    private fun calculate() {
        val buy = requiredMoney(buyPrice) ?: return
        val sale = requiredMoney(salePrice) ?: return
        val ship = moneyOrZero(buyShipping) ?: return
        val imp = moneyOrZero(importCosts) ?: return
        val ob = moneyOrZero(otherBuy) ?: return
        val os = moneyOrZero(otherSale) ?: return
        val roi = optionalPercent(targetRoi) ?: return
        val pos = profileSpinner.selectedItemPosition
        val profileKey = if (pos <= 0) null else profiles.getOrNull(pos - 1)?.key
        val input = CalculatorIn(
            buyPriceCents = buy,
            salePriceCents = sale,
            feeProfileKey = profileKey,
            buyShippingCents = ship,
            importCostsCents = imp,
            otherBuyCostsCents = ob,
            otherSaleCostsCents = os,
            salePriceIncludesIva = includesIva.isChecked,
            treatWithholdingsAsCost = withholdingsAsCost.isChecked,
            targetRoiBp = roi.value,
        )
        call({ api -> api.calculateProfit(input) }, toastErrors = true) { show(it) }
    }

    private fun show(r: CalculatorOut) {
        output.removeAllViews()
        val tiles = hbox()
        tiles.addView(tile(getString(R.string.detail_net_profit), Money.formatCents(r.result.netProfitCents), profitColor(r.result.netProfitCents)), lp(0, WRAP, 1f))
        tiles.addView(tile(getString(R.string.detail_roi), Money.formatBpOrDash(r.result.roiBp)), lp(0, WRAP, 1f).apply { marginStart = dp(6) })
        output.addView(tiles, lp())
        val extra = card()
        extra.addView(keyValue(getString(R.string.calc_break_even), Money.formatCentsOrDash(r.breakEvenSaleCents), bold = true), lp())
        if (r.targetRoiBp != null) {
            extra.addView(
                keyValue(getString(R.string.calc_max_buy, Money.formatBp(r.targetRoiBp)), Money.formatCentsOrDash(r.maxBuyForTargetCents), bold = true),
                lp(),
            )
        }
        output.addView(extra, cardLp())
        output.addView(sectionHeader(getString(R.string.detail_cost_breakdown)), lp())
        output.addView(card().apply { addView(costBreakdownView(r.result), lp()) }, cardLp())
    }
}
