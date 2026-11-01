package com.flipradar.app.ui

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Regression tests for review finding android#4: "Load more" built the next page from the current,
 * unapplied filter widgets and mixed pages of different queries.
 */
class PagerTest {
    private data class Query(val sort: String, val q: String? = null)

    @Test
    fun loadMoreContinuesTheAppliedQuery() {
        val pager = Pager<Query>(30)
        val first = pager.start(Query("score"))
        assertTrue(pager.onPage(first, 30, 120))
        // The user now edits the filters (sort = recent, a search term) but does not tap Apply.
        val more = pager.next()
        assertNotNull(more)
        assertEquals(Query("score"), more!!.query)
        assertEquals(30, more.offset)
        assertTrue(more.append)
        assertTrue(pager.onPage(more, 30, 120))
        assertEquals(60, pager.loaded)
        assertEquals(120L, pager.total)
    }

    @Test
    fun pageOfAnOlderQueryIsDropped() {
        val pager = Pager<Query>(30)
        val old = pager.start(Query("score"))
        val applied = pager.start(Query("recent", "switch"))
        assertFalse(pager.onPage(old, 30, 120))
        assertTrue(pager.onPage(applied, 7, 7))
        assertEquals(7, pager.loaded)
        assertEquals(7L, pager.total)
        assertEquals(Query("recent", "switch"), pager.query)
    }

    @Test
    fun noNextPageWhileLoadingOrWhenEverythingIsLoaded() {
        val pager = Pager<Query>(30)
        assertNull(pager.next())
        val first = pager.start(Query("score"))
        assertNull("first page still loading", pager.next())
        pager.onPage(first, 12, 12)
        assertFalse(pager.hasMore)
        assertNull(pager.next())
    }

    @Test
    fun failedPageCanBeRequestedAgain() {
        val pager = Pager<Query>(30)
        pager.onPage(pager.start(Query("score")), 30, 50)
        val more = pager.next()!!
        assertNull(pager.next())
        pager.onError(more)
        val retry = pager.next()!!
        assertEquals(30, retry.offset)
        assertEquals(Query("score"), retry.query)
    }
}
