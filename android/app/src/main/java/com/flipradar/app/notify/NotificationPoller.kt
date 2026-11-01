package com.flipradar.app.notify

import com.flipradar.app.data.NotificationCursor
import com.flipradar.app.data.NotificationOut

/**
 * One background notification poll without Android (NotificationPollJob does the I/O around it), so the
 * selection and failure handling are unit-testable.
 */
object NotificationPoller {
    const val MAX_POSTED = 5
    const val FETCH_LIMIT = 50

    /** Fetches notifications like ApiClient.notifications(unreadOnly, sinceId, limit), newest first. */
    fun interface Fetch {
        fun notifications(unreadOnly: Boolean, sinceId: Long, limit: Int): List<NotificationOut>
    }

    class Outcome(
        /** Notifications to post now, oldest first (the newest [MAX_POSTED] of [unseen]). */
        val toPost: List<NotificationOut>,
        /** Unseen notifications not posted individually (summarised as "N more"). */
        val skipped: Int,
        /** Every unseen notification: the cursor advances past these once they are posted. */
        val unseen: List<NotificationOut>,
        /** The network or server error, if the poll failed. */
        val error: Exception?,
    ) {
        /**
         * Always false.  The job is periodic: asking JobScheduler to reschedule after a failure replaces
         * the period with exponential backoff (30 s doubling up to 5 h) that only a successful run
         * resets, so a phone that spent a few hours away from a LAN-only server would get its alerts
         * hours late.  The next periodic run is the retry.
         */
        val wantsReschedule: Boolean get() = false
    }

    /**
     * Fetches the newest unread notifications and picks those after [cursor].  It asks for since_id=0
     * rather than since_id=cursor.id because the server reuses ids after deleting its newest rows (see
     * [NotificationCursor]); new notifications always get the highest ids, so they are in the first page.
     */
    fun poll(cursor: NotificationCursor, fetch: Fetch): Outcome {
        val items = try {
            fetch.notifications(unreadOnly = true, sinceId = 0, limit = FETCH_LIMIT)
        } catch (e: Exception) {
            return Outcome(emptyList(), 0, emptyList(), e)
        }
        val unseen = cursor.unseen(items)
        val toPost = unseen.takeLast(MAX_POSTED)
        return Outcome(toPost, unseen.size - toPost.size, unseen, null)
    }
}
