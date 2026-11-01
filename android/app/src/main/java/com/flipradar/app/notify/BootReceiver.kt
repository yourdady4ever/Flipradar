package com.flipradar.app.notify

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent

/** Re-schedules the notification poll after a reboot or an app update. */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        when (intent.action) {
            Intent.ACTION_BOOT_COMPLETED, Intent.ACTION_MY_PACKAGE_REPLACED -> PollScheduler.schedule(context)
        }
    }
}
