package com.flipradar.app.ui

import android.content.Intent
import android.graphics.Typeface
import android.os.Bundle
import android.view.Menu
import android.view.MenuItem
import android.view.View
import android.view.ViewGroup
import android.widget.CheckBox
import android.widget.LinearLayout
import android.widget.ListView
import android.widget.TextView
import com.flipradar.app.R
import com.flipradar.app.data.NotificationOut
import com.flipradar.app.util.Formats

/** Server-side notifications (alerts that matched): mark read, open the related opportunity. */
class NotificationsActivity : BaseActivity() {
    private val adapter = NotificationAdapter()
    private lateinit var unreadOnly: CheckBox
    private lateinit var empty: TextView

    private companion object {
        const val MENU_READ_ALL = 1
        const val MENU_REFRESH = 2
        const val LIMIT = 100
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        title = getString(R.string.nav_notifications)
        val root = vbox()
        unreadOnly = checkbox(getString(R.string.notif_unread_only)).apply {
            setPadding(dp(8), dp(8), dp(8), dp(8))
            setOnCheckedChangeListener { _, _ -> load() }
        }
        root.addView(unreadOnly, lp())
        empty = muted(getString(R.string.notif_empty), 15f).apply {
            setPadding(dp(24), dp(24), dp(24), dp(24))
            visibility = View.GONE
        }
        root.addView(empty, lp())
        val list = ListView(this).apply {
            divider = null
            setPadding(dp(8), 0, dp(8), 0)
        }
        list.adapter = adapter
        list.setOnItemClickListener { _, _, position, _ -> open(adapter.getItem(position)) }
        root.addView(list, lp(MATCH, 0, 1f))
        root.addView(muted(getString(R.string.notif_footer, LIMIT), 11f).apply { setPadding(dp(12), dp(4), dp(12), dp(8)) }, lp())
        setScreen(root)
    }

    override fun onResume() {
        super.onResume()
        load()
    }

    private fun load() {
        call({ api -> api.notifications(unreadOnly.isChecked, 0, LIMIT) }) { items ->
            adapter.replace(items)
            empty.visibility = if (items.isEmpty()) View.VISIBLE else View.GONE
            // Everything listed has been seen in-app: don't re-post these as system notifications.
            items.maxOfOrNull { it.id }?.let { if (it > settings.lastSeenNotificationId) settings.lastSeenNotificationId = it }
        }
    }

    private fun open(n: NotificationOut) {
        if (!n.isRead) {
            call({ api -> api.markNotificationRead(n.id) }, onError = { }) {
                val idx = adapter.items.indexOfFirst { it.id == n.id }
                if (idx >= 0) {
                    adapter.items[idx] = n.copy(readAt = "read")
                    adapter.notifyDataSetChanged()
                }
            }
        }
        val oid = n.opportunityId
        if (oid != null && oid > 0) {
            startActivity(Intent(this, OpportunityDetailActivity::class.java).putExtra(OpportunityDetailActivity.EXTRA_ID, oid))
        } else {
            info(n.title, n.body)
        }
    }

    override fun onCreateOptionsMenu(menu: Menu): Boolean {
        menu.add(0, MENU_READ_ALL, 0, R.string.notif_mark_all_read)
        menu.add(0, MENU_REFRESH, 1, R.string.action_refresh)
        return true
    }

    override fun onOptionsItemSelected(item: MenuItem): Boolean = when (item.itemId) {
        MENU_READ_ALL -> {
            call({ api -> api.markAllNotificationsRead() }, toastErrors = true) { load() }
            true
        }
        MENU_REFRESH -> {
            load(); true
        }
        else -> super.onOptionsItemSelected(item)
    }

    private inner class NotificationAdapter : SimpleAdapter<NotificationOut>() {
        private inner class Holder(val title: TextView, val body: TextView, val meta: TextView)

        override fun createView(parent: ViewGroup): View {
            val ctx = this@NotificationsActivity
            val card = ctx.card()
            val title = ctx.text("", 15f)
            val body = ctx.text("", 13f).clampLines(4)
            val meta = ctx.muted("", 11f)
            card.addView(title, lp())
            card.addView(body, lp())
            card.addView(meta, lp())
            val wrapper = LinearLayout(ctx).apply { orientation = LinearLayout.VERTICAL }
            wrapper.addView(card, ctx.cardLp())
            wrapper.tag = Holder(title, body, meta)
            return wrapper
        }

        override fun bind(view: View, item: NotificationOut, position: Int) {
            val h = view.tag as Holder
            h.title.text = if (item.isRead) item.title else "● " + item.title
            h.title.setTypeface(null, if (item.isRead) Typeface.NORMAL else Typeface.BOLD)
            h.body.text = item.body
            h.meta.text = listOf(Formats.dateTime(item.createdAt), Formats.humanize(item.kind)).joinToString(" · ")
        }
    }
}
