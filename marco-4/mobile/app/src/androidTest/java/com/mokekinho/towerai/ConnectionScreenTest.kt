package com.mokekinho.towerai

import android.Manifest
import android.util.Base64
import androidx.compose.ui.semantics.SemanticsActions
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.assertIsEnabled
import androidx.compose.ui.test.hasText
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollTo
import androidx.compose.ui.test.performSemanticsAction
import androidx.compose.ui.test.performTextInput
import androidx.lifecycle.ViewModelProvider
import androidx.test.platform.app.InstrumentationRegistry
import androidx.test.rule.GrantPermissionRule
import com.mokekinho.towerai.ui.state.HomeViewModel
import org.junit.Assume.assumeTrue
import org.junit.Rule
import org.junit.Test

class ConnectionScreenTest {
    @get:Rule
    val permission = GrantPermissionRule.grant(Manifest.permission.RECORD_AUDIO)

    @get:Rule
    val compose = createAndroidComposeRule<MainActivity>()

    @Test
    fun connectionScreenRequiresTheLocalCertificate() {
        compose.onNodeWithText("Conectar à sala").assertIsDisplayed()
        compose.onNodeWithText("Conectar").performScrollTo().performSemanticsAction(SemanticsActions.OnClick)
        compose.onNodeWithText("Importe o ca.pem do notebook para validar a conexão.").assertIsDisplayed()
    }

    @Test
    fun connectsOverTlsAndReceivesMicrophoneAcknowledgement() {
        val args = InstrumentationRegistry.getArguments()
        val origin = args.getString("towerOrigin").orEmpty()
        val token = args.getString("towerToken").orEmpty()
        val ca = args.getString("towerCa").orEmpty()
        val meetingTitle = args.getString("towerMeetingTitle").orEmpty()
        assumeTrue(origin.isNotEmpty() && token.isNotEmpty() && ca.isNotEmpty())
        compose.runOnUiThread {
            ViewModelProvider(compose.activity)[HomeViewModel::class.java].importCa(Base64.decode(ca, Base64.DEFAULT))
        }
        compose.onNodeWithText("Endereço do servidor").performTextInput(origin)
        compose.onNodeWithText("Credencial do microfone").performTextInput(token)
        compose.onNodeWithText("Conectar").performScrollTo().performSemanticsAction(SemanticsActions.OnClick)
        awaitText("Ativar microfone")
        compose.onNodeWithText("Ativar microfone").performScrollTo().assertIsEnabled()
            .performSemanticsAction(SemanticsActions.OnClick)
        awaitText("dBFS", substring = true)
        compose.onNodeWithText("Parar microfone").performScrollTo().performSemanticsAction(SemanticsActions.OnClick)
        awaitText("Ativar microfone")
        compose.onNodeWithText("Histórico").performClick()
        compose.onNodeWithText("Histórico de reuniões").assertIsDisplayed()
        if (meetingTitle.isNotEmpty()) {
            awaitText(meetingTitle)
            compose.onNodeWithText(meetingTitle).performScrollTo().performSemanticsAction(SemanticsActions.OnClick)
            awaitText("Ata")
            awaitText("Transcrição")
        }
    }

    private fun awaitText(value: String, substring: Boolean = false) {
        try {
            compose.waitUntil(timeoutMillis = 15_000) {
                compose.onAllNodes(hasText(value, substring = substring)).fetchSemanticsNodes().isNotEmpty()
            }
        } catch (error: Throwable) {
            val state = compose.runOnUiThread {
                ViewModelProvider(compose.activity)[HomeViewModel::class.java].state.value
            }
            throw AssertionError("Esperava $value. Captura: ${state.capturing}, erro: ${state.error}, rede: ${state.networkError}", error)
        }
    }
}
