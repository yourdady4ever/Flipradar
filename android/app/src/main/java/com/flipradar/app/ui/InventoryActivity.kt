package com.flipradar.app.ui

import android.content.Intent
import android.os.Bundle
import android.view.Menu
import android.view.MenuItem
import android.view.View
import android.view.ViewGroup
import android.widget.Button
import android.widget.LinearLayout
import android.widget.ListView
import android.widget.Spinner
import android.widget.TextView
import com.flipradar.app.R
import com.flipradar.app.data.InventoryIn
import com.flipradar.app.data.InventoryOut
import com.flipradar.app.data.InventoryStatus
import com.flipradar.app.util.Money

/** Inventory list with status filter, paging and an "add item" dialog. */
class InventoryActivity : BaseActivity() {
    private val adapter = InventoryAdapter()
    private lateinit var statusSpinner: Spinner
    private lateinit var loadMore: Button
    private lateinit var empty: TextView
    private var total = 0L
    private var generation = 0
    private val statusKeys: List<String?> = listOf(null) + InventoryStatus.FILTER

    private companion object {
        const val PAGE = 50
        const val MENU_ADD = 1
        const val MENU_REFRESH = 2
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        title = getString(R.string.nav_inventory)
        val root = vbox()
        val top = hbox(8)
        statusSpinner = spinner(statusKeys.map { if (it == null) getString(R.string.any_status) else Labels.inventoryStatus(this, it) }) { reload() }
        top.addView(statusSpinner, lp(0, WRAP, 1f))
        top.addView(button(getString(R.string.inv_add)) { showAddDialog() }, lp(WRAP, WRAP))
        root.addView(top, lp())
        empty = muted(getString(R.string.inv_empty), 15f).apply {
            setPadding(dp(24), dp(24), dp(24), dp(24))
            visibility = View.GONE
        }
        root.addView(empty, lp())
        val list = ListView(this).apply {
            divider = null
            setPadding(dp(8), 0, dp(8), 0)
        }
        loadMore = button(getString(R.string.action_load_more)) { load(append = true) }
        list.addFooterView(loadMore, null, false)
        list.adapter = adapter
        list.setOnItemClickListener { _, _, position, _ ->
            if (position < adapter.count) {
                startActivity(Intent(this, InventoryDetailActivity::class.java).putExtra(InventoryDetailActivity.EXTRA_ID, adapter.getItem(position).id))
            }
        }
        root.addView(list, lp(MATCH, 0, 1f))
        setScreen(root)
    }

    override fun onResume() {
        super.onResume()
        reload() // pick up sales / status changes made on the detail screen
    }

    private fun reload() {
        generation++
        load(append = false)
    }

    private fun load(append: Boolean) {
        val gen = generation
        val status = statusKeys.getOrNull(statusSpinner.selectedItemPosition)
        val offset = if (append) adapter.count else 0
        loadMore.isEnabled = false
        call({ api -> api.inventory(status, null, PAGE, offset) }, onError = { e ->
            loadMore.isEnabled = true
            showError(e)
        }) { page ->
            if (gen != generation) return@call
            clearError()
            if (append) adapter.append(page.items) else adapter.replace(page.items)
            total = page.total
            loadMore.isEnabled = true
            loadMore.visibility = if (adapter.count < total) View.VISIBLE else View.GONE
            empty.visibility = if (adapter.count == 0) View.VISIBLE else View.GONE
        }
    }

