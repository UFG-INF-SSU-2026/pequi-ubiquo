package com.mokekinho.towerai.util

import android.annotation.SuppressLint
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.util.Log
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okio.ByteString.Companion.toByteString

class AudioSender(
    val serverIp: String,
    val serverPort: Int,
) {

    val audioSource = MediaRecorder.AudioSource.MIC //Defini o dispositivo de entrada

    val sampleRate = 16000 // Frequencia (quantos amostras(de 16bits como selecionei em baixo) por segundo) nao estou usando a frequencia de CD(44100), estou usando a frequencia que o Flask quer

    val channelConfig = AudioFormat.CHANNEL_IN_MONO //Gravar em MONO mesmo, nao faz sentido ser esterio

    val audioFormat = AudioFormat.ENCODING_PCM_16BIT // Define o tamanho de cada amostra

    val bufferSize = AudioRecord.getMinBufferSize(
        sampleRate,
        channelConfig,
        audioFormat
    ) // calcula o tamanho do buffer seguro para dar empo do meu aplicativo ler e de o audio sobrescrever o que tem que ser sobreescrito.

    private var webSocket: WebSocket? = null
    private val client = OkHttpClient()

    private var isStreaming = false

    @SuppressLint("MissingPermission")
    suspend fun start(){
        isStreaming = true
        // Conectando ao WebSocket do Flask
        val roomName = "geral" // Ou o nome da sala que você está utilizando no chat
        val url = "ws://$serverIp:$serverPort/voice/ws/voice/$roomName"
        val request = Request.Builder().url(url).build()

        webSocket = client.newWebSocket(request, object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {
                Log.d("AudioSender", "Conectado ao WebSocket do Flask!")
            }

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                Log.e("AudioSender", "Erro no WebSocket: ${t.message}")
            }
        })



        // Adicionar a verificação de permissão

        val audioRecord = AudioRecord(
            audioSource,
            sampleRate,
            channelConfig,
            audioFormat,
            bufferSize
        ) // crio com base nos aprametros que já estabeleci


        audioRecord.startRecording() // Ele começa a gravar
        val buffer = ByteArray(3200)

        // Lendo os dados e enviando para o flask
        try {
            while (isStreaming) {
                val readSize = audioRecord.read(buffer, 0, buffer.size)
                if (readSize > 0) {
                    // 3. Envia os bytes crus (PCM16) diretamente pelo WebSocket
                    val byteString = buffer.copyOf(readSize).toByteString()
                    webSocket?.send(byteString)
                }
            }
        } catch (e: Exception) {
            Log.e("AudioSender", "Erro na gravação: ${e.message}")
        } finally {
            audioRecord.stop()
            audioRecord.release()
            webSocket?.close(1000, "Fim da transmissão")
        }

    }

    fun stop() {
        isStreaming = false
        webSocket?.close(1000, "Usuário parou")
        webSocket = null
    }

}