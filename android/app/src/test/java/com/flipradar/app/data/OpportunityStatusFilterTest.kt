package com.flipradar.app.data

import com.flipradar.app.util.ServerUrl
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Regression test for review finding android#5: "Any status" sent no status parameter, so the server
 * applied its default open set and hid dismissed and expired opportunities.
 */
class OpportunityStatusFilterTest {

    private fun url(status: String?) = ServerUrl.buildUrl(
        "http://192.168.1.20:8000", "/api/v1/opportunities", OpportunityQuery(status = status).toParams(30, 0),
    )

    @Test
    fun anyStatusAsksTheServerForEveryStatus() {
        assertTrue(OpportunityOut.STATUS_ALL in OpportunityOut.STATUS_FILTERS)
        assertTrue(url(OpportunityOut.STATUS_ALL).contains("status=all"))
    }

    @Test
    fun defaultEntryIsTheServersOpenSetAndEachStatusIsOffered() {
        assertNull(OpportunityOut.STATUS_FILTERS.first())
        assertTrue(!url(null).contains("status="))
        assertEquals(listOf(null, "all") + OpportunityOut.STATUSES, OpportunityOut.STATUS_FILTERS)
        assertTrue(url("dismissed").contains("status=dismissed"))
    }
}
