package com.mokekinho.towerai

import com.mokekinho.towerai.util.CaptureClock
import org.junit.Assert.assertEquals
import org.junit.Test

class CaptureClockTest {
    @Test
    fun datesEachBlockFromTheServerClockAndKeepsItsOriginalAge() {
        val serverEpochMillis = 1_800_000_000_000L
        val clock = CaptureClock(serverEpochMillis, sentAtMillis = 12_000L, receivedAtMillis = 12_200L)

        val capturedAt = clock.unixSecondsAt(13_100L)
        val retriedAt = clock.unixSecondsAt(21_100L)

        assertEquals(1_800_000_001.0, capturedAt, 0.001)
        assertEquals(8.0, retriedAt - capturedAt, 0.001)
    }
}
