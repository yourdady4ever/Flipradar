package com.flipradar.app.util

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ServerUrlTest {

    private fun valid(input: String): ServerUrl.Result.Valid {
        val r = ServerUrl.normalize(input)
        assertTrue("expected valid for '$input' but was $r", r is ServerUrl.Result.Valid)
        return r as ServerUrl.Result.Valid
    }

    private fun problem(input: String?): ServerUrl.Problem {
        val r = ServerUrl.normalize(input)
        assertTrue("expected invalid for '$input' but was $r", r is ServerUrl.Result.Invalid)
        return (r as ServerUrl.Result.Invalid).problem
    }

    @Test
    fun normalizesCommonInputs() {
        assertEquals("http://192.168.1.20:8000", valid("http://192.168.1.20:8000/").url)
        assertEquals("https://radar.example.com", valid("https://Radar.Example.com").url)
        assertEquals("https://radar.example.com/flip", valid("https://radar.example.com/flip///").url)
        assertEquals("http://10.0.2.2:8000", valid("  http://10.0.2.2:8000  ").url)
        assertEquals("http://[fd00::1]:8000", valid("http://[fd00::1]:8000").url)
    }

    @Test
    fun addsSchemeBasedOnHost() {
        assertEquals("http://192.168.1.20:8000", valid("192.168.1.20:8000").url)
        assertEquals("http://raspberrypi:8000", valid("raspberrypi:8000").url)
        assertEquals("http://nas.local", valid("nas.local").url)
        assertEquals("https://radar.example.com", valid("radar.example.com").url)
        assertEquals("http://[fd00::1]:8000", valid("[fd00::1]:8000").url)
        assertFalse(valid("radar.example.com").url.startsWith("http://"))
    }

    @Test
    fun reportsProblems() {
        assertEquals(ServerUrl.Problem.EMPTY, problem(null))
        assertEquals(ServerUrl.Problem.EMPTY, problem("   "))
        assertEquals(ServerUrl.Problem.BAD_SCHEME, problem("ftp://192.168.1.2"))
        assertEquals(ServerUrl.Problem.BAD_SCHEME, problem("file:///etc/passwd"))
        assertEquals(ServerUrl.Problem.HAS_QUERY, problem("http://192.168.1.2:8000/?token=abc"))
        assertEquals(ServerUrl.Problem.HAS_QUERY, problem("http://192.168.1.2:8000/#x"))
        assertEquals(ServerUrl.Problem.HAS_CREDENTIALS, problem("http://user:pass@192.168.1.2"))
        assertEquals(ServerUrl.Problem.MALFORMED, problem("http://exa mple.com"))
        assertEquals(ServerUrl.Problem.BAD_PORT, problem("http://192.168.1.2:99999"))
        assertEquals(ServerUrl.Problem.BAD_PORT, problem("http://192.168.1.2:abc"))
        assertEquals(ServerUrl.Problem.NO_HOST, problem("http://"))
    }

    @Test
    fun classifiesPrivateHosts() {
        listOf(
            "localhost", "127.0.0.1", "10.0.2.2", "10.255.255.255", "172.16.0.1", "172.31.255.1", "192.168.0.10",
            "169.254.1.1", "100.64.0.1", "100.127.255.254", "::1", "[::1]", "fd12:3456::1", "fe80::1",
            "raspberrypi", "nas.local", "server.lan", "box.home.arpa", "app.internal",
        ).forEach { assertTrue("$it should be private", ServerUrl.isPrivateHost(it)) }
        listOf(
            "8.8.8.8", "172.32.0.1", "172.15.0.1", "192.169.0.1", "100.128.0.1", "1.2.3.4",
            "radar.example.com", "mercadolibre.com.mx", "2001:4860::8888", "", "256.1.1.1.example",
        ).forEach { assertFalse("$it should be public", ServerUrl.isPrivateHost(it)) }
        assertFalse(ServerUrl.isPrivateHost(null))
    }

    @Test
    fun flagsCleartextToPublicHosts() {
        assertTrue(ServerUrl.isInsecurePublic("http://203.0.113.5:8000"))
        assertTrue(ServerUrl.isInsecurePublic("http://radar.example.com"))
        assertFalse(ServerUrl.isInsecurePublic("https://radar.example.com"))
        assertFalse(ServerUrl.isInsecurePublic("http://192.168.1.20:8000"))
        assertFalse(ServerUrl.isInsecurePublic("http://100.101.102.103:8000")) // Tailscale CGNAT range
        assertFalse(ServerUrl.isInsecurePublic("not a url"))
    }

    @Test
    fun buildsRequestUrls() {
        val url = ServerUrl.buildUrl(
            "http://192.168.1.20:8000/",
            "/api/v1/opportunities",
            listOf("q" to "iphone 13 128gb", "status" to null, "min_score" to 60, "include_suspicious" to false, "blank" to " ", "sym" to "a&b=c"),
        )
        assertEquals(
            "http://192.168.1.20:8000/api/v1/opportunities?q=iphone+13+128gb&min_score=60&include_suspicious=false&sym=a%26b%3Dc",
            url,
        )
        assertEquals("https://x.example/flip/health", ServerUrl.buildUrl("https://x.example/flip", "health"))
        assertEquals("mercado%20libre%2F%C3%B1", ServerUrl.encodePathSegment("mercado libre/ñ"))
    }
}
