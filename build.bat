@echo off
chcp 65001 >nul
REM 배포용 exe 빌드 (Windows, PyInstaller).
REM 결과: 배포용 압축 파일 dist\YouTubeAudio_날짜_시간.zip 하나 (압축을 풀면 YouTubeAudio\YouTubeAudio.exe)
REM bin\ffmpeg.exe, bin\deno.exe 가 있으면 함께 묶는다. deno.exe 가 없으면 자동으로 내려받는다.
REM (Deno 는 최신 yt-dlp 가 유튜브 추출에 쓰는 JavaScript 런타임. 받는 사람 PC 에 따로 설치할 필요가 없도록 포함)
cd /d "%~dp0"

if not exist .venv-build (
    echo [1/4] 빌드용 가상환경 만드는 중...
    py -3.12 -m venv .venv-build || python -m venv .venv-build
)
REM activate 대신 가상환경의 python 을 직접 쓴다
REM (activate 가 안 먹으면 PC 에 설치된 옛 yt-dlp 로 빌드되는 문제가 있었음)
set PY=.venv-build\Scripts\python.exe
if not exist %PY% goto :fail
echo [2/4] 패키지 설치 중...
%PY% -m pip install -q --upgrade pip
REM yt-dlp 는 유튜브 변경에 맞춰 자주 갱신되므로 빌드할 때마다 최신으로 올린다
%PY% -m pip install -q -U "yt-dlp[default]" yt-dlp-ejs || goto :fail
%PY% -m pip install -q "mutagen>=1.47" "Pillow>=10.0" "customtkinter>=5.2" "imageio-ffmpeg>=0.5" pyinstaller || goto :fail

if not exist bin\deno.exe (
    echo [3/4] Deno 내려받는 중...
    if not exist bin mkdir bin
    powershell -NoProfile -Command "Invoke-WebRequest 'https://github.com/denoland/deno/releases/latest/download/deno-x86_64-pc-windows-msvc.zip' -OutFile \"$env:TEMP\deno.zip\" -UseBasicParsing; Expand-Archive \"$env:TEMP\deno.zip\" -DestinationPath bin -Force; Remove-Item \"$env:TEMP\deno.zip\"" || goto :fail
)

set ADDBIN=--add-binary "bin\deno.exe;bin"
if exist bin\ffmpeg.exe set ADDBIN=%ADDBIN% --add-binary "bin\ffmpeg.exe;bin"

REM 빌드 시각("날짜_시간")을 exe 에 넣어 프로그램 오른쪽 위에 표시한다 (어떤 버전인지 확인용)
if not exist build mkdir build
%PY% -c "import datetime; open('build/build_stamp.txt', 'w', encoding='utf-8').write(datetime.datetime.now().strftime('%%Y%%m%%d_%%H%%M'))" || goto :fail
set /p STAMP=<build\build_stamp.txt
for /f %%v in ('%PY% -c "from yt_dlp.version import __version__; print(__version__)"') do set YTDLP=%%v

echo [4/4] exe 만드는 중... (몇 분 걸립니다)
%PY% -m PyInstaller --noconfirm --clean --windowed --name YouTubeAudio ^
  --collect-all customtkinter --collect-all yt_dlp --collect-all yt_dlp_ejs --collect-all imageio_ffmpeg ^
  --exclude-module pytest ^
  --add-data "build\build_stamp.txt;." ^
  %ADDBIN% main.py || goto :fail

REM zip 이름에도 빌드 시각을 넣는다 (예: YouTubeAudio_20260927_2039.zip)
set ZIPNAME=YouTubeAudio_%STAMP%
if exist dist\%ZIPNAME%.zip del dist\%ZIPNAME%.zip
%PY% -c "import shutil; shutil.make_archive('dist/%ZIPNAME%', 'zip', 'dist', 'YouTubeAudio')" || goto :fail
REM 결과물은 zip 하나만 남긴다 (폴더는 zip 과 내용이 같음)
rmdir /s /q dist\YouTubeAudio

echo.
echo 완료: dist\%ZIPNAME%.zip  (yt-dlp %YTDLP%)
echo 압축을 풀고 YouTubeAudio.exe 실행
echo.

REM 깃허브 Releases 에 올리면, 배포판을 켤 때 새 버전을 자동으로 받아 업데이트한다
choice /c YN /m "깃허브에 올려서 자동 업데이트로 배포할까요"
if errorlevel 2 goto :done
where gh >nul 2>nul || (echo gh ^(GitHub CLI^) 가 없습니다. winget install GitHub.cli 후 gh auth login 하세요. & goto :done)
gh release create v%STAMP% "dist\%ZIPNAME%.zip" --title "%STAMP%" --notes "빌드 %STAMP% (yt-dlp %YTDLP%)" || (echo 올리기 실패. gh auth login 으로 로그인했는지 확인하세요. & goto :done)
echo 올리기 완료: 배포판을 켜면 새 버전으로 업데이트할지 물어봅니다.

:done
pause
exit /b 0

:fail
echo.
echo 빌드 실패. 위 오류 메시지를 확인하세요.
pause
exit /b 1
