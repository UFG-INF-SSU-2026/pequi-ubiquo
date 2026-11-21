param([Parameter(Mandatory=$true)][string]$Saida)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Media.SpeechSynthesis.SpeechSynthesizer, Windows.Media.SpeechSynthesis, ContentType=WindowsRuntime]
$streamType = [Windows.Media.SpeechSynthesis.SpeechSynthesisStream, Windows.Media.SpeechSynthesis, ContentType=WindowsRuntime]
$asTask = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.IsGenericMethod -and $_.GetParameters().Count -eq 1 -and
        $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
} | Select-Object -First 1

$voiceInfo = [Windows.Media.SpeechSynthesis.SpeechSynthesizer]::AllVoices |
    Where-Object Language -eq 'pt-BR' | Select-Object -First 1
if (-not $voiceInfo) { throw 'Instale uma voz pt-BR no Windows ou forneca outro WAV PCM16.' }

$voice = New-Object Windows.Media.SpeechSynthesis.SpeechSynthesizer
$voice.Voice = $voiceInfo
$ate = 'at' + [char]0xE9
$text = "Reuni$([char]0xE3)o semanal do projeto. Decidimos publicar o manual na sexta-feira. A Ana vai revisar o manual $ate quinta-feira. Bruno vai testar o aplicativo $ate quarta-feira."
$speech = $voice.SynthesizeTextToStreamAsync($text)
$task = $asTask.MakeGenericMethod($streamType).Invoke($null, @($speech))
$task.Wait()
$inputStream = [System.IO.WindowsRuntimeStreamExtensions]::AsStreamForRead($task.Result)
$outputStream = [System.IO.File]::Create($Saida)
try { $inputStream.CopyTo($outputStream) }
finally { $outputStream.Dispose(); $inputStream.Dispose(); $voice.Dispose() }
