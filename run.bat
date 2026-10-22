@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist .venv (
  echo Создаю окружение и ставлю зависимости...
  python -m venv .venv || goto :error
  .venv\Scripts\python -m pip install -q -r requirements.txt || goto :error
)
if not exist .env copy .env.example .env >nul
start "" http://localhost:8000
.venv\Scripts\python -m uvicorn app.main:app --port 8000
goto :eof
:error
echo Не получилось. Проверь, что установлен Python 3.11 или новее.
pause
