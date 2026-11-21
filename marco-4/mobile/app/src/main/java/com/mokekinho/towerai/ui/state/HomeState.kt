package com.mokekinho.towerai.ui.state

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.mokekinho.towerai.util.ApiFailure
import com.mokekinho.towerai.util.LanApi
import com.mokekinho.towerai.util.MicrophoneSession
import com.mokekinho.towerai.util.readCa
import java.io.IOException
import java.security.MessageDigest
import java.util.UUID
import javax.net.ssl.SSLException
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONObject

data class HomeState(
    val address: String = "",
    val credential: String = "",
    val fingerprint: String? = null,
    val title: String = "",
    val room: String? = null,
    val presence: Int? = null,
    val working: Boolean = false,
    val capturing: Boolean = false,
    val stopping: Boolean = false,
    val reconnecting: String? = null,
    val networkError: String? = null,
    val error: String? = null,
    val active: JSONObject? = null,
    val history: List<JSONObject>? = null,
    val selectedId: String? = null,
    val detail: JSONObject? = null,
)

class HomeViewModel : ViewModel() {
    private val mutable = MutableStateFlow(HomeState())
    val state = mutable.asStateFlow()
    private var ca: ByteArray? = null
    private var client: LanApi? = null
    private var capture: MicrophoneSession? = null
    private var monitor: Job? = null
    private var activeId: String? = null

    fun address(value: String) = mutable.update { it.copy(address = value) }
    fun credential(value: String) = mutable.update { it.copy(credential = value) }
    fun title(value: String) = mutable.update { it.copy(title = value.take(100)) }
    fun message(value: String) = mutable.update { it.copy(error = value) }

    fun importCa(bytes: ByteArray) {
        try {
            val cert = readCa(bytes)
            val digest = MessageDigest.getInstance("SHA-256").digest(cert.encoded)
            ca = bytes
            mutable.update { it.copy(fingerprint = digest.joinToString(":") { byte -> "%02X".format(byte) }, error = null) }
        } catch (_: Exception) {
            message("O certificado está inválido ou venceu. Selecione o ca.pem emitido pelo notebook.")
        }
    }

    fun connect() {
        val certificate = ca ?: return message("Importe o ca.pem do notebook para validar a conexão.")
        if (mutable.value.working) return
        val form = mutable.value
        mutable.update { it.copy(working = true, error = null) }
        viewModelScope.launch {
            try {
                val (api, status) = withContext(Dispatchers.IO) {
                    val api = LanApi(form.address, form.credential.trim(), certificate)
                    api to api.status()
                }
                if (status.optString("role") != "microphone") {
                    message("Use a credencial do produtor de microfone para acessar o app.")
                    return@launch
                }
                client = api
                mutable.update { it.copy(room = status.getString("room"), presence = people(status), credential = "") }
                monitor = monitor(api)
            } catch (error: CancellationException) {
                throw error
            } catch (error: Exception) {
                message(describe(error))
            } finally {
                mutable.update { it.copy(working = false) }
            }
        }
    }

    fun disconnect() {
        if (capture != null) return
        monitor?.cancel()
        client = null
        activeId = null
        mutable.update { HomeState(address = it.address, fingerprint = it.fingerprint) }
    }

    fun start() {
        val api = client ?: return
        if (capture != null) return
        val id = UUID.randomUUID().toString().replace("-", "")
        val title = mutable.value.title.trim().ifEmpty { "Reunião" }
        val session = MicrophoneSession(id, api, title,
            update = { data -> mutable.update { it.copy(active = data) } },
            notice = { notice -> mutable.update { it.copy(reconnecting = notice) } },
        )
        activeId = id
        capture = session
        mutable.update { it.copy(capturing = true, active = null, error = null) }
        viewModelScope.launch {
            try {
                withContext(Dispatchers.IO) { session.run() }
            } catch (error: CancellationException) {
                throw error
            } catch (error: Exception) {
                message(describe(error))
            } finally {
                capture = null
                mutable.update { it.copy(capturing = false, stopping = false, reconnecting = null) }
            }
        }
    }

    fun stop() {
        capture?.stop() ?: return
        mutable.update { it.copy(stopping = true) }
    }

    fun select(id: String?) {
        mutable.update { it.copy(selectedId = id, detail = null) }
    }

    fun retry(id: String) {
        val api = client ?: return
        viewModelScope.launch {
            try {
                withContext(Dispatchers.IO) { api.retry(id) }
            } catch (error: CancellationException) {
                throw error
            } catch (error: Exception) {
                message(describe(error))
            }
        }
    }

    private fun monitor(api: LanApi) = viewModelScope.launch {
        while (isActive) {
            try {
                val current = activeId
                val selection = mutable.value.selectedId
                val (status, history) = withContext(Dispatchers.IO) { api.status() to api.history() }
                val items = history.getJSONArray("meetings")
                val entries = List(items.length()) { index -> items.getJSONObject(index) }
                mutable.update { it.copy(presence = people(status), history = entries, networkError = null) }
                val tracked = selection ?: current
                if (tracked != null) {
                    val detail = withContext(Dispatchers.IO) {
                        try { api.meeting(tracked) } catch (error: ApiFailure) {
                            if (error.status != 404) throw error
                            null
                        }
                    }
                    mutable.update {
                        when {
                            selection != null && it.selectedId == tracked -> it.copy(detail = detail)
                            selection == null && activeId == tracked && detail != null -> {
                                val visibleSequence = it.active?.optInt("nextSequence") ?: 0
                                if (detail.optInt("nextSequence") < visibleSequence) it else it.copy(active = detail)
                            }
                            else -> it
                        }
                    }
                }
            } catch (error: CancellationException) {
                throw error
            } catch (error: Exception) {
                mutable.update { it.copy(networkError = describe(error), presence = null) }
            }
            delay(1800)
        }
    }

    override fun onCleared() {
        capture?.stop()
    }

    private fun people(status: JSONObject): Int? = status.getJSONObject("presence").let {
        if (it.isNull("people")) null else it.getInt("people")
    }

    private fun describe(error: Throwable): String = when (error) {
        is SSLException -> "O servidor não pôde ser autenticado. Confira o IP, a porta e o certificado ca.pem."
        is ApiFailure -> error.message.orEmpty()
        is SecurityException -> "O microfone está sem permissão. Libere o acesso nas configurações do Android."
        is IOException -> error.message?.takeIf { it.startsWith("O limite de áudio") }
            ?: "O notebook está indisponível. Confira o Wi-Fi e o servidor."
        else -> error.message ?: "Não foi possível concluir a operação."
    }
}
