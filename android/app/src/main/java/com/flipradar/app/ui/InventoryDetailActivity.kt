package com.flipradar.app.ui

import android.content.Intent
import android.os.Bundle
import android.view.Menu
import android.view.MenuItem
import android.widget.LinearLayout
import com.flipradar.app.R
import com.flipradar.app.data.FeeProfileOut
import com.flipradar.app.data.InventoryOut
import com.flipradar.app.data.InventoryStatus
import com.flipradar.app.data.InventoryUpdate
import com.flipradar.app.data.SaleIn
import com.flipradar.app.data.SaleOut
import com.flipradar.app.util.Formats
import com.flipradar.app.util.Money

/** One inventory item: details, status change, record sale (fees via a fee profile), sales history. */
class InventoryDetailActivity : BaseActivity() {
    companion object {
        const val EXTRA_ID = "inventory_id"
        private const val MENU_DELETE = 1
    }

    private var id = 0L
    private lateinit var content: LinearLayout
    private var item: InventoryOut? = null
    private var profiles: List<FeeProfileOut>? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        title = getString(R.string.inv_detail_title)
        id = intent.getLongExtra(EXTRA_ID, 0)
        content = vbox(16)
        setScreen(content, scrollable = true)
        if (id <= 0) {
            content.addView(muted(getString(R.string.err_missing_id)))
            return
        }
        load()
    }

    private fun load() {
        call({ api -> api.inventoryItem(id) }) { render(it) }
    }

    private fun render(i: InventoryOut) {
        item = i
        content.removeAllViews()
        content.addView(heading(i.title), lp())
        content.addView(
            badgeRow(
                badge(Labels.inventoryStatus(this, i.status), getColor(R.color.brand_primary)),
                badge(Labels.condition(this, i.condition), getColor(R.color.severity_none)),
            ),
            lp(),
        )
        val tiles = hbox()
        tiles.addView(tile(getString(R.string.inv_available), "${i.quantityAvailable} / ${i.quantity}"), lp(0, WRAP, 1f))
        tiles.addView(tile(getString(R.string.inv_total_cost), Money.formatCents(i.totalCostCents)), lp(0, WRAP, 1f).apply { marginStart = dp(6) })
        tiles.addView(
            tile(getString(R.string.inv_realized_profit), Money.formatCents(i.realizedProfitCents), profitColor(i.realizedProfitCents)),
            lp(0, WRAP, 1f).apply { marginStart = dp(6) },
        )
        content.addView(tiles, lp().apply { topMargin = dp(8) })

        val actions = hbox()
        actions.addView(button(getString(R.string.inv_record_sale)) { showSaleDialog() }.apply { isEnabled = i.quantityAvailable > 0 }, lp(0, WRAP, 1f))
        actions.addView(button(getString(R.string.inv_change_status)) { showStatusDialog() }, lp(0, WRAP, 1f))
        content.addView(actions, lp().apply { topMargin = dp(8) })

        content.addView(sectionHeader(getString(R.string.inv_details)), lp())
        val d = card()
        d.addView(keyValue(getString(R.string.buy_unit_cost), Money.formatCents(i.unitCostCents)), lp())
        d.addView(keyValue(getString(R.string.buy_extra_costs), Money.formatCents(i.extraCostsCents)), lp())
        d.addView(keyValue(getString(R.string.inv_target_sale), Money.formatCentsOrDash(i.targetSaleCents)), lp())
        d.addView(keyValue(getString(R.string.inv_days_held), i.daysHeld.toString()), lp())
        d.addView(keyValue(getString(R.string.inv_purchased_at), Formats.dateTime(i.purchasedAt)), lp())
        i.purchaseSource?.let { d.addView(keyValue(getString(R.string.inv_purchase_source), it), lp()) }
        i.sku?.let { d.addView(keyValue(getString(R.string.inv_sku), it), lp()) }
        i.listedChannel?.let { d.addView(keyValue(getString(R.string.inv_listed_channel), it), lp()) }
        i.location?.let { d.addView(keyValue(getString(R.string.inv_location), it), lp()) }
        i.notes?.takeIf { it.isNotBlank() }?.let { d.addView(muted(it, 13f), lp().apply { topMargin = dp(4) }) }
        if (!i.purchaseUrl.isNullOrBlank()) d.addView(flatButton(getString(R.string.action_open_listing)) { openUrl(i.purchaseUrl) }, lp(WRAP, WRAP))
        i.opportunityId?.let { oid ->
            d.addView(flatButton(getString(R.string.inv_open_opportunity)) {
                startActivity(Intent(this, OpportunityDetailActivity::class.java).putExtra(OpportunityDetailActivity.EXTRA_ID, oid))
            }, lp(WRAP, WRAP))
        }
        content.addView(d, cardLp())

        content.addView(sectionHeader(getString(R.string.inv_sales, i.sales.size)), lp())
        if (i.sales.isEmpty()) content.addView(muted(getString(R.string.inv_no_sales)), lp())
        for (s in i.sales) content.addView(saleCard(s), cardLp())
    }

    private fun saleCard(s: SaleOut): LinearLayout = card().apply {
        addView(keyValue(getString(R.string.sale_header, s.quantity, Money.formatCents(s.unitPriceCents)), Money.formatSignedCents(s.profitCents), profitColor(s.profitCents), bold = true), lp())
        addView(muted(listOfNotNull(Formats.dateTime(s.soldAt), s.channel, s.feeProfileKey).joinToString(" · "), 12f), lp())
        addView(keyValue(getString(R.string.sale_fees), Money.formatCents(s.feesCents)), lp())
        addView(keyValue(getString(R.string.sale_shipping), Money.formatCents(s.shippingCents)), lp())
        addView(keyValue(getString(R.string.sale_taxes_withheld), Money.formatCents(s.taxesWithheldCents)), lp())
        addView(keyValue(getString(R.string.sale_other_costs), Money.formatCents(s.otherCostsCents)), lp())
        addView(keyValue(getString(R.string.sale_net_proceeds), Money.formatCents(s.netProceedsCents)), lp())
        addView(keyValue(getString(R.string.sale_cost_basis), Money.formatCents(s.costBasisCents)), lp())
        s.notes?.takeIf { it.isNotBlank() }?.let { addView(muted(it, 12f), lp()) }
        addView(flatButton(getString(R.string.sale_delete)) {
            confirm(getString(R.string.sale_delete_confirm)) {
                call({ api -> api.deleteSale(s.id) }, toastErrors = true) { load() }
            }
        }, lp(WRAP, WRAP))
    }

    private fun showStatusDialog() {
        val i = item ?: return
        val form = vbox()
        val statuses = InventoryStatus.UPDATE
        val sp = spinner(statuses.map { Labels.inventoryStatus(this, it) }, statuses.indexOf(i.status).coerceAtLeast(0))
        val target = moneyInput(getString(R.string.optional), i.targetSaleCents?.let { Money.centsToInput(it) })
        val channel = input(getString(R.string.optional), initial = i.listedChannel)
        form.addView(labeled(getString(R.string.opp_status), sp), lp())
        form.addView(labeled(getString(R.string.inv_target_sale), target), lp())
        form.addView(labeled(getString(R.string.inv_listed_channel), channel), lp())
        formDialog(getString(R.string.inv_change_status), form, getString(R.string.action_save)) {
            val tg = optionalMoney(target) ?: return@formDialog false
            val update = InventoryUpdate(
                status = statuses.getOrNull(sp.selectedItemPosition),
                targetSaleCents = tg.value,
                listedChannel = channel.text.toString().trim().ifEmpty { null },
            )
            call({ api -> api.updateInventory(id, update) }, toastErrors = true) { render(it) }
            true
        }
    }

    private fun showSaleDialog() {
        val cached = profiles
        if (cached == null) {
            call({ api -> api.feeProfiles() }, onError = { showSaleDialogWith(emptyList()) }) { list ->
                profiles = list
                showSaleDialogWith(list)
            }
        } else {
            showSaleDialogWith(cached)
        }
    }

    private fun showSaleDialogWith(list: List<FeeProfileOut>) {
        val i = item ?: return
        val form = vbox()
        val qty = intInput(getString(R.string.buy_quantity), "1")
        val price = moneyInput(getString(R.string.amount_hint), i.targetSaleCents?.let { Money.centsToInput(it) })
        val profileLabels = listOf(getString(R.string.sale_no_profile)) + list.map { "${it.name} (${it.key})" }
        val profileSp = spinner(profileLabels)
        val fees = moneyInput(getString(R.string.sale_fees_hint))
        val shipping = moneyInput(getString(R.string.amount_zero_hint))
        val other = moneyInput(getString(R.string.amount_zero_hint))
        val channel = input(getString(R.string.optional), initial = i.listedChannel)
        val notes = input(getString(R.string.optional))
        form.addView(labeled(getString(R.string.buy_quantity), qty), lp())
        form.addView(labeled(getString(R.string.sale_unit_price), price), lp())
        form.addView(labeled(getString(R.string.calc_fee_profile), profileSp), lp())
        form.addView(labeled(getString(R.string.sale_fees), fees), lp())
        form.addView(labeled(getString(R.string.sale_shipping), shipping), lp())
        form.addView(labeled(getString(R.string.sale_other_costs), other), lp())
        form.addView(labeled(getString(R.string.inv_listed_channel), channel), lp())
        form.addView(labeled(getString(R.string.notes), notes), lp())
        form.addView(muted(getString(R.string.sale_fee_note), 12f), lp())
        formDialog(getString(R.string.inv_record_sale), form, getString(R.string.action_save)) {
            val q = optionalInt(qty, 1..i.quantityAvailable.toInt().coerceAtLeast(1)) ?: return@formDialog false
            val p = requiredMoney(price) ?: return@formDialog false
            val f = optionalMoney(fees) ?: return@formDialog false
            val sh = moneyOrZero(shipping) ?: return@formDialog false
            val ot = moneyOrZero(other) ?: return@formDialog false
            val pos = profileSp.selectedItemPosition
            val sale = SaleIn(
                quantity = q.value ?: 1,
                unitPriceCents = p,
                channel = channel.text.toString().trim(),
                feeProfileKey = if (pos <= 0) null else list.getOrNull(pos - 1)?.key,
                feesCents = f.value,
                shippingCents = sh,
                otherCostsCents = ot,
                notes = notes.text.toString().trim(),
            )
            call({ api -> api.recordSale(id, sale) }, toastErrors = true) { s ->
                toast(getString(R.string.sale_recorded, Money.formatSignedCents(s.profitCents)))
                load()
            }
            true
        }
    }

    override fun onCreateOptionsMenu(menu: Menu): Boolean {
        menu.add(0, MENU_DELETE, 0, R.string.inv_delete)
        return true
    }

    override fun onOptionsItemSelected(item: MenuItem): Boolean {
        if (item.itemId == MENU_DELETE) {
            confirm(getString(R.string.inv_delete_confirm)) {
                call({ api -> api.deleteInventory(id) }, toastErrors = true) {
                    toast(getString(R.string.deleted))
                    finish()
                }
            }
            return true
        }
        return super.onOptionsItemSelected(item)
    }
}
