package com.flipradar.app.util

import android.os.Handler
import android.os.Looper
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors
import java.util.concurrent.Future
import java.util.concurrent.atomic.AtomicInteger

/**
 * Tiny background-work helper (no coroutines library): runs blocking work on a small thread pool and
 * delivers the result on the main thread unless the task was cancelled (e.g. its Activity finished).
 */
object Async {
    private val counter = AtomicInteger(1)
    private val executor: ExecutorService = Executors.newFixedThreadPool(4) { r ->
        Thread(r, "flipradar-io-" + counter.getAndIncrement()).apply { isDaemon = true }
    }
    private val main = Handler(Looper.getMainLooper())

    class Task internal constructor() {
        @Volatile
        var isCancelled: Boolean = false
            private set
        @Volatile
        internal var future: Future<*>? = null

        fun cancel() {
            isCancelled = true
            future?.cancel(true)
        }
    }

    /** Runs [work] off the main thread; exactly one of [onSuccess] / [onError] runs on the main thread. */
    fun <T> run(work: () -> T, onSuccess: (T) -> Unit, onError: (Throwable) -> Unit): Task {
        val task = Task()
        task.future = executor.submit {
            var result: T? = null
            var error: Throwable? = null
            try {
                result = work()
            } catch (t: Throwable) {
                error = t
            }
            if (!task.isCancelled) {
                main.post {
                    if (task.isCancelled) return@post
                    val e = error
                    @Suppress("UNCHECKED_CAST")
                    if (e != null) onError(e) else onSuccess(result as T)
                }
            }
        }
        return task
    }

    /** Fire-and-forget background work (errors are swallowed; used for best-effort calls). */
    fun background(work: () -> Unit) {
        executor.execute {
            try {
                work()
            } catch (_: Throwable) {
            }
        }
    }

    fun onMain(block: () -> Unit) {
        main.post(block)
    }
}
