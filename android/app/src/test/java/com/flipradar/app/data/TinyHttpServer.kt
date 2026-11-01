package com.flipradar.app.data

import java.io.BufferedInputStream
import java.io.ByteArrayOutputStream
import java.io.InputStream
import java.net.InetAddress
import java.net.ServerSocket
import java.net.Socket
import java.net.URI
import kotlin.concurrent.thread

/**
 * Minimal HTTP/1.1 test server built only on java.net.ServerSocket (available in android.jar), so the
 * HTTP tests compile both with plain kotlinc and with the Android Gradle Plugin's unit-test classpath
 * (which does not contain com.sun.net.httpserver).  One request per connection, Connection: close.
 */
class TinyHttpServer(private val handler: (Request) -> Response) {
    data class Request(
        val method: String,
        val path: String,
        val rawQuery: String?,
        val headers: Map<String, String>,
        val body: String,
    ) {
        fun header(name: String): String? = headers[name.lowercase()]
    }

    data class Response(val status: Int, val body: String, val headers: Map<String, String> = emptyMap())

    private val socket = ServerSocket(0, 50, InetAddress.getByName("127.0.0.1"))
    @Volatile private var running = true
    val port: Int get() = socket.localPort

    fun start(): TinyHttpServer {
        thread(isDaemon = true, name = "tiny-http") {
            while (running) {
                val client = try { socket.accept() } catch (e: Exception) { break }
                thread(isDaemon = true) { client.use { serve(it) } }
            }
        }
        return this
    }

    fun stop() {
        running = false
        socket.close()
    }

    private fun readLine(input: InputStream): String {
        val out = ByteArrayOutputStream()
        while (true) {
            val b = input.read()
            if (b == -1 || b == '\n'.code) break
            if (b != '\r'.code) out.write(b)
        }
        return out.toString(Charsets.UTF_8.name())
    }

    private fun serve(client: Socket) {
        val input = BufferedInputStream(client.getInputStream())
        val requestLine = readLine(input)
        if (requestLine.isBlank()) return
        val (method, target) = requestLine.split(" ").let { it[0] to it[1] }
        val headers = mutableMapOf<String, String>()
        while (true) {
            val line = readLine(input)
            if (line.isEmpty()) break
            val idx = line.indexOf(':')
            if (idx > 0) headers[line.substring(0, idx).trim().lowercase()] = line.substring(idx + 1).trim()
        }
        val length = headers["content-length"]?.toIntOrNull() ?: 0
        val bodyBytes = ByteArray(length)
        var read = 0
        while (read < length) {
            val n = input.read(bodyBytes, read, length - read)
            if (n < 0) break
            read += n
        }
        val uri = URI(target)
        val response = handler(Request(method, uri.path, uri.rawQuery, headers, String(bodyBytes, Charsets.UTF_8)))
        val payload = response.body.toByteArray(Charsets.UTF_8)
        val head = StringBuilder()
        head.append("HTTP/1.1 ${response.status} X\r\n")
        head.append("Content-Type: application/json\r\n")
        response.headers.forEach { (k, v) -> head.append("$k: $v\r\n") }
        head.append("Content-Length: ${payload.size}\r\n")
        head.append("Connection: close\r\n\r\n")
        val out = client.getOutputStream()
        out.write(head.toString().toByteArray(Charsets.UTF_8))
        out.write(payload)
        out.flush()
    }
}
