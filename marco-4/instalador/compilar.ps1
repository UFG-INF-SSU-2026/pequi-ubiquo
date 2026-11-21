param(
    [string]$Python = (Join-Path $PSScriptRoot '..\servidor\.venv\Scripts\python.exe'),
    [string]$Inno = 'C:\Program Files (x86)\Inno Setup 6\ISCC.exe',
    [string]$Llama = (Join-Path $PSScriptRoot '..\.ferramentas\llama')
)

$ErrorActionPreference = 'Stop'
$installer = $PSScriptRoot
$server = (Resolve-Path (Join-Path $installer '..\servidor')).Path
$build = Join-Path $installer 'build'
$dist = Join-Path $build 'dist'
$licenseOutput = Join-Path $build 'licencas'
$llamaOutput = Join-Path $build 'llama'
$personModel = Join-Path $server 'modelos\object_detection_yolox_2022nov.onnx'

if (!(Test-Path -LiteralPath $Inno)) { throw 'Instale o Inno Setup 6 ou indique o caminho do ISCC.exe com -Inno.' }
if (!(Test-Path -LiteralPath (Join-Path $Llama 'llama-server.exe'))) { throw 'Indique a pasta do llama.cpp Windows x64 CPU com -Llama.' }
if (!(Test-Path -LiteralPath $personModel) -or
    (Get-FileHash -LiteralPath $personModel -Algorithm SHA256).Hash -ne 'c5c2d13e59ae883e6af3b45daea64af4833a4951c92d116ec270d9ddbe998063') {
    throw 'Pesos do YOLOX-S ausentes ou incorretos. Siga a seção da webcam em docs/implantacao.md.'
}

New-Item -ItemType Directory -Force -Path $build,$llamaOutput,$licenseOutput | Out-Null
Get-ChildItem -LiteralPath $Llama -File | Where-Object {
    $_.Name -like 'ggml*.dll' -or $_.Name -in @('libomp.dll','llama.dll','llama-common.dll','llama-server-impl.dll','llama-server.exe','mtmd.dll')
} | Copy-Item -Destination $llamaOutput

Copy-Item -Path (Join-Path $installer 'licencas\*') -Destination $licenseOutput -Recurse -Force
Copy-Item -LiteralPath (Join-Path $Llama 'LICENSE-LLVM-OpenMP') -Destination $licenseOutput
& $Python (Join-Path $installer 'licencas_python.py') $licenseOutput
if ($LASTEXITCODE -ne 0) { throw 'Não foi possível reunir as licenças dos componentes Python.' }

& $Python -m PyInstaller --noconfirm --onedir --windowed --name TowerAI-Servidor `
    --distpath $dist --workpath (Join-Path $build 'pyinstaller') --specpath $build `
    --paths $server --collect-data faster_whisper --copy-metadata faster-whisper --copy-metadata huggingface_hub `
    --hidden-import huggingface_hub._snapshot_download --hidden-import huggingface_hub.hf_api `
    --hidden-import huggingface_hub.file_download (Join-Path $server 'desktop_app.py')
if ($LASTEXITCODE -ne 0) { throw 'A compilação do executável falhou.' }

& $Inno (Join-Path $installer 'TowerAI-Servidor.iss')
if ($LASTEXITCODE -ne 0) { throw 'A compilação do instalador falhou.' }
