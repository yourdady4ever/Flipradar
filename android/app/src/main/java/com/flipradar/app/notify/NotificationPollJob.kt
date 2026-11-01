package com.flipradar.app.notify

import android.app.job.JobParameters
import android.app.job.JobService
import com.flipradar.app.data.NotificationOut
import com.flipradar.app.data.Settings
import java.util.concurrent.Executors

/**
 * Periodic JobScheduler job: fetches unread notifications newer than the last one seen and posts them
 * as Android notifications.  Runs its network call on a background thread.
 */
class NotificationPollJob : JobService() {
    private companion object {
        val executor = Executors.newSingleThreadExecutor { r -> Thread(r, "flipradar-poll").apply { isDaemon = true } }
        const val MAX_POSTED = 5
        const val FETCH_LIMIT = 50
    }

    @Volatile
    private var stopped = false

    override fun onStartJob(params: JobParameters): Boolean {
        stopped = false
        executor.execute {
            var retry = false
            try {
                poll()
            } catch (e: Exception) {
                retry = true // network or server error: let JobScheduler back off and retry
            } finally {
                if (!stopped) jobFinished(params, retry)
            }
        }
        return true
    }

    override fun onStopJob(params: JobParameters): Boolean {
        stopped = true
        return true
    }

    private fun poll() {
        val settings = Settings(applicationContext)
        val api = settings.apiClient() ?: return
        val lastSeen = settings.lastSeenNotificationId
        val items = api.notifications(unreadOnly = true, sinceId = lastSeen, limit = FETCH_LIMIT)
        val fresh = items.filter { it.id > lastSeen && !it.isRead }
        if (fresh.isEmpty() || stopped) return
        val toPost = NotificationOut.newSince(fresh, lastSeen, MAX_POSTED)
        Notifier.post(applicationContext, toPost, fresh.size - toPost.size)
        settings.lastSeenNotificationId = fresh.maxOf { it.id }
    }
}
