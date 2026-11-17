package com.mokekinho.towerai.ui

import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp

@Composable
fun MainScreen(
    modifier: Modifier = Modifier
) {
    Surface( // isso aqui ajuda nas cores ficar certas, estudar sobre
        modifier = modifier
            .fillMaxSize(),
        color = MaterialTheme.colorScheme.background,
        contentColor = MaterialTheme.colorScheme.onBackground
    ){
        Scaffold(
            modifier = Modifier
                .padding(top = 10.dp)
        ) {
            Home(
                modifier = Modifier
                    .padding(it)
            )
        }
    }

}