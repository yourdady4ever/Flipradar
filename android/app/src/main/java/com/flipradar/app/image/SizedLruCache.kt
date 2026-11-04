package com.flipradar.app.image

/**
 * Thread-safe least-recently-used cache bounded by the total [sizeOf] of its values (bytes of decoded
 * bitmaps for the image memory cache).  Pure JVM so it is unit-testable (android.util.LruCache is not
 * available to JVM tests).
 *
 * [get] refreshes an entry; [put] evicts the least recently used entries until the total fits
 * [maxSize].  A value whose size alone exceeds [maxSize] is not cached (and removes the old value of
 * its key), so one huge image can never flush the whole cache.
 */
class SizedLruCache<K : Any, V : Any>(val maxSize: Long, private val sizeOf: (V) -> Long) {
    init {
        require(maxSize > 0) { "maxSize must be positive" }
    }

    private class Entry<V>(val value: V, val size: Long)

    // accessOrder = true: iteration goes from the least to the most recently used entry.
    private val map = LinkedHashMap<K, Entry<V>>(16, 0.75f, true)
    private var total = 0L

    /** Sum of the sizes of the cached values. */
    val size: Long get() = synchronized(this) { total }

    val count: Int get() = synchronized(this) { map.size }

    fun get(key: K): V? = synchronized(this) { map[key]?.value }

    /** Caches [value]; returns false (caching nothing) when it is larger than [maxSize]. */
    fun put(key: K, value: V): Boolean {
        val s = sizeOf(value)
        require(s >= 0) { "negative size" }
        synchronized(this) {
            map.remove(key)?.let { total -= it.size }
            if (s > maxSize) return false
            map[key] = Entry(value, s)
            total += s
            trimTo(maxSize)
        }
        return true
    }

    fun remove(key: K): V? = synchronized(this) {
        map.remove(key)?.also { total -= it.size }?.value
    }

    fun clear() = synchronized(this) {
        map.clear()
        total = 0
    }

    /** Keys from the least to the most recently used (for tests and debugging). */
    fun keysEldestFirst(): List<K> = synchronized(this) { map.keys.toList() }

    private fun trimTo(max: Long) {
        val it = map.entries.iterator()
        while (total > max && it.hasNext()) {
            val eldest = it.next()
            it.remove()
            total -= eldest.value.size
        }
    }
}
