package com.flipradar.app.util

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class MoneyTest {

    @Test
    fun formatsCentsWithEsMxGrouping() {
        assertEquals("$0.00", Money.formatCents(0))
        assertEquals("$0.05", Money.formatCents(5))
        assertEquals("$0.99", Money.formatCents(99))
        assertEquals("$1.00", Money.formatCents(100))
        assertEquals("$999.99", Money.formatCents(99_999))
        assertEquals("$1,000.00", Money.formatCents(100_000))
        assertEquals("$12,345.67", Money.formatCents(1_234_567))
        assertEquals("$1,234,567.89", Money.formatCents(123_456_789))
    }

    @Test
    fun formatsNegativeCents() {
        assertEquals("-$0.50", Money.formatCents(-50))
        assertEquals("-$1,234,567.89", Money.formatCents(-123_456_789))
    }

    @Test
    fun formatsExtremeLongValuesWithoutOverflow() {
        assertEquals("$92,233,720,368,547,758.07", Money.formatCents(Long.MAX_VALUE))
        assertEquals("-$92,233,720,368,547,758.08", Money.formatCents(Long.MIN_VALUE))
    }

    @Test
    fun formatsNullableAndSigned() {
        assertEquals(Money.DASH, Money.formatCentsOrDash(null))
        assertEquals("$10.00", Money.formatCentsOrDash(1000))
        assertEquals("+$15.00", Money.formatSignedCents(1500))
        assertEquals("-$15.00", Money.formatSignedCents(-1500))
        assertEquals("$0.00", Money.formatSignedCents(0))
    }

    @Test
    fun formatsBasisPoints() {
        assertEquals("12.34%", Money.formatBp(1234))
        assertEquals("16.00%", Money.formatBp(1600))
        assertEquals("0.05%", Money.formatBp(5))
        assertEquals("0.00%", Money.formatBp(0))
        assertEquals("-0.50%", Money.formatBp(-50))
        assertEquals("1,600.00%", Money.formatBp(160_000))
        assertEquals(Money.DASH, Money.formatBpOrDash(null))
    }

    @Test
    fun formatsCompactAxisLabels() {
        assertEquals("$0", Money.formatCompactCents(0))
        assertEquals("$0", Money.formatCompactCents(-49)) // rounds to zero pesos: no sign
        assertEquals("$350", Money.formatCompactCents(35_000))
        assertEquals("$999", Money.formatCompactCents(99_900))
        assertEquals("$1k", Money.formatCompactCents(100_000))
        assertEquals("$1.2k", Money.formatCompactCents(123_456))
        assertEquals("$1.3k", Money.formatCompactCents(125_000)) // half up
        assertEquals("-$1.2k", Money.formatCompactCents(-123_456))
        assertEquals("$2.5M", Money.formatCompactCents(250_000_000))
    }

    @Test
    fun parsesValidPesoInput() {
        assertEquals(123_450L, Money.parsePesosToCents("1,234.50"))
        assertEquals(123_450L, Money.parsePesosToCents("1234.5"))
        assertEquals(123_400L, Money.parsePesosToCents("1234"))
        assertEquals(123_400L, Money.parsePesosToCents(" $1,234 "))
        assertEquals(50L, Money.parsePesosToCents(".5"))
        assertEquals(5L, Money.parsePesosToCents("0.05"))
        assertEquals(1_200L, Money.parsePesosToCents("12."))
        assertEquals(100_000_000L, Money.parsePesosToCents("1,000,000"))
        assertEquals(123_400L, Money.parsePesosToCents("1 234.00"))
        assertEquals(123_400L, Money.parsePesosToCents("1234 MXN"))
        assertEquals(0L, Money.parsePesosToCents("0"))
    }

    @Test
    fun rejectsInvalidPesoInput() {
        assertNull(Money.parsePesosToCents(null))
        assertNull(Money.parsePesosToCents(""))
        assertNull(Money.parsePesosToCents("   "))
        assertNull(Money.parsePesosToCents("$"))
        assertNull(Money.parsePesosToCents("."))
        assertNull(Money.parsePesosToCents("abc"))
        assertNull(Money.parsePesosToCents("1.234")) // three decimals
        assertNull(Money.parsePesosToCents("12,5")) // ambiguous grouping
        assertNull(Money.parsePesosToCents("1,23,456"))
        assertNull(Money.parsePesosToCents("1.2.3"))
        assertNull(Money.parsePesosToCents("-5")) // negatives not allowed by default
        assertNull(Money.parsePesosToCents("1e5"))
        assertNull(Money.parsePesosToCents("9999999999999999")) // > 15 integer digits
    }

    @Test
    fun parsesNegativeWhenAllowed() {
        assertEquals(-150L, Money.parsePesosToCents("-1.50", allowNegative = true))
        assertEquals(-150L, Money.parsePesosToCents("-$1.50", allowNegative = true))
        assertEquals(-150L, Money.parsePesosToCents("$-1.50", allowNegative = true))
        assertNull(Money.parsePesosToCents("--1.50", allowNegative = true))
    }

    @Test
    fun parseIsExactForAllCentValues() {
        // Exhaustive round trip over 0.00 .. 2,000.00 plus some large values: no floating point drift.
        val values = (0L..200_000L) + listOf(999_999_999L, 123_456_789_012L, 99_999_999_999_999L)
        for (c in values) {
            assertEquals(c, Money.parsePesosToCents(Money.centsToInput(c)))
            assertEquals(c, Money.parsePesosToCents(Money.formatCents(c)))
        }
    }

    @Test
    fun parsesPercentToBasisPoints() {
        assertEquals(2_500L, Money.parsePercentToBp("25"))
        assertEquals(1_250L, Money.parsePercentToBp("12.5"))
        assertEquals(1_250L, Money.parsePercentToBp("12.5%"))
        assertEquals(5L, Money.parsePercentToBp("0.05"))
        assertEquals(50L, Money.parsePercentToBp(".5"))
        assertNull(Money.parsePercentToBp("12.345"))
        assertNull(Money.parsePercentToBp("-3"))
        assertNull(Money.parsePercentToBp(""))
        assertNull(Money.parsePercentToBp("%"))
        assertNull(Money.parsePercentToBp("abc"))
    }

    @Test
    fun convertsToEditableText() {
        assertEquals("1234.50", Money.centsToInput(123_450))
        assertEquals("0.05", Money.centsToInput(5))
        assertEquals("-0.05", Money.centsToInput(-5))
        assertEquals("25", Money.bpToInput(2_500))
        assertEquals("12.5", Money.bpToInput(1_250))
        assertEquals("0.05", Money.bpToInput(5))
        for (bp in 0L..20_000L) assertEquals(bp, Money.parsePercentToBp(Money.bpToInput(bp)))
    }

    @Test
    fun groupsThousands() {
        assertEquals("1", Money.groupThousands("1"))
        assertEquals("123", Money.groupThousands("123"))
        assertEquals("1,234", Money.groupThousands("1234"))
        assertEquals("123,456", Money.groupThousands("123456"))
        assertEquals("1,234,567", Money.groupThousands("1234567"))
    }
}
