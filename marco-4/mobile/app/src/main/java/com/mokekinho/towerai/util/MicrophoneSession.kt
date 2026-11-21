package com.mokekinho.towerai.util

import android.annotation.SuppressLint
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.os.SystemClock
import java.io.IOException
import javax.net.ssl.SSLException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import org.json.JSONObject

class MicrophoneSession(
    private val id: String,
    private val api: LanApi,
    private val title: String,
    private val update: (JSONObject) -> Unit,
    private val notice: (String?) -> Unit,
) {
    @Volatile private var stopping = false

    fun stop() {
        stopping = true
    }

    suspend fun run() = coroutineScope {
        val started = send { api.start(id, title) }
        var latest = started.body
        update(latest)
        if (latest.optString("state") == "FECHADA") return@coroutineScope
        val pending = Channel<Chunk>(capacity = 120)
        val microphone = launch(Dispatchers.IO) { readMicrophone(pending, started.clock) }
        try {
            for (chunk in pending) {
                latest = send { api.chunk(id, chunk.sequence, chunk.timestamp, chunk.bytes) }
                update(latest)
                if (latest.optString("state") == "FECHADA") break
            }
        } finally {
            stop()
            pending.cancel()
            microphone.join()
        }
        if (latest.optString("state") != "FECHADA") update(send { api.finish(id) })
    }

    private suspend fun <T> send(action: () -> T): T {
        val deadline = SystemClock.elapsedRealtime() + 115_000
        var retryDelay = 500L
        while (true) {
            try {
                return action().also { notice(null) }
            } catch (error: IOException) {
                if (error is SSLException || (error is ApiFailure && error.status in 400..499 && error.status != 408)) {
                    throw error
                }
                if (SystemClock.elapsedRealtime() >= deadline) throw error
                notice("Conexão interrompida. Tentando enviar o áudio pendente.")
                delay(retryDelay)
                retryDelay = (retryDelay * 2).coerceAtMost(4000)
            }
        }
    }

    private data class Chunk(val sequence: Int, val timestamp: Double, val bytes: ByteArray)

    @SuppressLint("MissingPermission")
    private fun readMicrophone(output: Channel<Chunk>, clock: CaptureClock) {
        val rate = 16000
        val format = AudioFormat.ENCODING_PCM_16BIT
        val mono = AudioFormat.CHANNEL_IN_MONO
        var audio: AudioRecord? = null
        try {
            if (stopping) return
            val minimum = AudioRecord.getMinBufferSize(rate, mono, format)
            check(minimum > 0) { "O microfone não suporta a captura solicitada." }
            val recorder = AudioRecord(MediaRecorder.AudioSource.MIC, rate, mono, format, maxOf(minimum, rate * 2))
            audio = recorder
            check(recorder.state == AudioRecord.STATE_INITIALIZED) { "O microfone está indisponível." }
            recorder.startRecording()
            var sequence = 0
            while (!stopping) {
                val samples = ShortArray(rate)
                var offset = 0
                while (offset < samples.size && !stopping) {
                    val count = recorder.read(samples, offset, minOf(1600, rate - offset), AudioRecord.READ_BLOCKING)
                    check(count > 0) { "O microfone interrompeu a captura." }
                    offset += count
                }
                if (offset == 0) break
                val capturedAt = clock.unixSecondsAt(SystemClock.elapsedRealtime())
                val pcm = ByteArray(rate * 2)
                for (index in 0 until offset) {
                    val sample = samples[index].toInt()
                    pcm[index * 2] = sample.toByte()
                    pcm[index * 2 + 1] = (sample shr 8).toByte()
                }
                val item = Chunk(sequence++, capturedAt, pcm)
                if (output.trySend(item).isFailure) {
                    if (!stopping) output.close(IOException("O limite de áudio pendente foi atingido. Reabra a reunião após reconectar."))
                    return
                }
            }
        } catch (error: Exception) {
            output.close(error)
        } finally {
            audio?.let {
                try {
                    if (it.recordingState == AudioRecord.RECORDSTATE_RECORDING) it.stop()
                } finally {
                    it.release()
                }
            }
            output.close()
        }
    }
}
