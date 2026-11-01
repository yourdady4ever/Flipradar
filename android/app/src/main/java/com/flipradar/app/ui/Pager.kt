package com.flipradar.app.ui

/**
 * Paging state for a list loaded page by page (pure JVM, unit-tested).  [start] fixes the query;
 * [next] always continues that same query, never whatever the filter widgets show at that moment, so
 * pages of different queries are never mixed.  Pages answering an older [start] are dropped.
 */
class Pager<Q : Any>(private val pageSize: Int) {
    class Request<Q> internal constructor(
        val query: Q,
        val offset: Int,
        val limit: Int,
        val append: Boolean,
        internal val generation: Int,
    )

    private var generation = 0
    private var inFlight: Request<Q>? = null

    /** The query the list shows (null before the first [start]). */
    var query: Q? = null
        private set
    var loaded = 0
        private set
    var total = 0L
        private set

    val hasMore: Boolean get() = loaded < total

    /** First page of [query]; replaces whatever was loaded or in flight. */
    fun start(query: Q): Request<Q> {
        generation++
        this.query = query
        loaded = 0
        total = 0
        return Request(query, 0, pageSize, append = false, generation = generation).also { inFlight = it }
    }

    /** Next page of the current query, or null while a page is loading or when nothing is left. */
    fun next(): Request<Q>? {
        val q = query ?: return null
        if (inFlight != null || !hasMore) return null
        return Request(q, loaded, pageSize, append = true, generation = generation).also { inFlight = it }
    }

    /** Records a loaded page; false when [request] belongs to an older [start] (drop its items). */
    fun onPage(request: Request<Q>, itemCount: Int, total: Long): Boolean {
        if (request.generation != generation) return false
        inFlight = null
        loaded = (if (request.append) loaded else 0) + itemCount
        this.total = total
        return true
    }

    /** Records a failed page so it can be requested again. */
    fun onError(request: Request<Q>) {
        if (request.generation == generation) inFlight = null
    }
}
