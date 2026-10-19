@echo off
REM Uruchamia dashboard Invest Radar. Kliknij dwa razy ten plik.
cd /d "%~dp0"

if not exist .venv (
    echo Pierwsze uruchomienie - tworze srodowisko i instaluje zaleznosci...
    python -m venv .venv
    .venv\Scripts\python.exe -m pip install --upgrade pip
    .venv\Scripts\python.exe -m pip install -r requirements.txt
)

echo Startuje Invest Radar - za chwile otworzy sie przegladarka...
.venv\Scripts\python.exe -m streamlit run app.py

pause
