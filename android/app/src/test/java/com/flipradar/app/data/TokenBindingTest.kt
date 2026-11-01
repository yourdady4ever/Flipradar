package com.flipradar.app.data

import com.flipradar.app.util.ServerUrl
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.Collections

/**
 * Regression tests for review finding android#2: the saved API token must never be sent to a server
 * other than the one it was saved for (e.g. after a typo in the server URL).
 */
class TokenBindingTest {
    private val saved = "https://radar.home.example"

    @Test
    fun savedTokenIsNotUsedForAnotherServer() {
        assertNull(TokenBinding.tokenFor("https://radar.hom3.example", "", saved) { "SECRET-T" })
        assertNull(TokenBinding.tokenFor("http://192.168.1.100:8000", "  ", "http://192.168.1.10:8000") { "SECRET-T" })
        assertNull(TokenBinding.tokenFor("https://radar.home.example", null, null) { "SECRET-T" })
    }

    @Test
    fun savedTokenIsUsedForTheSameServerHoweverItIsTyped() {
        assertEquals("SECRET-T", TokenBinding.tokenFor("https://radar.home.example", "", saved) { "SECRET-T" })
        assertEquals("SECRET-T", TokenBinding.tokenFor("radar.HOME.example/", null, saved) { "SECRET-T" })
    }

    @Test
    fun typedTokenIsAlwaysUsed() {
        assertEquals("NEW", TokenBinding.tokenFor("https://radar.hom3.example", " NEW ", saved) { "SECRET-T" })
    }

    @Test
    fun savedTokenIsNotEvenDecryptedForAnotherServer() {
        var decrypted = false
        TokenBinding.tokenFor("https://other.example", "", saved) { decrypted = true; "SECRET-T" }
        assertFalse(decrypted)
    }

    @Test
    fun sameServerComparesTheNormalisedUrl() {
        assertTrue(ServerUrl.sameServer("https://radar.home.example", "radar.home.example/"))
        assertTrue(ServerUrl.sameServer("http://192.168.1.10:8000", "192.168.1.10:8000"))
        assertFalse(ServerUrl.sameServer("http://192.168.1.10:8000", "http://192.168.1.10:8001"))
        assertFalse(ServerUrl.sameServer("http://radar.home.example", "https://radar.home.example"))
        assertFalse(ServerUrl.sameServer("https://radar.home.example/a", "https://radar.home.example/b"))
        assertFalse(ServerUrl.sameServer(null, "https://radar.home.example"))
        assertFalse(ServerUrl.sameServer("https://radar.home.example", "not a url"))
    }

    @Test
    fun testingAnotherHostSendsNoAuthorizationHeader() {
        val seen = Collections.synchronizedList(ArrayList<String?>())
        val other = TinyHttpServer { req ->
            seen += req.header("Authorization")
            TinyHttpServer.Response(200, """{"ok": false, "token_name": null, "auth_required": true}""")
        }.start()
        try {
            val url = "http://127.0.0.1:${other.port}"
            // What SetupActivity.testConnection does with the token field left blank.
            ApiClient(url, TokenBinding.tokenFor(url, "", saved) { "SECRET-T" }).authCheck()
            assertEquals(listOf<String?>(null), seen)
        } finally {
            other.stop()
        }
    }
}
