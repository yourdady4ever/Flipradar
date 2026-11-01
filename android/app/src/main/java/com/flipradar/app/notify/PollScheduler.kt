package com.flipradar.app.notify

import android.app.job.JobInfo
import android.app.job.JobScheduler
import android.content.ComponentName
import android.content.Context
import com.flipradar.app.data.Settings

/** Schedules / cancels the periodic notification poll according to the user's settings. */
object PollScheduler {
    const val JOB_ID = 4201
    /** JobScheduler never runs periodic jobs more often than every 15 minutes. */
    private const val MIN_MINUTES = 15L

    fun schedule(context: Context) {
        val settings = Settings(context)
        val scheduler = context.getSystemService(JobScheduler::class.java) ?: return
        val minutes = settings.pollIntervalMinutes
        if (!settings.isConfigured || minutes <= 0) {
            scheduler.cancel(JOB_ID)
            return
        }
        val intervalMs = maxOf(MIN_MINUTES, minutes.toLong()) * 60_000L
        val existing = scheduler.getPendingJob(JOB_ID)
        if (existing != null && existing.intervalMillis == intervalMs) return // already scheduled as wanted
        val job = JobInfo.Builder(JOB_ID, ComponentName(context, NotificationPollJob::class.java))
            .setRequiredNetworkType(JobInfo.NETWORK_TYPE_ANY)
            .setPeriodic(intervalMs)
            .setPersisted(true) // survives reboots (needs RECEIVE_BOOT_COMPLETED); BootReceiver re-checks too
            .build()
        scheduler.schedule(job)
    }
}
