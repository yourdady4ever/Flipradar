package com.flipradar.app.data

import org.json.JSONObject
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Before
import org.junit.Test
import java.net.ServerSocket
import java.util.Collections

/**
 * Exercises the real HttpURLConnection code path of [ApiClient] against an in-process HTTP server:
 * bearer header handling, query encoding, PATCH bodies, FastAPI error details, redirects, bad JSON.
 */
class ApiClientHttpTest {
    private lateinit var server: TinyHttpServer
    private lateinit var base: String
    private val requests = Collections.synchronizedList(ArrayList<Recorded>())

    data class Recorded(val method: String, val path: String, val query: String?, val auth: String?, val body: String)

    private fun respond(@Suppress("UNUSED_PARAMETER") ex: TinyHttpServer.Request, status: Int, body: String,
                        headers: Map<String, String> = emptyMap()) = TinyHttpServer.Response(status, body, headers)

    @Before
    fun start() {
        server = TinyHttpServer { ex ->
            val body = ex.body
            val path = ex.path
            val auth = ex.header("Authorization")
            requests.add(Recorded(ex.method, path, ex.rawQuery, auth, body))
            val authorized = auth == "Bearer s3cret-token"
            when {
                path == "/health" -> respond(ex, 200, """{"status": "ok", "version": "0.1.0", "api_version": "1", "database": "sqlite", "time": "2026-11-01T00:00:00Z"}""")
                path == "/redirect/api/v1/auth/check" -> respond(ex, 307, "", mapOf("Location" to "http://evil.example/steal"))
                path == "/notjson/api/v1/auth/check" -> respond(ex, 200, "<html>proxy login</html>")
                !authorized -> respond(ex, 401, """{"detail": "Invalid or missing API token"}""")
                path == "/api/v1/auth/check" -> respond(ex, 200, """{"ok": true, "token_name": "phone", "auth_required": true}""")
                path == "/api/v1/opportunities" -> respond(ex, 200, """{"items": [{"id": 1, "product_title": "Switch", "net_profit_cents": 1500, "score": 70}], "total": 1, "limit": 30, "offset": 0}""")
                path == "/api/v1/opportunities/5" && ex.method == "PATCH" ->
                    respond(ex, 200, """{"id": 5, "status": "${JSONObject(body).getString("status")}"}""")
                path == "/api/v1/calculator/profit" -> respond(
                    ex, 422,
                    """{"detail": [{"type": "greater_than_equal", "loc": ["body", "buy_price_cents"], "msg": "Input should be greater than or equal to 0"}]}""",
                )
                path == "/api/v1/notifications" -> respond(ex, 200, "[]")
                path == "/api/v1/alerts/3" && ex.method == "DELETE" -> respond(ex, 200, """{"ok": true, "message": "deleted"}""")
                else -> respond(ex, 404, """{"detail": "Not Found"}""")
            }
        }
        server.start()
        base = "http://127.0.0.1:${server.port}"
    }

    @After
    fun stop() {
        server.stop()
    }

    private fun client(token: String? = "s3cret-token", path: String = "") = ApiClient(base + path, token, 3_000, 3_000)

    @Test
    fun healthIsCalledWithoutTheToken() {
        val h = client().health()
        assertEquals("ok", h.status)
        assertNull("token must not be sent to the unauthenticated /health endpoint", requests.single().auth)
    }

    @Test
    fun sendsBearerTokenAndParsesAuthCheck() {
        val a = client().authCheck()
        assertTrue(a.ok)
        assertEquals("phone", a.tokenName)
        assertEquals("Bearer s3cret-token", requests.single().auth)
    }

    @Test
    fun wrongTokenGivesUnauthorizedWithDetailButNoTokenInMessage() {
        try {
            client(token = "wrong-token").authCheck()
            fail("expected ApiException")
        } catch (e: ApiException) {
            assertEquals(ApiException.Kind.UNAUTHORIZED, e.kind)
            assertEquals(401, e.httpStatus)
            assertEquals("Invalid or missing API token", e.detail)
            assertFalse(e.message!!.contains("wrong-token"))
        }
    }

    @Test
    fun encodesOpportunityQuery() {
        val page = client().opportunities(OpportunityQuery(q = "iphone 13", minScore = 60, sort = "profit"), 30, 0)
        assertEquals(1, page.items.size)
        assertEquals(1_500L, page.items[0].netProfitCents)
        val q = requests.single().query!!
        assertTrue(q, q.contains("q=iphone+13"))
        assertTrue(q, q.contains("min_score=60"))
        assertTrue(q, q.contains("include_suspicious=false"))
        assertTrue(q, q.contains("sort=profit"))
        assertTrue(q, q.contains("limit=30") && q.contains("offset=0"))
        assertFalse(q, q.contains("status="))
    }

    @Test
    fun sendsPatchWithJsonBody() {
        val o = client().setOpportunityStatus(5, "watching")
        assertEquals("watching", o.status)
        val r = requests.single()
        assertEquals("PATCH", r.method)
        assertEquals("watching", JSONObject(r.body).getString("status"))
    }

    @Test
    fun deleteReturnsMessage() {
        val m = client().deleteAlert(3)
        assertTrue(m.ok)
        assertEquals("DELETE", requests.single().method)
    }

    @Test
    fun validationErrorsAreReadable() {
        try {
            client().calculateProfit(CalculatorIn(buyPriceCents = 1, salePriceCents = 2))
            fail("expected ApiException")
        } catch (e: ApiException) {
            assertEquals(ApiException.Kind.HTTP, e.kind)
            assertEquals(422, e.httpStatus)
            assertEquals("buy_price_cents: Input should be greater than or equal to 0", e.detail)
        }
    }

    @Test
    fun parsesEmptyArrays() {
        assertTrue(client().notifications(unreadOnly = true, sinceId = 0).isEmpty())
        assertTrue(requests.single().query!!.contains("unread_only=true"))
    }

    @Test
    fun doesNotFollowRedirects() {
        try {
            client(path = "/redirect").authCheck()
            fail("expected ApiException")
        } catch (e: ApiException) {
            assertEquals(ApiException.Kind.REDIRECT, e.kind)
            assertEquals("http://evil.example/steal", e.detail)
        }
        assertEquals(1, requests.size)
    }

    @Test
    fun nonJsonResponseIsAParseError() {
        try {
            client(path = "/notjson").authCheck()
            fail("expected ApiException")
        } catch (e: ApiException) {
            assertEquals(ApiException.Kind.PARSE, e.kind)
        }
    }

    @Test
    fun unknownEndpointIsNotFound() {
        try {
            client().inventoryItem(999)
            fail("expected ApiException")
        } catch (e: ApiException) {
            assertEquals(ApiException.Kind.NOT_FOUND, e.kind)
            assertEquals("Not Found", e.detail)
        }
    }

    @Test
    fun closedPortIsANetworkError() {
        val port = ServerSocket(0).use { it.localPort }
        try {
            ApiClient("http://127.0.0.1:$port", "t", 2_000, 2_000).health()
            fail("expected ApiException")
        } catch (e: ApiException) {
            assertEquals(ApiException.Kind.NETWORK, e.kind)
        }
    }
}
