#!/data/data/com.termux/files/usr/bin/bash
# 휴대폰(Termux) 처음 설치. 프로그램 폴더에서:  bash android/install.sh
set -e

APP_DIR="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"
YTA="$APP_DIR/android/yta.sh"

echo "=== [1/5] 휴대폰 저장소 권한 ==="
echo "  잠시 후 뜨는 창에서 '허용'을 누르세요. (곡을 Music/추출사운드 에 저장하려면 필요)"
termux-setup-storage || true
sleep 3

echo "=== [2/5] 필요한 프로그램 설치 (python, ffmpeg, node) ==="
pkg update -y
pkg install -y python ffmpeg nodejs-lts python-pillow git curl

echo "=== [3/5] 파이썬 패키지 설치 (yt-dlp, mutagen) ==="
pip_install() {
    pip install -U "$@" || pip install -U --break-system-packages "$@"
}
# [default] 에 들어 있는 일부 패키지는 휴대폰에서 빌드가 실패할 수 있어, 실패하면 꼭 필요한 것만 설치
pip_install "yt-dlp[default]" mutagen || pip_install yt-dlp yt-dlp-ejs mutagen
python -c "import PIL" 2>/dev/null || pip_install pillow

echo "=== [4/5] 'yta' 명령, 유튜브 앱 공유, 홈 화면 바로가기 연결 ==="
chmod +x "$YTA"
ln -sf "$YTA" "$PREFIX/bin/yta"

# 유튜브 앱에서 '공유 → Termux' 를 누르면 이 스크립트가 링크를 받는다
mkdir -p "$HOME/bin"
if [ -f "$HOME/bin/termux-url-opener" ] && ! grep -q "yta.sh" "$HOME/bin/termux-url-opener"; then
    cp "$HOME/bin/termux-url-opener" "$HOME/bin/termux-url-opener.bak"
    echo "  (기존 termux-url-opener 는 termux-url-opener.bak 으로 백업했습니다)"
fi
cat > "$HOME/bin/termux-url-opener" <<EOF
#!/data/data/com.termux/files/usr/bin/bash
exec bash "$YTA" add "\$1"
EOF
chmod +x "$HOME/bin/termux-url-opener"

# Termux:Widget 앱을 설치하면 홈 화면 위젯으로 바로 실행할 수 있다 (선택)
mkdir -p "$HOME/.shortcuts"
cat > "$HOME/.shortcuts/유튜브음악" <<EOF
#!/data/data/com.termux/files/usr/bin/bash
exec bash "$YTA"
EOF
chmod +x "$HOME/.shortcuts/유튜브음악"

echo "=== [5/5] 확인 ==="
python -c "import yt_dlp, mutagen, PIL; print('  yt-dlp', yt_dlp.version.__version__)"
command -v ffmpeg >/dev/null && echo "  ffmpeg OK"
command -v node >/dev/null && echo "  node $(node --version)"
[ -w /storage/emulated/0 ] && echo "  저장소 권한 OK" || echo "  [주의] 저장소 권한이 없습니다. termux-setup-storage 를 다시 실행해 '허용'을 누르세요."

echo
echo "설치 완료!  이제 'yta' 를 입력하면 실행됩니다."
echo "유튜브 앱에서 공유 → Termux 를 누르면 목록에 바로 추가됩니다."
