@echo off
chcp 65001 >nul
setlocal
rem Usa a pasta deste arquivo, mesmo quando ele e aberto por dois cliques.
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
    echo Instale Python 3.11 ou superior e marque a opcao Add Python to PATH.
    echo Depois abra este arquivo novamente.
    pause
    exit /b 1
)
python "%~dp0marco-3\executar.py" %*
if errorlevel 1 (
    echo.
    echo A execucao encontrou um problema. Leia a orientacao acima.
    pause
    exit /b 1
)
