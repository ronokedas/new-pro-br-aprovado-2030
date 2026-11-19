@echo off
chcp 65001 > nul
echo ========================================================
echo   RADAR DE ANTECIPAÇÃO M1 - BTCUSDT ^| IA SCALP
echo ========================================================
echo.
echo [1] Iniciar Localmente (Python FastAPI + WebSocket)
echo [2] Iniciar via Docker (docker-compose)
echo.
set /p op="Escolha a opção (1 ou 2) [padrao: 1]: "
if "%op%"=="" set op=1

if "%op%"=="1" (
    echo.
    echo [INFO] Iniciando servidor em http://localhost:8888 ...
    python -m uvicorn api.server:app --host 0.0.0.0 --port 8888
) else (
    echo.
    echo [INFO] Iniciando via Docker Compose...
    docker compose up --build
)
pause
