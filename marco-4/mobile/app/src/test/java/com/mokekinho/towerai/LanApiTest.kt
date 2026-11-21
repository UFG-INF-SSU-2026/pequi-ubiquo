package com.mokekinho.towerai

import com.mokekinho.towerai.util.LanApi
import org.junit.Assert.assertThrows
import org.junit.Test

class LanApiTest {
    @Test
    fun rejectsCleartextAndOriginsContainingCredentialsOrRoutes() {
        for (address in listOf("http://192.168.1.2", "https://user@towerai.local", "https://towerai.local/v1")) {
            assertThrows(IllegalArgumentException::class.java) {
                LanApi(address, "sample_token_for_unit_test", byteArrayOf())
            }
        }
    }

    @Test
    fun rejectsCredentialContainingLineBreakBeforeConnecting() {
        assertThrows(IllegalArgumentException::class.java) {
            LanApi("https://towerai.local:8443", "invalid\r\nAuthorization:", byteArrayOf())
        }
    }
}
