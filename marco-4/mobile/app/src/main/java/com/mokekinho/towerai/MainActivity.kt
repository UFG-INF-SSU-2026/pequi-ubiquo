package com.mokekinho.towerai

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.lifecycle.ViewModelProvider
import com.mokekinho.towerai.ui.MainScreen
import com.mokekinho.towerai.ui.state.HomeViewModel
import com.mokekinho.towerai.ui.theme.TowerAITheme

class MainActivity : ComponentActivity() {
    private val home by lazy { ViewModelProvider(this)[HomeViewModel::class.java] }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContent { TowerAITheme { MainScreen(home) } }
    }

    override fun onStop() {
        if (!isChangingConfigurations) home.stop()
        super.onStop()
    }
}
