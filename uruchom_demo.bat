@echo off
REM Uruchamia dashboard w trybie demo: fikcyjne dane, bez internetu i bez klucza API.
cd /d "%~dp0"

if not exist .venv (
    echo Pierwsze uruchomienie - tworze srodowisko i instaluje zaleznosci...
    python -m venv .venv
    .venv\Scripts\python.exe -m pip install --upgrade pip
    .venv\Scripts\python.exe -m pip install -r requirements.txt
)

set RADAR_DEMO=1
.venv\Scripts\python.exe -m streamlit run app.py

pause
