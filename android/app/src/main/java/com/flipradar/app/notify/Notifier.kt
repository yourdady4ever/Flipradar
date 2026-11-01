package com.flipradar.app.notify

import android.Manifest
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.TaskStackBuilder
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import com.flipradar.app.R
import com.flipradar.app.data.NotificationOut
import com.flipradar.app.ui.NotificationsActivity
import com.flipradar.app.ui.OpportunityDetailActivity

/** Creates the notification channel and posts system notifications for server notifications. */
object Notifier {
    const val CHANNEL_OPPORTUNITIES = "opportunities"
    private const val SUMMARY_ID = 1

    fun ensureChannel(context: Context) {
        val nm = context.getSystemService(NotificationManager::class.java) ?: return
        if (nm.getNotificationChannel(CHANNEL_OPPORTUNITIES) != null) return
        val channel = NotificationChannel(
            CHANNEL_OPPORTUNITIES,
            context.getString(R.string.channel_opportunities),
            NotificationManager.IMPORTANCE_DEFAULT,
        ).apply { description = context.getString(R.string.channel_opportunities_desc) }
        nm.createNotificationChannel(channel)
    }

    fun canPost(context: Context): Boolean {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            context.checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
        ) return false
        return context.getSystemService(NotificationManager::class.java)?.areNotificationsEnabled() == true
    }

    /** Posts one notification per item (oldest first) plus a summary line when [skipped] > 0. */
    fun post(context: Context, items: List<NotificationOut>, skipped: Int) {
        if (!canPost(context)) return
        ensureChannel(context)
        val nm = context.getSystemService(NotificationManager::class.java) ?: return
        for (n in items) {
            val notification = Notification.Builder(context, CHANNEL_OPPORTUNITIES)
                .setSmallIcon(R.drawable.ic_stat_radar)
                .setContentTitle(n.title.ifBlank { context.getString(R.string.app_name) })
                .setContentText(n.body)
                .setStyle(Notification.BigTextStyle().bigText(n.body))
                .setAutoCancel(true)
                .setCategory(Notification.CATEGORY_RECOMMENDATION)
                .setContentIntent(intentFor(context, n))
                .build()
            // Notification ids are server ids; collisions only matter after 2^31 notifications.
            nm.notify((n.id % Int.MAX_VALUE).toInt() + SUMMARY_ID + 1, notification)
        }
        if (skipped > 0) {
            val open = TaskStackBuilder.create(context)
                .addNextIntentWithParentStack(Intent(context, NotificationsActivity::class.java))
                .getPendingIntent(SUMMARY_ID, PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
            val summary = Notification.Builder(context, CHANNEL_OPPORTUNITIES)
                .setSmallIcon(R.drawable.ic_stat_radar)
                .setContentTitle(context.getString(R.string.app_name))
                .setContentText(context.getString(R.string.notif_more_unread, skipped))
                .setAutoCancel(true)
                .setContentIntent(open)
                .build()
            nm.notify(SUMMARY_ID, summary)
        }
    }

    private fun intentFor(context: Context, n: NotificationOut): PendingIntent {
        val oid = n.opportunityId
        val intent = if (oid != null && oid > 0) {
            Intent(context, OpportunityDetailActivity::class.java)
                .putExtra(OpportunityDetailActivity.EXTRA_ID, oid)
                .putExtra(OpportunityDetailActivity.EXTRA_NOTIFICATION_ID, n.id)
        } else {
            Intent(context, NotificationsActivity::class.java)
        }
        return TaskStackBuilder.create(context)
            .addNextIntentWithParentStack(intent)
            .getPendingIntent((n.id % Int.MAX_VALUE).toInt(), PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
    }
}
