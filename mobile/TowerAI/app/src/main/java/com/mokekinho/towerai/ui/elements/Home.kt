package com.mokekinho.towerai.ui.elements

import android.Manifest
import android.app.Activity
import android.content.pm.PackageManager
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import com.mokekinho.towerai.ui.state.HomeEvent
import com.mokekinho.towerai.ui.state.HomeViewModel
import androidx.lifecycle.viewmodel.compose.viewModel


@Composable
fun Home(
    modifier: Modifier = Modifier
) {

    val viewModel: HomeViewModel = viewModel()
    val state by viewModel.state.collectAsState()

    val serverIp = state.serverIp
    val context = LocalContext.current
    val activity = context as? Activity // Transforma o contexto em Activity
    //val microphoneAccess = state.microphoneAccess

    val micPermission = rememberLauncherForActivityResult(
        contract = ActivityResultContracts.RequestPermission(),
        onResult ={ isGranted ->
            if(isGranted){
                viewModel.onEvent(HomeEvent.StartAudio)
            }
        }
    )

    Column(
        modifier = modifier.fillMaxSize(),
        horizontalAlignment = Alignment.CenterHorizontally
    ){
        OutlinedTextField(
            value = serverIp,
            onValueChange = {
                viewModel.onEvent(HomeEvent.ServerIpChanged(it))
            },
            label = {
                Text("Type the IP showed on your PC")
            }
        )

        Row(
            modifier = Modifier
                .fillMaxWidth()
            ,
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.Center
        ){
            TextButton(
                onClick = {
                    when {
                        ContextCompat.checkSelfPermission(
                            context,
                            Manifest.permission.RECORD_AUDIO
                        ) == PackageManager.PERMISSION_GRANTED -> {
                            viewModel.onEvent(HomeEvent.StartAudio)
                        }

                        else -> {
                            micPermission.launch(Manifest.permission.RECORD_AUDIO)
                        }
                    }
                }
            ) {
                Text("Connect")
            }
            Spacer(
                modifier = Modifier
                    .padding(10.dp)
            )
            TextButton(
                onClick = {

                }
            ) {
                Text("Stop")
            }
        }
    }

}