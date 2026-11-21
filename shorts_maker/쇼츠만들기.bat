@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not "%~1"=="" goto run
echo.
echo  원본 영상 파일을 이 검은 창 위로 끌어다 놓은 뒤 Enter 를 누르세요.
echo.
set "VIDS="
set /p "VIDS=영상: "
if not defined VIDS (
  echo 영상이 없어서 종료합니다.
  pause
  exit /b
)
py make_short.py %VIDS%
goto done
:run
py make_short.py %*
:done
echo.
if exist "%~dp0완성" explorer "%~dp0완성"
echo 끝났어요. 이 창은 아무 키나 누르면 닫힙니다.
pause >nul
