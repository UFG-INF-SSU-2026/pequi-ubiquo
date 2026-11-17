package com.mokekinho.towerai.util

import android.annotation.SuppressLint
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder

class AudioSender(
    val serverIp: String,
    val serverPort: Int,
) {

    val audioSource = MediaRecorder.AudioSource.MIC //Defini o dispositivo de entrada

    val sampleRate = 44100 // Frequencia (quantos amostras(de 16bits como selecionei em baixo) por segundo) to suando a frequencia de CD

    val channelConfig = AudioFormat.CHANNEL_IN_MONO //Gravar em MONO mesmo, nao faz sentido ser esterio

    val audioFormat = AudioFormat.ENCODING_PCM_16BIT // Define o tamanho de cada amostra

    val bufferSize = AudioRecord.getMinBufferSize(
        sampleRate,
        channelConfig,
        audioFormat
    ) // calcula o tamanho do buffer seguro para dar empo do meu aplicativo ler e de o audio sobrescrever o que tem que ser sobreescrito.

    @SuppressLint("MissingPermission")
    suspend fun start(){

        // Adicionar a verificação de permissão

        val audioRecord = AudioRecord(
            audioSource,
            sampleRate,
            channelConfig,
            audioFormat,
            bufferSize
        ) // crio com base nos aprametros que já estabeleci


        audioRecord.startRecording() // Ele começa a gravar

        // fazer a Logica de mandar os pacotes (se eu nao me engano via mqqt)

    }





}