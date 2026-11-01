package com.flipradar.app.notify

import android.app.job.JobParameters
import android.app.job.JobService
import com.flipradar.app.data.Settings
import java.util.concurrent.Executors

/**
 * Periodic JobScheduler job: fetches unread notifications newer than the last one seen and posts them
 * as Android notifications.  Runs its network call on a background thread; the selection logic is in
 * [NotificationPoller].
 */
class NotificationPollJob : JobService() {
    private companion object {
        val executor = Executors.newSingleThreadExecutor { r -> Thread(r, "flipradar-poll").apply { isDaemon = true } }
    }

    @Volatile
    private var stopped = false

    override fun onStartJob(params: JobParameters): Boolean {
        stopped = false
        executor.execute {
            // A failed poll is retried by the next periodic run, never by backoff (see Outcome.wantsReschedule).
            val wantsReschedule = try {
                poll()?.wantsReschedule ?: false
            } catch (e: Exception) {
                false
            }
            if (!stopped) jobFinished(params, wantsReschedule)
        }
        return true
    }

    override fun onStopJob(params: JobParameters): Boolean {
        stopped = true
        return true
    }

    private fun poll(): NotificationPoller.Outcome? {
        val settings = Settings(applicationContext)
        val server = settings.serverUrl
        val api = settings.apiClient() ?: return null
        val outcome = NotificationPoller.poll(settings.notificationCursor, api::notifications)
        // Server changed in Setup meanwhile: these notifications (and their cursor) belong to the old one.
        if (outcome.unseen.isEmpty() || stopped || settings.serverUrl != server) return outcome
        Notifier.post(applicationContext, outcome.toPost, outcome.skipped)
        settings.advanceNotificationCursor(outcome.unseen)
        return outcome
    }
}
