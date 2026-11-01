package com.flipradar.app.ui

import android.widget.EditText
import com.flipradar.app.R
import com.flipradar.app.util.Money

/** Wrapper so "blank" (use the server default) is distinguishable from "invalid" (null). */
class OptionalValue<T>(val value: T?)

/** Blank -> OptionalValue(null); valid amount -> OptionalValue(cents); invalid -> null (and sets error). */
fun optionalMoney(field: EditText): OptionalValue<Long>? {
    val raw = field.text.toString().trim()
    if (raw.isEmpty()) return OptionalValue(null)
    val cents = Money.parsePesosToCents(raw)
    if (cents == null) {
        field.error = field.context.getString(R.string.err_invalid_amount)
        return null
    }
    return OptionalValue(cents)
}

/** Required amount: blank or invalid -> null (and sets error). */
fun requiredMoney(field: EditText): Long? {
    val cents = Money.parsePesosToCents(field.text.toString())
    if (cents == null) field.error = field.context.getString(R.string.err_invalid_amount)
    return cents
}

/** Blank -> 0, otherwise like [requiredMoney]. */
fun moneyOrZero(field: EditText): Long? =
    if (field.text.toString().isBlank()) 0L else requiredMoney(field)

/** Optional percent (e.g. "25" or "12.5") as basis points; invalid -> null (and sets error). */
fun optionalPercent(field: EditText): OptionalValue<Long>? {
    val raw = field.text.toString().trim()
    if (raw.isEmpty()) return OptionalValue(null)
    val bp = Money.parsePercentToBp(raw)
    if (bp == null) {
        field.error = field.context.getString(R.string.err_invalid_percent)
        return null
    }
    return OptionalValue(bp)
}

/** Optional integer within [range]; invalid -> null (and sets error). */
fun optionalInt(field: EditText, range: IntRange): OptionalValue<Int>? {
    val raw = field.text.toString().trim()
    if (raw.isEmpty()) return OptionalValue(null)
    val v = raw.toIntOrNull()
    if (v == null || v !in range) {
        field.error = field.context.getString(R.string.err_out_of_range, range.first, range.last)
        return null
    }
    return OptionalValue(v)
}
