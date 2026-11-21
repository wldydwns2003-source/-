@echo off
chcp 65001 >nul
cd /d "%~dp0"
if "%~1"=="" (
  echo 원본 영상 파일을 이 아이콘 위로 끌어다 놓으세요.
  pause
  exit /b
)
py make_short.py --review %*
echo.
explorer "%~dp0완성"
pause
