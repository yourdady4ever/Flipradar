package com.flipradar.app.ui

import android.os.Bundle
import android.widget.LinearLayout
import com.flipradar.app.R
import com.flipradar.app.data.AlertRuleIn
import com.flipradar.app.data.AlertRuleOut
import com.flipradar.app.util.Formats
import com.flipradar.app.util.Money

/** Alert rules: list with enable toggle and delete, plus a create dialog. */
class AlertsActivity : BaseActivity() {
    private lateinit var listBox: LinearLayout
    private val confidences: List<String?> = listOf(null, "low", "medium", "high")

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        title = getString(R.string.nav_alerts)
        val root = vbox(16)
        val header = hbox()
        header.addView(muted(getString(R.string.alerts_intro), 12f), lp(0, WRAP, 1f))
        header.addView(button(getString(R.string.alerts_add)) { showCreateDialog() }, lp(WRAP, WRAP))
        root.addView(header, lp())
        listBox = vbox()
        root.addView(listBox, lp())
        setScreen(root, scrollable = true)
        load()
    }

    private fun load() {
        call({ api -> api.alerts() }) { render(it) }
    }

    private fun render(list: List<AlertRuleOut>) {
        listBox.removeAllViews()
        if (list.isEmpty()) listBox.addView(muted(getString(R.string.alerts_empty)), lp().apply { topMargin = dp(16) })
        for (r in list) {
            val c = card()
            val top = hbox()
            top.addView(text(r.name, 15f, bold = true), lp(0, WRAP, 1f))
            top.addView(remoteSwitch(r.enabled, getString(R.string.enabled)) { checked, revert ->
                call({ api -> api.setAlertEnabled(r.id, checked) }, onError = { e ->
                    revert()
                    showError(e, toast = true)
                }) { }
            }, lp(WRAP, WRAP))
            c.addView(top, lp())
            c.addView(muted(describe(r), 12f), lp())
            c.addView(flatButton(getString(R.string.action_delete)) {
                confirm(getString(R.string.alerts_delete_confirm, r.name)) {
                    call({ api -> api.deleteAlert(r.id) }, toastErrors = true) { load() }
                }
            }, lp(WRAP, WRAP))
            listBox.addView(c, cardLp())
        }
    }

    private fun describe(r: AlertRuleOut): String {
        val parts = ArrayList<String>()
        r.keywords?.takeIf { it.isNotBlank() }?.let { parts.add(getString(R.string.alert_desc_keywords, it)) }
        r.brand?.takeIf { it.isNotBlank() }?.let { parts.add(getString(R.string.alert_desc_brand, it)) }
        r.category?.takeIf { it.isNotBlank() }?.let { parts.add(getString(R.string.alert_desc_category, it)) }
        r.minProfitCents?.let { parts.add(getString(R.string.alert_desc_min_profit, Money.formatCents(it))) }
        r.minRoiBp?.let { parts.add(getString(R.string.alert_desc_min_roi, Money.formatBp(it))) }
        r.minScore?.let { parts.add(getString(R.string.alert_desc_min_score, Formats.score(it))) }
        r.maxBuyCents?.let { parts.add(getString(R.string.alert_desc_max_buy, Money.formatCents(it))) }
        r.minConfidence?.let { parts.add(getString(R.string.alert_desc_min_confidence, Labels.level(this, it))) }
        if (r.excludeSuspicious) parts.add(getString(R.string.alert_desc_exclude_suspicious))
        if (r.channels.isNotEmpty()) parts.add(getString(R.string.alert_desc_channels, r.channels.joinToString(", ")))
        return if (parts.isEmpty()) getString(R.string.alert_desc_any) else parts.joinToString(" · ")
    }

    private fun showCreateDialog() {
        val form = vbox()
        val name = input(getString(R.string.alerts_name_hint))
        val keywords = input(getString(R.string.optional))
        val minProfit = moneyInput(getString(R.string.optional))
        val minRoi = decimalInput(getString(R.string.optional))
        val minScore = decimalInput(getString(R.string.optional))
        val maxBuy = moneyInput(getString(R.string.optional))
        val confidenceSp = spinner(confidences.map { if (it == null) getString(R.string.any) else Labels.level(this, it) })
        val excludeSuspicious = checkbox(getString(R.string.alerts_exclude_suspicious), true)
        form.addView(labeled(getString(R.string.alerts_name), name), lp())
        form.addView(labeled(getString(R.string.alerts_keywords), keywords), lp())
        form.addView(labeled(getString(R.string.alerts_min_profit), minProfit), lp())
        form.addView(labeled(getString(R.string.alerts_min_roi), minRoi), lp())
        form.addView(labeled(getString(R.string.alerts_min_score), minScore), lp())
        form.addView(labeled(getString(R.string.alerts_max_buy), maxBuy), lp())
        form.addView(labeled(getString(R.string.alerts_min_confidence), confidenceSp), lp())
        form.addView(excludeSuspicious, lp())
        formDialog(getString(R.string.alerts_add), form, getString(R.string.action_save)) {
            val n = name.text.toString().trim()
            if (n.isEmpty()) {
                name.error = getString(R.string.err_required)
                return@formDialog false
            }
            val profit = optionalMoney(minProfit) ?: return@formDialog false
            val roi = optionalPercent(minRoi) ?: return@formDialog false
            val buy = optionalMoney(maxBuy) ?: return@formDialog false
            val scoreRaw = minScore.text.toString().trim()
            val score: Double? = if (scoreRaw.isEmpty()) null else scoreRaw.toDoubleOrNull()?.takeIf { it in 0.0..100.0 }
            if (scoreRaw.isNotEmpty() && score == null) {
                minScore.error = getString(R.string.err_out_of_range, 0, 100)
                return@formDialog false
            }
            val body = AlertRuleIn(
                name = n,
                keywords = keywords.text.toString().trim(),
                minProfitCents = profit.value,
                minRoiBp = roi.value,
                minScore = score,
                maxBuyCents = buy.value,
                minConfidence = confidences.getOrNull(confidenceSp.selectedItemPosition),
                excludeSuspicious = excludeSuspicious.isChecked,
            )
            call({ api -> api.createAlert(body) }, toastErrors = true) {
                toast(getString(R.string.alerts_created))
                load()
            }
            true
        }
    }
}
