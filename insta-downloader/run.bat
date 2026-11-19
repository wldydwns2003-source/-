@echo off
chcp 65001 >nul
cd /d "%~dp0"
where python >nul 2>nul || (echo [!] Python이 없습니다. https://www.python.org/downloads/ 에서 설치하세요. ^(설치 시 "Add python.exe to PATH" 체크^) & pause & exit /b)
if not exist .venv (
  echo 처음 실행: 필요한 프로그램 설치 중...
  python -m venv .venv
)
call .venv\Scripts\activate
python -m pip install -q --upgrade pip
python -m pip install -q --upgrade -r requirements.txt
python app.py
pause
