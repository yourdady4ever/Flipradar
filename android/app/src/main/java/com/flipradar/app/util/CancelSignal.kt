package com.flipradar.app.util

/**
 * Cancellation for blocking work on a background thread.  Thread.interrupt() (Future.cancel(true))
 * cannot unblock a socket read, so blocking code registers how to abort itself ([setAbort]; ApiClient
 * disconnects its HttpURLConnection) and [cancel] hands that action to the canceller.
 */
class CancelSignal {
    private var abort: (() -> Unit)? = null

    @Volatile
    var isCancelled: Boolean = false
        private set

    /**
     * Marks the signal cancelled and returns the registered abort action (null if none or already
     * cancelled).  The caller runs it, off the Android main thread: closing a TLS socket does I/O.
     */
    fun cancel(): (() -> Unit)? = synchronized(this) {
        if (isCancelled) return null
        isCancelled = true
        abort.also { abort = null }
    }

    /**
     * Registers [action] as the way to abort the blocking call in progress (null unregisters).  Returns
     * false, registering nothing, when the signal is already cancelled: the caller should not start.
     */
    fun setAbort(action: (() -> Unit)?): Boolean = synchronized(this) {
        if (isCancelled) return false
        abort = action
        true
    }

    companion object {
        private val current = ThreadLocal<CancelSignal?>()

        /** The signal of the task running on this thread (null outside [TaskRunner]). */
        fun current(): CancelSignal? = current.get()

        /** Runs [block] with [signal] as this thread's [current] signal. */
        fun <T> runWith(signal: CancelSignal, block: () -> T): T {
            val previous = current.get()
            current.set(signal)
            try {
                return block()
            } finally {
                current.set(previous)
            }
        }
    }
}
