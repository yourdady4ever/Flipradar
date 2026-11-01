package com.flipradar.app.util

import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors
import java.util.concurrent.Future
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicInteger

/**
 * Runs blocking work on [executor] and hands exactly one callback to [deliver] (the main thread on
 * Android, see Async).  Pure JVM so it is unit-testable.
 *
 * Cancelling a task drops its success/error callback and runs its onCancel instead, so a caller that
 * counts busy work stays balanced.  It also aborts an HTTP call the task is blocked in (see
 * [CancelSignal]), which frees the thread at once instead of after the read timeout.
 */
class TaskRunner(private val executor: ExecutorService, private val deliver: (Runnable) -> Unit) {

    companion object {
        private val counter = AtomicInteger(1)

        /**
         * Unbounded (cached) pool of daemon threads: a long call (run search, source test, recompute, AI
         * summary: up to 3 minutes) never makes another screen's requests queue behind it.  Threads only
         * live while work runs, plus 60 s idle.
         */
        fun ioExecutor(): ExecutorService = Executors.newCachedThreadPool { r ->
            Thread(r, "flipradar-io-" + counter.getAndIncrement()).apply { isDaemon = true }
        }
    }

    class Task internal constructor(private val abortOn: ExecutorService, private val onCancel: (() -> Unit)?) {
        internal val signal = CancelSignal()
        private val settled = AtomicBoolean(false)

        @Volatile
        internal var future: Future<*>? = null

        val isCancelled: Boolean get() = signal.isCancelled

        /** Claims the task's single callback; false once a callback ran or the task was cancelled. */
        internal fun settle(): Boolean = settled.compareAndSet(false, true)

        /**
         * Cancels the task: its success/error callback will not run.  If it had not delivered yet, its
         * onCancel runs now, on the calling thread, exactly once.
         */
        fun cancel() {
            signal.cancel()?.let { abort ->
                abortOn.execute {
                    try {
                        abort()
                    } catch (_: Throwable) {
                    }
                }
            }
            future?.cancel(true)
            if (settle()) onCancel?.invoke()
        }
    }

    /**
     * Runs [work] off the calling thread.  Exactly one of [onSuccess] / [onError] / [onCancel] runs:
     * the first two through [deliver], [onCancel] inside [Task.cancel].
     */
    fun <T> run(work: () -> T, onSuccess: (T) -> Unit, onError: (Throwable) -> Unit, onCancel: (() -> Unit)? = null): Task {
        val task = Task(executor, onCancel)
        task.future = executor.submit {
            var result: T? = null
            var error: Throwable? = null
            try {
                result = CancelSignal.runWith(task.signal, work)
            } catch (t: Throwable) {
                error = t
            }
            if (!task.isCancelled) {
                deliver(
                    Runnable {
                        if (task.isCancelled || !task.settle()) return@Runnable
                        val e = error
                        @Suppress("UNCHECKED_CAST")
                        if (e != null) onError(e) else onSuccess(result as T)
                    },
                )
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
}
