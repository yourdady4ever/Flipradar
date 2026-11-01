package com.flipradar.app.data

import org.json.JSONArray
import org.json.JSONObject

/*
 * Lenient JSON accessors that behave identically on Android's org.json and the reference org.json
 * library used by JVM unit tests.  Notable difference they paper over: Android's optString() returns
 * the literal "null" for JSON null values.  Missing or null fields map to Kotlin null / defaults so a
 * partial server response never crashes the UI.
 */

internal fun JSONObject.rawOrNull(key: String): Any? = if (isNull(key)) null else opt(key)

fun JSONObject.strOrNull(key: String): String? = rawOrNull(key)?.toString()

fun JSONObject.str(key: String, default: String = ""): String = strOrNull(key) ?: default

fun JSONObject.longOrNull(key: String): Long? = when (val v = rawOrNull(key)) {
    is Number -> v.toLong()
    is String -> v.trim().toLongOrNull() ?: v.trim().toDoubleOrNull()?.toLong()
    else -> null
}

fun JSONObject.long(key: String, default: Long = 0L): Long = longOrNull(key) ?: default

fun JSONObject.intOrNull(key: String): Int? = longOrNull(key)?.toInt()

fun JSONObject.int(key: String, default: Int = 0): Int = intOrNull(key) ?: default

fun JSONObject.doubleOrNull(key: String): Double? = when (val v = rawOrNull(key)) {
    is Number -> v.toDouble()
    is String -> v.trim().toDoubleOrNull()
    else -> null
}

fun JSONObject.double(key: String, default: Double = 0.0): Double = doubleOrNull(key) ?: default

fun JSONObject.boolOrNull(key: String): Boolean? = when (val v = rawOrNull(key)) {
    is Boolean -> v
    is String -> when (v.trim().lowercase()) {
        "true", "1", "yes" -> true
        "false", "0", "no" -> false
        else -> null
    }
    is Number -> v.toInt() != 0
    else -> null
}

fun JSONObject.bool(key: String, default: Boolean = false): Boolean = boolOrNull(key) ?: default

fun JSONObject.objOrNull(key: String): JSONObject? = rawOrNull(key) as? JSONObject

fun JSONObject.obj(key: String): JSONObject = objOrNull(key) ?: JSONObject()

fun JSONObject.arr(key: String): JSONArray = (rawOrNull(key) as? JSONArray) ?: JSONArray()

/** Maps every JSON object element of the array (non-objects are skipped). */
inline fun <T> JSONArray.mapObjects(transform: (JSONObject) -> T): List<T> {
    val out = ArrayList<T>(length())
    for (i in 0 until length()) {
        val o = optJSONObject(i) ?: continue
        out.add(transform(o))
    }
    return out
}

fun JSONArray.strings(): List<String> {
    val out = ArrayList<String>(length())
    for (i in 0 until length()) {
        if (isNull(i)) continue
        out.add(opt(i).toString())
    }
    return out
}

fun JSONArray.longs(): List<Long> {
    val out = ArrayList<Long>(length())
    for (i in 0 until length()) {
        when (val v = opt(i)) {
            is Number -> out.add(v.toLong())
            is String -> v.toLongOrNull()?.let { out.add(it) }
        }
    }
    return out
}

/** Converts org.json values into plain Kotlin values: JSONObject -> Map, JSONArray -> List, NULL -> null. */
fun jsonToKotlin(value: Any?): Any? = when (value) {
    null -> null
    JSONObject.NULL -> null
    is JSONObject -> {
        val map = LinkedHashMap<String, Any?>()
        val keys = value.keys()
        while (keys.hasNext()) {
            val k = keys.next()
            map[k] = jsonToKotlin(value.opt(k))
        }
        map
    }
    is JSONArray -> (0 until value.length()).map { jsonToKotlin(value.opt(it)) }
    is Number -> {
        // Normalise BigDecimal/BigInteger (newer org.json) to Long/Double for uniform display.
        val d = value.toDouble()
        if (value is Int || value is Long || value is java.math.BigInteger) value.toLong()
        else if (value is java.math.BigDecimal && value.scale() <= 0 && value.abs() < java.math.BigDecimal(Long.MAX_VALUE)) value.toLong()
        else d
    }
    else -> value
}

@Suppress("UNCHECKED_CAST")
fun JSONObject.mapOrEmpty(key: String): Map<String, Any?> =
    (jsonToKotlin(objOrNull(key)) as? Map<String, Any?>) ?: emptyMap()

/** dict[str, int] fields such as `aging` or `opportunities_by_source`. */
fun JSONObject.longMap(key: String): Map<String, Long> {
    val o = objOrNull(key) ?: return emptyMap()
    val out = LinkedHashMap<String, Long>()
    val keys = o.keys()
    while (keys.hasNext()) {
        val k = keys.next()
        o.longOrNull(k)?.let { out[k] = it }
    }
    return out
}

@Suppress("UNCHECKED_CAST")
fun JSONObject.listOfMaps(key: String): List<Map<String, Any?>> =
    arr(key).let { a -> (0 until a.length()).mapNotNull { jsonToKotlin(a.opt(it)) as? Map<String, Any?> } }

/** Builder helper: puts only non-null values (pydantic then applies its own defaults). */
fun JSONObject.putIfNotNull(key: String, value: Any?): JSONObject {
    if (value != null) put(key, value)
    return this
}
