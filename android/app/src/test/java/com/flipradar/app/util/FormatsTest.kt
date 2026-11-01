package com.flipradar.app.util

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Test
import java.time.ZoneId

class FormatsTest {
    private val mx = ZoneId.of("America/Mexico_City") // UTC-6 all year since 2022 (no DST)
    private val utc = ZoneId.of("UTC")

    @Test
    fun formatsServerTimestampsInLocalZone() {
        assertEquals("2026-10-31 12:05", Formats.dateTime("2026-10-31T18:05:00Z", mx))
        assertEquals("2026-10-31 12:05", Formats.dateTime("2026-10-31T18:05:00+00:00", mx))
        assertEquals("2026-10-31 12:05", Formats.dateTime("2026-10-31T18:05:00.123456Z", mx))
        // Naive timestamps are treated as UTC.
        assertEquals("2026-10-31 18:05", Formats.dateTime("2026-10-31T18:05:00", utc))
        assertEquals("2026-10-31", Formats.date("2026-10-31T18:05:00Z", utc))
        assertEquals(Money.DASH, Formats.dateTime(null))
        assertEquals("garbage", Formats.dateTime("garbage"))
    }

    @Test
    fun parsesInstants() {
        assertNotNull(Formats.parseInstant("2026-11-01"))
        assertNull(Formats.parseInstant(""))
        assertNull(Formats.parseInstant("yesterday"))
    }

    @Test
    fun formatsChartPeriodsAndScores() {
        assertEquals("10-05", Formats.shortPeriod("2026-10-05"))
        assertEquals("W40", Formats.shortPeriod("W40"))
        assertEquals("", Formats.shortPeriod(null))
        assertEquals("78.5", Formats.score(78.46))
        assertEquals("100.0", Formats.score(100.0))
        assertEquals("0.0", Formats.score(0.0))
        assertEquals("-2.5", Formats.score(-2.5))
        assertEquals(Money.DASH, Formats.score(null))
        assertEquals(Money.DASH, Formats.score(Double.NaN))
        assertEquals("87%", Formats.fractionPercent(0.8734))
    }

    @Test
    fun humanizesKeysAndValues() {
        assertEquals("In transit", Formats.humanize("in_transit"))
        assertEquals(Money.DASH, Formats.humanize(null))
        assertEquals("a: 1, b: x, y", Formats.anyValue(linkedMapOf("a" to 1L, "b" to listOf("x", "y"))))
        assertEquals("3", Formats.anyValue(3.0))
        assertEquals("0.75", Formats.anyValue(0.75))
        assertEquals(Money.DASH, Formats.anyValue(null))
    }
}
