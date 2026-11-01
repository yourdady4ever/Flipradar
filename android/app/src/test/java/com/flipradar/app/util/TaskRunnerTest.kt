package com.flipradar.app.util

import com.flipradar.app.data.ApiClient
import com.flipradar.app.data.TinyHttpServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.net.InetAddress
import java.net.ServerSocket
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.CountDownLatch
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger
import kotlin.concurrent.thread

/**
 * Regression tests for review findings android#6 (a few long requests blocked every other screen, and
 * cancelling did not free their threads) and android#7 (re-tapping "Test connection" left the progress
 * bar spinning).
 */
class TaskRunnerTest {
    /** Stands in for the Android main thread. */
    private val main = Executors.newSingleThreadExecutor()
    private val runner = TaskRunner(TaskRunner.ioExecutor()) { r -> main.execute(r) }
    private val release = CountDownLatch(1)

    @After
    fun tearDown() {
        release.countDown()
        main.shutdown()
    }

    @Test
    fun cancelAbortsABlockingHttpCall() {
        // A server that accepts the request and answers only much later (a slow "run search").
        val server = ServerSocket(0, 50, InetAddress.getByName("127.0.0.1"))
        thread(isDaemon = true) {
            try {
                server.accept().use { release.await(30, TimeUnit.SECONDS) }
            } catch (_: Exception) {
            }
        }
        try {
            val finished = CountDownLatch(1)
            val api = ApiClient("http://127.0.0.1:${server.localPort}", null, readTimeoutMs = ApiClient.LONG_TIMEOUT_MS)
            val task = runner.run({
                try {
                    api.request("POST", "/api/v1/searches/1/run")
                } finally {
                    finished.countDown()
                }
            }, {}, {})
            Thread.sleep(300)
            task.cancel()
            assertTrue("worker thread still blocked after cancel()", finished.await(5, TimeUnit.SECONDS))
        } finally {
            release.countDown()
            server.close()
        }
    }

    @Test
    fun shortTasksDoNotWaitBehindLongOnes() {
        repeat(6) { runner.run({ release.await(30, TimeUnit.SECONDS) }, {}, {}) }
        val loaded = CountDownLatch(1)
        runner.run({ "opportunities" }, { loaded.countDown() }, {})
        assertTrue("short request queued behind long ones", loaded.await(5, TimeUnit.SECONDS))
    }

    @Test
    fun replacedTaskReleasesItsBusySlotAndItsResultIsDropped() {
        // SetupActivity.testConnection: cancel the pending test, setBusy(true), start the new test.
        val busy = AtomicInteger()
        val shown = CopyOnWriteArrayList<String>()
        val firstStarted = CountDownLatch(1)
        val firstDone = CountDownLatch(1)
        val secondShown = CountDownLatch(1)
        fun tap(work: () -> String): TaskRunner.Task {
            busy.incrementAndGet()
            return runner.run(
                work,
                { busy.decrementAndGet(); shown += it; secondShown.countDown() },
                { busy.decrementAndGet() },
                onCancel = { busy.decrementAndGet() },
            )
        }
        val first = tap {
            firstStarted.countDown()
            try {
                release.await(30, TimeUnit.SECONDS)
                "first"
            } finally {
                firstDone.countDown()
            }
        }
        assertTrue(firstStarted.await(5, TimeUnit.SECONDS)) // the first test is in flight
        first.cancel()
        tap { "second" }
        assertTrue(secondShown.await(5, TimeUnit.SECONDS))
        release.countDown()
        assertTrue(firstDone.await(5, TimeUnit.SECONDS))
        main.submit {}.get(5, TimeUnit.SECONDS) // let any (wrongly) posted callback run
        assertEquals(listOf("second"), shown)
        assertEquals(0, busy.get())
    }

    @Test
    fun cancelAfterDeliveryDoesNotRunOnCancel() {
        val calls = CopyOnWriteArrayList<String>()
        val delivered = CountDownLatch(1)
        val task = runner.run({ 1 }, { calls += "success"; delivered.countDown() }, { calls += "error" }, onCancel = { calls += "cancel" })
        assertTrue(delivered.await(5, TimeUnit.SECONDS))
        task.cancel()
        task.cancel()
        assertEquals(listOf("success"), calls)
    }

    @Test
    fun cancelBeforeTheRequestStartsDoesNotSendIt() {
        val hits = AtomicInteger()
        val server = TinyHttpServer { hits.incrementAndGet(); TinyHttpServer.Response(200, "{}") }.start()
        try {
            val signal = CancelSignal()
            signal.cancel()
            val error = runCatching {
                CancelSignal.runWith(signal) { ApiClient("http://127.0.0.1:${server.port}", null).request("GET", "/health") }
            }.exceptionOrNull()
            assertTrue(error is com.flipradar.app.data.ApiException)
            assertEquals(0, hits.get())
        } finally {
            server.stop()
        }
    }
}
