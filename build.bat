@echo off
chcp 65001 >nul
REM 배포용 exe 빌드 (Windows, PyInstaller).
REM 결과: dist\YouTubeAudio\YouTubeAudio.exe  +  배포용 압축 파일 dist\YouTubeAudio.zip
REM bin\ffmpeg.exe, bin\deno.exe 가 있으면 함께 묶는다. deno.exe 가 없으면 자동으로 내려받는다.
REM (Deno 는 최신 yt-dlp 가 유튜브 추출에 쓰는 JavaScript 런타임. 받는 사람 PC 에 따로 설치할 필요가 없도록 포함)
cd /d "%~dp0"

if not exist .venv-build (
    echo [1/4] 빌드용 가상환경 만드는 중...
    py -3.12 -m venv .venv-build || python -m venv .venv-build
)
call .venv-build\Scripts\activate
echo [2/4] 패키지 설치 중...
python -m pip install -q --upgrade pip
python -m pip install -q "yt-dlp[default]>=2025.10.22" "mutagen>=1.47" "Pillow>=10.0" "customtkinter>=5.2" "imageio-ffmpeg>=0.5" pyinstaller || goto :fail

if not exist bin\deno.exe (
    echo [3/4] Deno 내려받는 중...
    if not exist bin mkdir bin
    powershell -NoProfile -Command "Invoke-WebRequest 'https://github.com/denoland/deno/releases/latest/download/deno-x86_64-pc-windows-msvc.zip' -OutFile \"$env:TEMP\deno.zip\" -UseBasicParsing; Expand-Archive \"$env:TEMP\deno.zip\" -DestinationPath bin -Force; Remove-Item \"$env:TEMP\deno.zip\"" || goto :fail
)

set ADDBIN=--add-binary "bin\deno.exe;bin"
if exist bin\ffmpeg.exe set ADDBIN=%ADDBIN% --add-binary "bin\ffmpeg.exe;bin"

echo [4/4] exe 만드는 중... (몇 분 걸립니다)
pyinstaller --noconfirm --clean --windowed --name YouTubeAudio ^
  --collect-all customtkinter --collect-all yt_dlp --collect-all yt_dlp_ejs --collect-all imageio_ffmpeg ^
  --exclude-module pytest ^
  %ADDBIN% main.py || goto :fail

if exist dist\YouTubeAudio.zip del dist\YouTubeAudio.zip
python -c "import shutil; shutil.make_archive('dist/YouTubeAudio', 'zip', 'dist', 'YouTubeAudio')" || goto :fail

echo.
echo 완료: dist\YouTubeAudio\YouTubeAudio.exe
echo 배포용: dist\YouTubeAudio.zip  (압축을 풀고 YouTubeAudio.exe 실행)
echo 참고: exe 판은 yt-dlp 자동 업데이트가 안 되므로, 유튜브가 바뀌면 다시 빌드하세요.
pause
exit /b 0

:fail
echo.
echo 빌드 실패. 위 오류 메시지를 확인하세요.
pause
exit /b 1
