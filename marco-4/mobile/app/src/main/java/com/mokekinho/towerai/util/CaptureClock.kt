package com.mokekinho.towerai.util

internal class CaptureClock(
    private val serverEpochMillis: Long,
    sentAtMillis: Long,
    receivedAtMillis: Long,
) {
    private val anchorMillis = sentAtMillis + (receivedAtMillis - sentAtMillis) / 2

    fun unixSecondsAt(elapsedMillis: Long): Double =
        (serverEpochMillis + (elapsedMillis - anchorMillis)) / 1000.0
}
