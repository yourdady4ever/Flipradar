package com.flipradar.app.util

/**
 * Exact money / percentage formatting and parsing.
 *
 * The backend sends money as integer centavos (`*_cents`) and percentages as integer basis points
 * (`*_bp`, 1 bp = 0.01 %).  Everything here is integer/string arithmetic - no floating point - so the
 * app never displays a rounding artefact the server did not compute.
 *
 * Output follows es-MX conventions: `$12,345.67` (comma grouping, period decimal separator).
 */
object Money {
    const val DASH = "—"

    /** Maximum integer digits accepted when parsing (keeps `pesos * 100` far below Long.MAX_VALUE). */
    private const val MAX_INTEGER_DIGITS = 15

    /** Groups an unsigned decimal digit string in thousands: "1234567" -> "1,234,567". */
    fun groupThousands(digits: String): String {
        if (digits.length <= 3) return digits
        val sb = StringBuilder(digits.length + digits.length / 3)
        val first = digits.length % 3
        if (first > 0) sb.append(digits, 0, first)
        var i = first
        while (i < digits.length) {
            if (sb.isNotEmpty()) sb.append(',')
            sb.append(digits, i, i + 3)
            i += 3
        }
        return sb.toString()
    }

    /** 1234567 centavos -> "$12,345.67"; -50 -> "-$0.50".  Works for the full Long range. */
    fun formatCents(cents: Long): String {
        val negative = cents < 0
        // toString() + removePrefix avoids overflow for Long.MIN_VALUE (abs() would overflow).
        val digits = cents.toString().removePrefix("-").padStart(3, '0')
        val intPart = digits.substring(0, digits.length - 2)
        val frac = digits.substring(digits.length - 2)
        return (if (negative) "-$" else "$") + groupThousands(intPart) + "." + frac
    }

    fun formatCents(cents: Int): String = formatCents(cents.toLong())

    /** Nullable variant: null -> em dash. */
    fun formatCentsOrDash(cents: Long?): String = if (cents == null) DASH else formatCents(cents)

    /** Formats with an explicit sign for profits: 1500 -> "+$15.00", -1500 -> "-$15.00", 0 -> "$0.00". */
    fun formatSignedCents(cents: Long): String = if (cents > 0) "+" + formatCents(cents) else formatCents(cents)

    /** 1234 bp -> "12.34%"; -50 -> "-0.50%". */
    fun formatBp(bp: Long): String {
        val negative = bp < 0
        val digits = bp.toString().removePrefix("-").padStart(3, '0')
        val intPart = digits.substring(0, digits.length - 2)
        val frac = digits.substring(digits.length - 2)
        return (if (negative) "-" else "") + groupThousands(intPart) + "." + frac + "%"
    }

    fun formatBp(bp: Int): String = formatBp(bp.toLong())

    fun formatBpOrDash(bp: Long?): String = if (bp == null) DASH else formatBp(bp)

    /**
     * Compact axis/label form, rounded half away from zero:
     * 35_000 cents -> "$350", 123_456 cents -> "$1.2k", 250_000_000 cents -> "$2.5M".
     */
    fun formatCompactCents(cents: Long): String {
        val negative = cents < 0
        val absCents = if (cents == Long.MIN_VALUE) Long.MAX_VALUE else kotlin.math.abs(cents)
        val pesos = (absCents + 50) / 100 // rounded to whole pesos
        val body = when {
            pesos < 1_000 -> "$$pesos"
            pesos < 1_000_000 -> "$" + oneDecimal(pesos, 1_000) + "k"
            else -> "$" + oneDecimal(pesos, 1_000_000) + "M"
        }
        return if (negative && pesos != 0L) "-$body" else body
    }

    /** value / divisor with one decimal, rounded half up, trailing ".0" removed. */
    private fun oneDecimal(value: Long, divisor: Long): String {
        val tenths = (value * 10 + divisor / 2) / divisor
        val whole = tenths / 10
        val dec = tenths % 10
        return if (dec == 0L) whole.toString() else "$whole.$dec"
    }

    private val MONEY_PATTERN = Regex("^(\\d{1,3}(?:,\\d{3})+|\\d*)(?:\\.(\\d{0,2}))?$")

    /**
     * Parses user input in pesos into centavos with exact integer math.
     *
     * Accepted: "1234", "1,234.50", "$1,234.5", " 12.05 ", ".5", "12.", "1 234.00" (spaces ignored).
     * Rejected (returns null): empty input, more than two decimals ("1.234"), malformed grouping
     * ("12,5" - ambiguous in es-MX), letters, negative values unless [allowNegative].
     */
    fun parsePesosToCents(input: String?, allowNegative: Boolean = false): Long? {
        if (input == null) return null
        var s = input.trim().replace(" ", "").replace(" ", "")
        if (s.endsWith("MXN", ignoreCase = true)) s = s.dropLast(3)
        var negative = false
        if (s.startsWith("-")) {
            negative = true
            s = s.substring(1)
        }
        if (s.startsWith("$")) s = s.substring(1)
        if (s.startsWith("-")) { // "$-5" form
            if (negative) return null
            negative = true
            s = s.substring(1)
        }
        if (negative && !allowNegative) return null
        if (s.isEmpty()) return null
        val m = MONEY_PATTERN.matchEntire(s) ?: return null
        val intDigits = m.groupValues[1].replace(",", "")
        val fracDigits = m.groupValues[2]
        if (intDigits.isEmpty() && fracDigits.isEmpty()) return null
        if (intDigits.length > MAX_INTEGER_DIGITS) return null
        val pesos = if (intDigits.isEmpty()) 0L else intDigits.toLong()
        val centavos = fracDigits.padEnd(2, '0').toLong()
        val total = pesos * 100 + centavos
        return if (negative) -total else total
    }

    private val PERCENT_PATTERN = Regex("^(\\d{1,7})?(?:\\.(\\d{0,2}))?$")

    /** "25" -> 2500 bp, "12.5%" -> 1250, "0.05" -> 5.  More than two decimals or negatives -> null. */
    fun parsePercentToBp(input: String?): Long? {
        if (input == null) return null
        var s = input.trim().replace(" ", "")
        if (s.endsWith("%")) s = s.dropLast(1)
        if (s.isEmpty()) return null
        val m = PERCENT_PATTERN.matchEntire(s) ?: return null
        val intDigits = m.groupValues[1]
        val fracDigits = m.groupValues[2]
        if (intDigits.isEmpty() && fracDigits.isEmpty()) return null
        val whole = if (intDigits.isEmpty()) 0L else intDigits.toLong()
        return whole * 100 + fracDigits.padEnd(2, '0').toLong()
    }

    /** Centavos -> plain editable text without grouping: 123450 -> "1234.50". */
    fun centsToInput(cents: Long): String {
        val negative = cents < 0
        val digits = cents.toString().removePrefix("-").padStart(3, '0')
        return (if (negative) "-" else "") + digits.substring(0, digits.length - 2) + "." + digits.substring(digits.length - 2)
    }

    /** Basis points -> plain editable percent text: 2500 -> "25", 1250 -> "12.5", 5 -> "0.05". */
    fun bpToInput(bp: Long): String {
        val negative = bp < 0
        val digits = bp.toString().removePrefix("-").padStart(3, '0')
        val intPart = digits.substring(0, digits.length - 2)
        val frac = digits.substring(digits.length - 2).trimEnd('0')
        return (if (negative) "-" else "") + intPart + if (frac.isEmpty()) "" else ".$frac"
    }
}
