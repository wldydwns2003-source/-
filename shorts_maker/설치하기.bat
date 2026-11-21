@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ==== 쇼츠 자동 제작기 설치 ====
where py >nul 2>nul
if errorlevel 1 (
  echo 파이썬이 없어요. 설치를 시작합니다...
  winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements
  echo.
  echo 파이썬 설치가 끝나면 이 창을 닫고 설치하기.bat 을 한 번 더 실행해 주세요.
  pause
  exit /b
)
where ffmpeg >nul 2>nul
if errorlevel 1 (
  echo ffmpeg 를 설치합니다...
  winget install -e --id Gyan.FFmpeg --accept-package-agreements --accept-source-agreements
)
echo 필요한 프로그램을 설치합니다...
py -m pip install --upgrade pip
py -m pip install -r requirements.txt
echo.
echo 원본 영상의 말소리 받아쓰기 기능을 설치합니다. (실패해도 쇼츠 제작은 됩니다)
py -m pip install faster-whisper
if not exist .env (
  copy .env.example .env >nul
  echo.
  echo 메모장이 열리면 API 키 두 개를 붙여넣고 저장하세요.
  notepad .env
)
echo.
echo 설치 완료! ffmpeg 를 방금 설치했다면 컴퓨터를 한 번 다시 켜 주세요.
pause
