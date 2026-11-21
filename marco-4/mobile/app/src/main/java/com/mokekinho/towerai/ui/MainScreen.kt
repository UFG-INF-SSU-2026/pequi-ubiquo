package com.mokekinho.towerai.ui

import android.Manifest
import android.content.pm.PackageManager
import androidx.activity.compose.BackHandler
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Tab
import androidx.compose.material3.TabRow
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.mokekinho.towerai.ui.state.HomeState
import com.mokekinho.towerai.ui.state.HomeViewModel
import org.json.JSONArray
import org.json.JSONObject
import java.text.DateFormat
import java.util.Date

@Composable
fun MainScreen(home: HomeViewModel) {
    val state by home.state.collectAsStateWithLifecycle()
    val context = LocalContext.current
    var tab by rememberSaveable { mutableIntStateOf(0) }
    val ca = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        if (uri != null) {
            val bytes = runCatching {
                context.contentResolver.openInputStream(uri)?.use { stream ->
                    val data = ByteArray(16_385)
                    var size = 0
                    while (size < data.size) {
                        val count = stream.read(data, size, data.size - size)
                        if (count <= 0) break
                        size += count
                    }
                    require(size in 1..16_384)
                    data.copyOf(size)
                }
            }.getOrNull()
            if (bytes != null) home.importCa(bytes) else home.message("Não foi possível ler o arquivo ca.pem.")
        }
    }
    val permission = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        if (granted) home.start() else home.message("Permita o uso do microfone para iniciar a captura.")
    }
    BackHandler(state.selectedId != null) { home.select(null) }
    Scaffold(modifier = Modifier.fillMaxSize()) { insets ->
        Column(Modifier.fillMaxSize().padding(insets).imePadding()) {
            Row(
                Modifier.fillMaxWidth().padding(horizontal = 24.dp, vertical = 14.dp),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Column(Modifier.weight(1f)) {
                    Text("TowerAI", style = MaterialTheme.typography.headlineMedium, fontWeight = FontWeight.Bold)
                    state.room?.let { Text(it, color = MaterialTheme.colorScheme.onSurfaceVariant) }
                }
                if (state.room != null) {
                    TextButton(enabled = !state.capturing, onClick = { tab = 0; home.disconnect() }) { Text("Sair") }
                }
            }
            if (state.room != null) {
                TabRow(selectedTabIndex = tab) {
                    Tab(tab == 0, onClick = { tab = 0; home.select(null) }, text = { Text("Reunião") })
                    Tab(tab == 1, onClick = { tab = 1 }, text = { Text("Histórico") })
                }
            } else {
                HorizontalDivider()
            }
            val scroll = rememberScrollState()
            LaunchedEffect(state.room, tab, state.selectedId) { scroll.scrollTo(0) }
            Column(
                Modifier.weight(1f).fillMaxWidth().verticalScroll(scroll).padding(24.dp),
                verticalArrangement = Arrangement.spacedBy(14.dp),
            ) {
                state.error?.let { Notice(it, error = true) }
                (state.reconnecting ?: state.networkError)?.let { Notice(it, error = true) }
                when {
                    state.room == null -> Connection(state, home) { ca.launch(arrayOf("*/*")) }
                    tab == 0 -> Capture(state, home, openMinutes = { id -> home.select(id); tab = 1 }) {
                        if (ContextCompat.checkSelfPermission(context, Manifest.permission.RECORD_AUDIO)
                            == PackageManager.PERMISSION_GRANTED) home.start()
                        else permission.launch(Manifest.permission.RECORD_AUDIO)
                    }
                    state.selectedId != null -> Meeting(state.detail, home)
                    else -> History(state.history, state.networkError == null, home::select)
                }
            }
        }
    }
}

