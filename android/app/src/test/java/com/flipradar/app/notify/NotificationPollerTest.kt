package com.flipradar.app.notify

import com.flipradar.app.data.ApiException
import com.flipradar.app.data.NotificationCursor
import com.flipradar.app.data.NotificationOut
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.Instant

/**
 * Regression tests for the background notification poll (review findings android#0 and android#1).
 */
class NotificationPollerTest {

    /**
     * In-memory stand-in for GET /api/v1/notifications with the backend's semantics: newest first,
     * `id > since_id`, optional unread filter, and SQLite rowids without AUTOINCREMENT (a new row gets
     * max(id) + 1, so ids are reused after the newest rows are deleted).
     */
    private class FakeServer {
        val rows = ArrayList<NotificationOut>()
        private var clock = Instant.parse("2026-11-01T10:00:00Z")

        fun add(title: String): NotificationOut {
            val id = (rows.maxOfOrNull { it.id } ?: 0L) + 1
            clock = clock.plusSeconds(60)
            return NotificationOut(id, 1, id, "opportunity", title, "body", clock.toString(), null).also { rows += it }
        }

        fun markRead(id: Long) {
            val i = rows.indexOfFirst { it.id == id }
            rows[i] = rows[i].copy(readAt = clock.toString())
        }

        val fetch = NotificationPoller.Fetch { unreadOnly, sinceId, limit ->
            rows.filter { it.id > sinceId && (!unreadOnly || !it.isRead) }.sortedByDescending { it.id }.take(limit)
        }
    }

    private class Run(val posted: List<Long>, val skipped: Int, val cursor: NotificationCursor)

    /** One run of NotificationPollJob: post, then advance the cursor past everything unseen. */
    private fun poll(server: FakeServer, cursor: NotificationCursor): Run {
        val o = NotificationPoller.poll(cursor, server.fetch)
        return Run(o.toPost.map { it.id }, o.skipped, cursor.advancedTo(o.unseen))
    }

    /** What NotificationsActivity does after listing notifications (since_id=0). */
    private fun listInApp(server: FakeServer, cursor: NotificationCursor) =
        cursor.advancedTo(server.fetch.notifications(unreadOnly = false, sinceId = 0, limit = 100))

    @Test
    fun reusedIdsAfterClearDemoAreStillPosted() {
        val server = FakeServer()
        repeat(12) { server.add("demo $it") }
        var cursor = listInApp(server, NotificationCursor())
        assertEquals(12L, cursor.id)

        server.rows.clear() // clear-demo deletes the demo notifications
        val real = (1..3).map { server.add("real $it") }
        assertEquals(listOf(1L, 2L, 3L), real.map { it.id }) // the server reuses ids 1..3

        val first = poll(server, cursor)
        assertEquals(listOf(1L, 2L, 3L), first.posted)
        cursor = first.cursor
        assertTrue("already posted", poll(server, cursor).posted.isEmpty())
        server.add("real 4")
        assertEquals(listOf(4L), poll(server, cursor).posted)
    }

    @Test
    fun allNewNotificationsAreFoundEvenWhenTheirIdsPassTheStaleCursor() {
        val server = FakeServer()
        repeat(12) { server.add("old $it") }
        val cursor = listInApp(server, NotificationCursor())
        server.rows.clear() // database reset / clear-demo
        repeat(15) { server.add("new $it") } // ids 1..15; an id-only cursor would only see 13..15

        val run = poll(server, cursor)
        assertEquals((11L..15L).toList(), run.posted)
        assertEquals(10, run.skipped)
        assertEquals(15L, run.cursor.id)
    }

    @Test
    fun olderNotificationsLeftAfterTheNewestRowsAreDeletedAreNotPostedAgain() {
        val server = FakeServer()
        repeat(5) { server.add("real $it") } // ids 1..5, unread, already posted
        var cursor = poll(server, NotificationCursor()).cursor
        repeat(7) { server.add("demo $it") } // ids 6..12
        cursor = listInApp(server, cursor)
        assertEquals(12L, cursor.id)

        server.rows.removeAll { it.id > 5 } // clear-demo removes the newest rows: the server's newest id is now 5
        assertTrue(poll(server, cursor).posted.isEmpty())
        val next = server.add("real after clear")
        assertEquals(6L, next.id)
        assertEquals(listOf(6L), poll(server, cursor).posted)
    }

    @Test
    fun readNotificationsAreNotPosted() {
        val server = FakeServer()
        server.add("a")
        server.add("b")
        server.markRead(2)
        assertEquals(listOf(1L), poll(server, NotificationCursor()).posted)
    }

    @Test
    fun cursorNeverMovesBackwards() {
        // The poll job's (older) result must not lower a cursor the Notifications screen raised meanwhile.
        val raised = NotificationCursor(20, "2026-11-01T12:00:00Z")
        val older = NotificationOut(15, null, 15, "opportunity", "t", "b", "2026-11-01T11:00:00Z", null)
        assertEquals(raised, raised.advancedTo(listOf(older)))
        // After an id reset the newer notification has a lower id and still moves the cursor.
        val afterReset = NotificationOut(2, null, 2, "opportunity", "t", "b", "2026-11-01T13:00:00Z", null)
        assertEquals(NotificationCursor(2, "2026-11-01T13:00:00Z"), raised.advancedTo(listOf(afterReset)))
    }

    @Test
    fun equalTimestampsAreOrderedById() {
        val cursor = NotificationCursor(7, "2026-11-01T12:00:00.123456Z")
        assertTrue(cursor.isBefore(8, "2026-11-01T12:00:00.123456Z"))
        assertFalse(cursor.isBefore(6, "2026-11-01T12:00:00.123456Z"))
        assertFalse(cursor.isBefore(7, "2026-11-01T12:00:00.123456Z"))
    }

    @Test
    fun withoutTimestampsIdsAreCompared() {
        assertTrue(NotificationCursor(12).isBefore(13, "2026-11-01T12:00:00Z"))
        assertFalse(NotificationCursor(12).isBefore(3, "2026-11-01T12:00:00Z"))
        assertTrue(NotificationCursor(12, "2026-11-01T12:00:00Z").isBefore(13, null))
        assertFalse(NotificationCursor(12, "not a date").isBefore(3, "2026-11-02T12:00:00Z"))
    }

    @Test
    fun pollAsksForTheNewestNotificationsNotForIdsAboveTheCursor() {
        val asked = ArrayList<Long>()
        NotificationPoller.poll(NotificationCursor(12, "2026-11-01T12:00:00Z")) { _, sinceId, _ ->
            asked += sinceId
            emptyList()
        }
        assertEquals(listOf(0L), asked)
    }

    @Test
    fun failedPollKeepsThePeriodicScheduleInsteadOfBackingOff() {
        // android#1: jobFinished(params, true) after a failure switches the periodic job to exponential
        // backoff (up to 5 h) until a poll succeeds.
        val outcome = NotificationPoller.poll(NotificationCursor()) { _, _, _ ->
            throw ApiException(ApiException.Kind.NETWORK, "No route to host")
        }
        assertNotNull(outcome.error)
        assertTrue(outcome.toPost.isEmpty())
        assertFalse(outcome.wantsReschedule)
        assertFalse(NotificationPoller.poll(NotificationCursor()) { _, _, _ -> emptyList() }.wantsReschedule)
    }
}
