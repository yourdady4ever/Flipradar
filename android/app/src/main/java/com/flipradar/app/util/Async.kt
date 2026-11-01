package com.flipradar.app.util

import android.os.Handler
import android.os.Looper

/**
 * Tiny background-work helper (no coroutines library): runs blocking work on a cached thread pool and
 * delivers the result on the main thread unless the task was cancelled (e.g. its Activity finished).
 * The logic lives in [TaskRunner] (pure JVM, unit-tested).
 */
object Async {
    private val main = Handler(Looper.getMainLooper())
    private val runner = TaskRunner(TaskRunner.ioExecutor()) { r -> main.post(r) }

    /**
     * Runs [work] off the main thread; exactly one of [onSuccess] / [onError] runs on the main thread,
     * or [onCancel] when the task is cancelled first (see [TaskRunner.Task.cancel]).
     */
    fun <T> run(
        work: () -> T,
        onSuccess: (T) -> Unit,
        onError: (Throwable) -> Unit,
        onCancel: (() -> Unit)? = null,
    ): TaskRunner.Task = runner.run(work, onSuccess, onError, onCancel)

    /** Fire-and-forget background work (errors are swallowed; used for best-effort calls). */
    fun background(work: () -> Unit) = runner.background(work)

    fun onMain(block: () -> Unit) {
        main.post(block)
    }
}