    private fun showAddDialog() {
        val form = vbox()
        val titleIn = input(getString(R.string.inv_title_hint))
        val qty = intInput(getString(R.string.buy_quantity), "1")
        val unit = moneyInput(getString(R.string.amount_hint))
        val extra = moneyInput(getString(R.string.amount_zero_hint))
        val target = moneyInput(getString(R.string.optional))
        val source = input(getString(R.string.optional))
        val conditionSpinner = spinner(InventoryStatus.CONDITIONS.map { Labels.condition(this, it) }, InventoryStatus.CONDITIONS.indexOf("unknown"))
        val statusSp = spinner(InventoryStatus.CREATE.map { Labels.inventoryStatus(this, it) })
        val notes = input(getString(R.string.optional))
        form.addView(labeled(getString(R.string.inv_title), titleIn), lp())
        form.addView(labeled(getString(R.string.buy_quantity), qty), lp())
        form.addView(labeled(getString(R.string.buy_unit_cost), unit), lp())
        form.addView(labeled(getString(R.string.buy_extra_costs), extra), lp())
        form.addView(labeled(getString(R.string.inv_target_sale), target), lp())
        form.addView(labeled(getString(R.string.inv_purchase_source), source), lp())
        form.addView(labeled(getString(R.string.detail_condition), conditionSpinner), lp())
        form.addView(labeled(getString(R.string.opp_status), statusSp), lp())
        form.addView(labeled(getString(R.string.notes), notes), lp())
        formDialog(getString(R.string.inv_add), form, getString(R.string.action_save)) {
            val t = titleIn.text.toString().trim()
            if (t.isEmpty()) {
                titleIn.error = getString(R.string.err_required)
                return@formDialog false
            }
            val q = optionalInt(qty, 1..100000) ?: return@formDialog false
            val u = requiredMoney(unit) ?: return@formDialog false
            val e = moneyOrZero(extra) ?: return@formDialog false
            val tg = optionalMoney(target) ?: return@formDialog false
            val body = InventoryIn(
                title = t,
                quantity = q.value ?: 1,
                unitCostCents = u,
                extraCostsCents = e,
                condition = InventoryStatus.CONDITIONS.getOrElse(conditionSpinner.selectedItemPosition) { "unknown" },
                purchaseSource = source.text.toString().trim(),
                status = InventoryStatus.CREATE.getOrElse(statusSp.selectedItemPosition) { "purchased" },
                targetSaleCents = tg.value,
                notes = notes.text.toString().trim(),
            )
            call({ api -> api.createInventory(body) }, toastErrors = true) { item ->
                toast(getString(R.string.inv_added, item.title))
                reload()
            }
            true
        }
    }

    override fun onCreateOptionsMenu(menu: Menu): Boolean {
        menu.add(0, MENU_ADD, 0, R.string.inv_add)
        menu.add(0, MENU_REFRESH, 1, R.string.action_refresh)
        return true
    }

    override fun onOptionsItemSelected(item: MenuItem): Boolean = when (item.itemId) {
        MENU_ADD -> {
            showAddDialog(); true
        }
        MENU_REFRESH -> {
            reload(); true
        }
        else -> super.onOptionsItemSelected(item)
    }

    private inner class InventoryAdapter : SimpleAdapter<InventoryOut>() {
        private inner class Holder(val title: TextView, val line1: TextView, val line2: TextView, val badges: LinearLayout)

        override fun createView(parent: ViewGroup): View {
            val ctx = this@InventoryActivity
            val card = ctx.card()
            val title = ctx.text("", 15f, bold = true).clampLines(2)
            val line1 = ctx.text("", 14f)
            val line2 = ctx.muted("", 12f)
            val badges = ctx.hbox()
            card.addView(title, lp())
            card.addView(line1, lp())
            card.addView(line2, lp())
            card.addView(badges, lp().apply { topMargin = dp(4) })
            val wrapper = LinearLayout(ctx).apply { orientation = LinearLayout.VERTICAL }
            wrapper.addView(card, ctx.cardLp())
            wrapper.tag = Holder(title, line1, line2, badges)
            return wrapper
        }

        override fun bind(view: View, item: InventoryOut, position: Int) {
            val h = view.tag as Holder
            val ctx = this@InventoryActivity
            h.title.text = item.title
            h.line1.text = getString(R.string.inv_row_line1, item.quantityAvailable, item.quantity, Money.formatCents(item.totalCostCents))
            h.line2.text = getString(
                R.string.inv_row_line2, item.daysHeld, Money.formatCentsOrDash(item.targetSaleCents), Money.formatSignedCents(item.realizedProfitCents),
            )
            h.badges.removeAllViews()
            h.badges.addView(ctx.badge(Labels.inventoryStatus(ctx, item.status), getColor(R.color.brand_primary)), lp(WRAP, WRAP).apply { marginEnd = dp(6) })
            h.badges.addView(ctx.badge(Labels.condition(ctx, item.condition), getColor(R.color.severity_none)), lp(WRAP, WRAP))
        }
    }
}
