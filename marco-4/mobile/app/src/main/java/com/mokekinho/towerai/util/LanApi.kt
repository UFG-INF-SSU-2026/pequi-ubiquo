package com.mokekinho.towerai.util

import android.os.SystemClock
import java.io.IOException
import java.net.Proxy
import java.security.KeyStore
import java.security.cert.CertificateFactory
import java.security.cert.X509Certificate
import java.util.concurrent.TimeUnit
import javax.net.ssl.SSLContext
import javax.net.ssl.TrustManagerFactory
import javax.net.ssl.X509TrustManager
import okhttp3.ConnectionSpec
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.TlsVersion
import org.json.JSONObject

class ApiFailure(val status: Int, message: String) : IOException(message)

internal data class StartedCapture(val body: JSONObject, val clock: CaptureClock)

class LanApi(address: String, private val token: String, ca: ByteArray) {
    private val origin = requireNotNull(address.trim().toHttpUrlOrNull()) {
        "Informe o endereço do notebook, incluindo https:// e a porta."
    }.also {
        require(it.isHttps && it.encodedPath == "/" && it.query == null && it.fragment == null &&
            it.username.isEmpty() && it.password.isEmpty()) {
            "Informe somente a origem HTTPS do notebook."
        }
        require(token.matches(Regex("[A-Za-z0-9_-]{16,128}"))) {
            "Informe a credencial do microfone provisionada no servidor."
        }
    }

    private val client: OkHttpClient

    init {
        val trust = KeyStore.getInstance(KeyStore.getDefaultType()).apply {
            load(null)
            setCertificateEntry("towerai-lan", readCa(ca))
        }
        val manager = TrustManagerFactory.getInstance(TrustManagerFactory.getDefaultAlgorithm()).apply {
            init(trust)
        }.trustManagers.filterIsInstance<X509TrustManager>().single()
        val context = SSLContext.getInstance("TLS").apply { init(null, arrayOf(manager), null) }
        val tls = ConnectionSpec.Builder(ConnectionSpec.MODERN_TLS)
            .tlsVersions(TlsVersion.TLS_1_3, TlsVersion.TLS_1_2).build()
        client = OkHttpClient.Builder()
            .sslSocketFactory(context.socketFactory, manager)
            .connectionSpecs(listOf(tls))
            .proxy(Proxy.NO_PROXY)
            .followRedirects(false)
            .followSslRedirects(false)
            .retryOnConnectionFailure(false)
            .callTimeout(5, TimeUnit.SECONDS)
            .build()
    }

    fun status() = request("GET", "status").body
    fun history() = request("GET", "meetings").body
    fun meeting(id: String) = request("GET", "captures/$id").body

    internal fun start(id: String, title: String): StartedCapture {
        val body = JSONObject().put("title", title).toString().encodeToByteArray()
        val sentAtMillis = SystemClock.elapsedRealtime()
        val reply = request("PUT", "captures/$id", body)
        val serverMillis = checkNotNull(reply.serverEpochMillis) {
            "O servidor não informou o horário para iniciar a captura."
        }
        return StartedCapture(reply.body, CaptureClock(serverMillis, sentAtMillis, reply.receivedAtMillis))
    }

    fun finish(id: String) = request("PUT", "captures/$id/finish", ByteArray(0)).body
    fun retry(id: String) = request("PUT", "captures/$id/retry", ByteArray(0)).body

    fun chunk(id: String, sequence: Int, timestamp: Double, pcm: ByteArray) = request(
        "PUT", "captures/$id/chunks/$sequence", pcm, timestamp,
    ).body

    private data class Reply(val body: JSONObject, val serverEpochMillis: Long?, val receivedAtMillis: Long)

    private fun request(method: String, path: String, body: ByteArray? = null, timestamp: Double? = null): Reply {
        val url = origin.newBuilder().addPathSegment("v1").addPathSegments(path).build()
        val type = if (timestamp == null) "application/json" else "application/octet-stream"
        val request = Request.Builder().url(url)
            .header("Authorization", "Bearer $token")
            .method(method, body?.toRequestBody(type.toMediaType()))
            .apply { if (timestamp != null) header("X-Captured-At", timestamp.toString()) }
            .build()
        return client.newCall(request).execute().use { response ->
            val receivedAtMillis = SystemClock.elapsedRealtime()
            val data = response.body?.string().orEmpty()
            if (!response.isSuccessful) {
                val reason = runCatching { JSONObject(data).optString("error") }.getOrDefault("")
                throw ApiFailure(response.code, reason.ifBlank { "O servidor retornou HTTP ${response.code}." })
            }
            Reply(JSONObject(data), response.headers.getDate("Date")?.time, receivedAtMillis)
        }
    }
}

fun readCa(bytes: ByteArray): X509Certificate {
    val cert = CertificateFactory.getInstance("X.509").generateCertificate(bytes.inputStream()) as X509Certificate
    cert.checkValidity()
    require(cert.basicConstraints >= 0) { "Selecione o arquivo ca.pem da instalação." }
    return cert
}