@Composable
private fun Connection(state: HomeState, home: HomeViewModel, selectCertificate: () -> Unit) {
    val focus = LocalFocusManager.current
    Text("Conectar à sala", style = MaterialTheme.typography.headlineSmall)
    Text("Use o endereço e o certificado do notebook na mesma rede Wi-Fi.")
    OutlinedTextField(
        state.address, home::address, Modifier.fillMaxWidth(), enabled = !state.working, singleLine = true,
        label = { Text("Endereço do servidor") }, placeholder = { Text("https://192.168.1.20:8443") },
        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri),
    )
    OutlinedTextField(
        state.credential, home::credential, Modifier.fillMaxWidth(), enabled = !state.working, singleLine = true,
        label = { Text("Credencial do microfone") }, visualTransformation = PasswordVisualTransformation(),
        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password),
    )
    OutlinedButton(onClick = selectCertificate, enabled = !state.working) {
        Text(if (state.fingerprint == null) "Importar ca.pem" else "Trocar certificado")
    }
    state.fingerprint?.let {
        SelectionContainer {
            Text("SHA-256 do certificado\n$it", style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
    Button(onClick = {
        focus.clearFocus()
        home.connect()
    }, enabled = !state.working, modifier = Modifier.fillMaxWidth()) {
        Text(if (state.working) "Verificando conexão…" else "Conectar")
    }
}

@Composable
private fun Capture(state: HomeState, home: HomeViewModel, openMinutes: (String) -> Unit, start: () -> Unit) {
    val focus = LocalFocusManager.current
    val recording = state.active
    val presence = when (state.presence) {
        null -> "Presença desconhecida"
        0 -> "Sala vazia pela estimativa da câmera"
        1 -> "1 pessoa detectada"
        else -> "${state.presence} pessoas detectadas"
    }
    Text("Reunião", style = MaterialTheme.typography.headlineSmall)
    Notice(presence)
    if (recording != null || state.capturing) {
        val label = if (recording == null) "Ativando o microfone…" else status(recording)
        Text(label, style = MaterialTheme.typography.titleLarge, modifier = Modifier.semantics {
            liveRegion = LiveRegionMode.Polite
        })
        if (recording != null && !recording.isNull("levelDbfs") && state.capturing && state.reconnecting == null) {
            Text("Nível recebido · ${recording.optDouble("levelDbfs")} dBFS")
        }
        if (recording != null && !recording.isNull("presenceVerified") && !recording.optBoolean("presenceVerified")) {
            Text("Presença não verificada na abertura.")
        }
        if (recording?.optString("quality") == "INCOMPLETA") Text("A captura registrou uma lacuna de conexão.")
        if (recording?.optString("processing") in listOf("PENDENTE", "TRANSCREVENDO", "GERANDO_ATA")) {
            LinearProgressIndicator(Modifier.fillMaxWidth())
        }
    }
    if (state.capturing) {
        OutlinedButton(onClick = home::stop, enabled = !state.stopping, modifier = Modifier.fillMaxWidth()) {
            Text(if (state.stopping) "Finalizando envio…" else "Parar microfone")
        }
    } else {
        OutlinedTextField(
            state.title, home::title, Modifier.fillMaxWidth(), singleLine = true,
            label = { Text("Título da reunião") }, placeholder = { Text("Reunião") },
        )
        Text("Avise os participantes antes de ativar o microfone. O áudio é enviado ao notebook.")
        Button(onClick = {
            focus.clearFocus()
            start()
        }, modifier = Modifier.fillMaxWidth()) {
            Text("Ativar microfone")
        }
        if (recording?.optString("processing") == "FALHOU") {
            OutlinedButton(onClick = { home.retry(recording.getString("id")) }) { Text("Tentar processar novamente") }
        }
        if (recording?.optString("processing") == "PRONTA") {
            OutlinedButton(onClick = { openMinutes(recording.getString("id")) }) { Text("Ver ata") }
        }
    }
}

@Composable
private fun History(meetings: List<JSONObject>?, connected: Boolean, select: (String) -> Unit) {
    Text("Histórico de reuniões", style = MaterialTheme.typography.headlineSmall)
    when {
        meetings == null && connected -> LinearProgressIndicator(Modifier.fillMaxWidth())
        meetings == null -> Text("O histórico será carregado ao reconectar.")
        meetings.isEmpty() -> Text("As reuniões registradas nesta sala aparecem aqui.")
        else -> meetings.forEach { meeting ->
            Surface(
                onClick = { select(meeting.getString("id")) },
                shape = MaterialTheme.shapes.medium,
                color = MaterialTheme.colorScheme.surfaceVariant,
                modifier = Modifier.fillMaxWidth(),
            ) {
                Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                    Text(meeting.getString("title"), style = MaterialTheme.typography.titleMedium)
                    Text("${whenStarted(meeting)} · ${status(meeting)}", style = MaterialTheme.typography.bodyMedium)
                }
            }
        }
    }
}

@Composable
private fun Meeting(meeting: JSONObject?, home: HomeViewModel) {
    TextButton(onClick = { home.select(null) }) { Text("Voltar ao histórico") }
    if (meeting == null) {
        Text("Carregando os dados da reunião…")
        return
    }
    Text(meeting.getString("title"), style = MaterialTheme.typography.headlineSmall)
    Text("${whenStarted(meeting)} · ${status(meeting)}")
    if (meeting.optString("quality") == "INCOMPLETA") Notice("Captura incompleta. O áudio pode ter lacunas.")
    if (!meeting.isNull("presenceVerified") && !meeting.optBoolean("presenceVerified")) {
        Text("Presença não verificada na abertura.")
    }
    if (meeting.optString("processing") == "FALHOU") {
        Notice(meeting.optString("error"), error = true)
        OutlinedButton(onClick = { home.retry(meeting.getString("id")) }) { Text("Tentar processar novamente") }
    }
    val minutes = meeting.optJSONObject("minutes")
    if (minutes != null) {
        Heading("Ata")
        SelectionContainer { Text(minutes.getString("summary")) }
        val decisions = minutes.getJSONArray("decisions")
        Heading("Decisões")
        if (decisions.length() == 0) Text("Nenhuma decisão registrada.")
        for (index in 0 until decisions.length()) {
            SelectionContainer { Text(decisions.getString(index)) }
        }
        Heading("Ações")
        val actions = minutes.getJSONArray("action_items")
        if (actions.length() == 0) Text("Nenhuma ação registrada.")
        actions.eachObject { action ->
            SelectionContainer {
                Column {
                    Text(action.getString("task"), fontWeight = FontWeight.Medium)
                    val owner = action.optString("owner").ifBlank { "Responsável não informado" }
                    val due = action.optString("due").ifBlank { "Prazo não informado" }
                    Text("$owner · $due")
                }
            }
        }
    }
    Heading("Transcrição")
    val transcript = meeting.optJSONArray("transcript")
    if (transcript == null || transcript.length() == 0) Text("Nenhum trecho disponível.")
    transcript?.eachObject { segment ->
        SelectionContainer {
            Text("${offset(segment.getDouble("start"))}   ${segment.getString("text")}")
        }
    }
}

@Composable
private fun Heading(value: String) {
    Text(value, style = MaterialTheme.typography.titleLarge, modifier = Modifier.padding(top = 10.dp))
}

@Composable
private fun Notice(value: String, error: Boolean = false) {
    val palette = MaterialTheme.colorScheme
    Surface(
        modifier = Modifier.fillMaxWidth(),
        shape = MaterialTheme.shapes.medium,
        color = if (error) palette.errorContainer else palette.secondaryContainer,
        contentColor = if (error) palette.onErrorContainer else palette.onSecondaryContainer,
    ) {
        Text(value, Modifier.padding(14.dp).semantics { liveRegion = LiveRegionMode.Polite })
    }
}

private inline fun JSONArray.eachObject(action: (JSONObject) -> Unit) {
    for (index in 0 until length()) action(getJSONObject(index))
}

private fun whenStarted(meeting: JSONObject): String =
    DateFormat.getDateTimeInstance(DateFormat.SHORT, DateFormat.SHORT)
        .format(Date((meeting.optDouble("startedAt", 0.0) * 1000).toLong()))

private fun offset(seconds: Double): String {
    val total = seconds.toInt()
    return "${total / 60}:${(total % 60).toString().padStart(2, '0')}"
}

private fun status(meeting: JSONObject): String = when (meeting.optString("processing")) {
    "PENDENTE" -> "Na fila de processamento"
    "TRANSCREVENDO" -> "Transcrevendo no notebook"
    "GERANDO_ATA" -> "Gerando a ata"
    "PRONTA" -> "Ata disponível no histórico"
    "FALHOU" -> "Falha no processamento"
    "DESCARTADA" -> "Encerrada sem reunião detectada"
    else -> when (meeting.optString("state")) {
        "ABERTA" -> "Reunião em gravação"
        "ENCERRANDO" -> "Aguardando retorno da fala"
        "SUSPENSA" -> "Captura suspensa"
        "FECHADA" -> "Encerrada"
        else -> "Aguardando contexto de reunião"
    }
}
