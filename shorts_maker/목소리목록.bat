@echo off
chcp 65001 >nul
cd /d "%~dp0"
py make_short.py --list-voices > 목소리목록.txt
type 목소리목록.txt
echo.
echo 목소리 목록을 목소리목록.txt 로도 저장했어요.
echo 원하는 목소리의 맨 앞 ID 를 presets\default.json 의 typecast_voice_id 에 넣으세요.
pause
