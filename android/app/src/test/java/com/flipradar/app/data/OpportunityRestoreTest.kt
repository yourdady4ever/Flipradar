package com.flipradar.app.data

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Regression test for review finding contracts#1: an opportunity marked bought by mistake could never be
 * reopened from the app, although the server allows it once no inventory item references it.
 */
class OpportunityRestoreTest {

    @Test
    fun boughtOffersOnlyRestoreToNew() {
        assertEquals(listOf(OpportunityOut.STATUS_RESTORE), OpportunityOut.statusActions("bought"))
        assertEquals("new", OpportunityOut.STATUS_RESTORE)
    }

    @Test
    fun openStatusesKeepWatchAndDismissExpiredAndUnknownGetNothing() {
        for (s in listOf("new", "seen", "watching", "dismissed")) {
            assertEquals(s, listOf("watching", "dismissed"), OpportunityOut.statusActions(s))
        }
        assertTrue(OpportunityOut.statusActions("expired").isEmpty())
        assertTrue(OpportunityOut.statusActions("something_else").isEmpty())
    }

    @Test
    fun only409OnABoughtOpportunityIsTheStillInInventoryRefusal() {
        val conflict = ApiException(ApiException.Kind.HTTP, "HTTP 409", 409, "an inventory item references it")
        assertTrue(OpportunityOut.isRestoreRefused("bought", conflict))
        assertFalse(OpportunityOut.isRestoreRefused("seen", conflict))
        assertFalse(OpportunityOut.isRestoreRefused("bought", ApiException(ApiException.Kind.HTTP, "HTTP 500", 500, "boom")))
        assertFalse(OpportunityOut.isRestoreRefused("bought", IllegalStateException("x")))
    }
}
