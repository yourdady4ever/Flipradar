package com.flipradar.app.ui

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.view.Gravity
import android.view.Menu
import android.view.MenuItem
import android.widget.Button
import android.widget.LinearLayout
import android.widget.TextView
import com.flipradar.app.R
import com.flipradar.app.notify.PollScheduler

/** Hub screen: connection status, navigation to every feature, unread notification badge. */
class MainActivity : BaseActivity() {
    override val showUp: Boolean = false

    private lateinit var status: TextView
    private lateinit var notificationsButton: Button
    private var unread = 0

    private companion object {
        const val MENU_SETTINGS = 1
        const val MENU_REFRESH = 2
        const val REQ_NOTIFICATIONS = 42
        const val UNREAD_PAGE = 50
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        if (!settings.isConfigured) {
            startActivity(Intent(this, SetupActivity::class.java))
            finish()
            return
        }
        title = getString(R.string.app_name)
        val root = vbox(16)
        status = muted("")
        root.addView(status, lp())

        val entries = listOf(
            R.string.nav_opportunities to OpportunitiesActivity::class.java,
            R.string.nav_calculator to CalculatorActivity::class.java,
            R.string.nav_inventory to InventoryActivity::class.java,
            R.string.nav_dashboard to DashboardActivity::class.java,
            R.string.nav_searches to SearchesActivity::class.java,
            R.string.nav_alerts to AlertsActivity::class.java,
            R.string.nav_notifications to NotificationsActivity::class.java,
            R.string.nav_settings to SetupActivity::class.java,
        )
        var row: LinearLayout? = null
        entries.forEachIndexed { i, (label, cls) ->
            if (i % 2 == 0) {
                row = hbox()
                root.addView(row, lp().apply { topMargin = dp(8) })
            }
            val b = button(getString(label)) { startActivity(Intent(this, cls)) }.apply {
                minHeight = dp(72)
                gravity = Gravity.CENTER
            }
            if (cls == NotificationsActivity::class.java) notificationsButton = b
            row!!.addView(b, lp(0, WRAP, 1f))
        }
        root.addView(muted(getString(R.string.main_footer), 12f), lp().apply { topMargin = dp(24) })
        setScreen(root, scrollable = true)

        PollScheduler.schedule(this)
        maybeRequestNotificationPermission()
    }

    override fun onResume() {
        super.onResume()
        if (settings.isConfigured && ::status.isInitialized) refresh()
    }

    private fun refresh() {
        status.text = getString(R.string.main_connecting, settings.serverUrl ?: "")
        call({ api ->
            val health = api.health()
            val count = api.notifications(unreadOnly = true, limit = UNREAD_PAGE).size
            health to count
        }, onError = { e ->
            status.text = getString(R.string.main_offline, settings.serverUrl ?: "") + "\n" + Labels.error(this, e)
        }) { (health, count) ->
            unread = count
            status.text = getString(R.string.main_connected, settings.serverUrl ?: "", health.version, health.status)
            updateBadge()
        }
    }

    private fun updateBadge() {
        val base = getString(R.string.nav_notifications)
        notificationsButton.text = when {
            unread <= 0 -> base
            unread >= UNREAD_PAGE -> getString(R.string.nav_notifications_badge, "$UNREAD_PAGE+")
            else -> getString(R.string.nav_notifications_badge, unread.toString())
        }
    }

    private fun maybeRequestNotificationPermission() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU) return
        if (checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED) return
        if (settings.pollIntervalMinutes == 0 || settings.notificationPermissionAsked) return
        settings.notificationPermissionAsked = true
        requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), REQ_NOTIFICATIONS)
    }

    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode == REQ_NOTIFICATIONS && grantResults.firstOrNull() != PackageManager.PERMISSION_GRANTED) {
            toast(getString(R.string.main_notifications_denied))
        }
    }

    override fun onCreateOptionsMenu(menu: Menu): Boolean {
        menu.add(0, MENU_REFRESH, 0, R.string.action_refresh)
        menu.add(0, MENU_SETTINGS, 1, R.string.nav_settings)
        return true
    }

    override fun onOptionsItemSelected(item: MenuItem): Boolean = when (item.itemId) {
        MENU_REFRESH -> {
            refresh(); true
        }
        MENU_SETTINGS -> {
            startActivity(Intent(this, SetupActivity::class.java)); true
        }
        else -> super.onOptionsItemSelected(item)
    }
}
