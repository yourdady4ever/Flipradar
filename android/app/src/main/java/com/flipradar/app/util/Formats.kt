package com.flipradar.app.util

import java.time.Instant
import java.time.LocalDate
import java.time.LocalDateTime
import java.time.OffsetDateTime
import java.time.ZoneId
import java.time.ZoneOffset
import java.time.format.DateTimeFormatter

/** Display helpers for non-financial values (dates, scores).  Pure JVM; java.time is API 26+. */
object Formats {
    private val DATE_TIME = DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm")
    private val DATE = DateTimeFormatter.ofPattern("yyyy-MM-dd")

    /** Parses an ISO-8601 timestamp from the API.  Naive timestamps (no offset) are treated as UTC. */
    fun parseInstant(iso: String?): Instant? {
        if (iso.isNullOrBlank()) return null
        val s = iso.trim()
        return try {
            OffsetDateTime.parse(s).toInstant()
        } catch (e: Exception) {
            try {
                LocalDateTime.parse(s).toInstant(ZoneOffset.UTC)
            } catch (e2: Exception) {
                try {
                    LocalDate.parse(s).atStartOfDay().toInstant(ZoneOffset.UTC)
                } catch (e3: Exception) {
                    null
                }
            }
        }
    }

    /** "2026-10-31T18:05:00Z" -> "2026-10-31 12:05" in [zone]; unparseable input is returned unchanged. */
    fun dateTime(iso: String?, zone: ZoneId = ZoneId.systemDefault()): String {
        if (iso.isNullOrBlank()) return Money.DASH
        val instant = parseInstant(iso) ?: return iso
        return DATE_TIME.format(instant.atZone(zone))
    }

    fun date(iso: String?, zone: ZoneId = ZoneId.systemDefault()): String {
        if (iso.isNullOrBlank()) return Money.DASH
        val instant = parseInstant(iso) ?: return iso
        return DATE.format(instant.atZone(zone))
    }

    /** Chart label for a bucket start "2026-10-05" -> "10-05" (falls back to the input). */
    fun shortPeriod(period: String?): String {
        if (period.isNullOrBlank()) return ""
        val m = Regex("^\\d{4}-(\\d{2})-(\\d{2})").find(period) ?: return period
        return m.groupValues[1] + "-" + m.groupValues[2]
    }

    /** Score 0..100 with one decimal, e.g. 78.46 -> "78.5".  Display only - not used in any calculation. */
    fun score(value: Double?): String {
        if (value == null || value.isNaN() || value.isInfinite()) return Money.DASH
        val tenths = Math.round(value * 10)
        val negative = tenths < 0
        val abs = kotlin.math.abs(tenths)
        return (if (negative) "-" else "") + (abs / 10) + "." + (abs % 10)
    }

    /** 0.8734 -> "87%" for match confidences reported as fractions. */
    fun fractionPercent(value: Double?): String {
        if (value == null || value.isNaN()) return Money.DASH
        return Math.round(value * 100).toString() + "%"
    }

    /** "in_transit" -> "In transit". */
    fun humanize(key: String?): String {
        if (key.isNullOrBlank()) return Money.DASH
        val s = key.replace('_', ' ').trim()
        return s.substring(0, 1).uppercase() + s.substring(1)
    }

    /** Renders an arbitrary JSON-derived value (maps/lists from the API) as short readable text. */
    fun anyValue(value: Any?): String = when (value) {
        null -> Money.DASH
        is Map<*, *> -> value.entries.joinToString(", ") { "${it.key}: ${anyValue(it.value)}" }
        is List<*> -> value.joinToString(", ") { anyValue(it) }
        is Double -> if (value == Math.floor(value) && !value.isInfinite() && kotlin.math.abs(value) < 1e15) value.toLong().toString() else value.toString()
        else -> value.toString()
    }
}
