package com.mokekinho.towerai.ui.state


import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.mokekinho.towerai.util.AudioSender
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch


data class HomeState(
    val serverIp: String = "",
    //val microphoneAccess : Boolean = false
)


sealed class HomeEvent{
    data class ServerIpChanged(val serverIp: String): HomeEvent()
    object StartAudio: HomeEvent()
    object StopAudio : HomeEvent()
    //data class MicrophoneAccessChanged(val microphoneAccess: Boolean): HomeEvent()
}

class HomeViewModel(): ViewModel() {

    private val _state = MutableStateFlow(HomeState())
    val state: StateFlow<HomeState> = _state.asStateFlow()

    private var audioSender: AudioSender? = null

    fun onEvent(event: HomeEvent) {
        when (event) {
            is HomeEvent.ServerIpChanged -> _state.update { it.copy(serverIp = event.serverIp) }
            //is HomeEvent.MicrophoneAccessChanged -> _state.update { it.copy(microphoneAccess = event.microphoneAccess) }
            is HomeEvent.StartAudio -> startAudio()
            is HomeEvent.StopAudio -> stopAudio()
        }
    }

    private fun startAudio(){
        viewModelScope.launch {
            val audioSender = AudioSender(
                serverIp = state.value.serverIp,
                serverPort = 5000
            )
            audioSender.start()
        }

    }

    private fun stopAudio(){
        // Para o WebSocket e o loop de gravação
        audioSender?.stop()
        audioSender = null
    }


}