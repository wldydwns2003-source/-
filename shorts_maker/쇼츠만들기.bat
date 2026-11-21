@echo off
chcp 65001 >nul
cd /d "%~dp0"
if "%~1"=="" (
  echo 원본 영상 파일을 이 아이콘 위로 끌어다 놓으세요. 여러 개도 됩니다.
  pause
  exit /b
)
py make_short.py %*
echo.
explorer "%~dp0완성"
pause
