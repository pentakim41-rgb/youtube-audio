#!/data/data/com.termux/files/usr/bin/bash
# 휴대폰(Termux)에서 웹 버전 실행/관리.  install.sh 가 'yta' 명령으로 연결해 둔다.
#
#   yta           서버를 켜고(이미 켜져 있으면 그대로) 브라우저로 화면 열기
#   yta add URL   목록에 링크 추가 후 화면 열기 (유튜브 앱 '공유 → Termux' 가 이것을 부른다)
#   yta stop      서버 끄기
#   yta restart   서버 다시 켜기
#   yta update    프로그램 + yt-dlp 최신으로 업데이트 (유튜브 쪽이 바뀌어 안 받아질 때)
#   yta log       서버 로그 보기

APP_DIR="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"
PORT=8765
URL="http://127.0.0.1:$PORT"
LOG="$HOME/.yta_server.log"

running() {
    curl -s -m 2 "$URL/api/me" >/dev/null 2>&1
}

start_server() {
    running && return 0
    termux-wake-lock 2>/dev/null  # 화면을 꺼도 다운로드가 멈추지 않도록
    (cd "$APP_DIR" && nohup python -m web.server --port "$PORT" --no-browser >"$LOG" 2>&1 &)
    for _ in $(seq 1 40); do
        running && return 0
        sleep 0.5
    done
    echo "서버를 시작하지 못했습니다. 로그:"
    tail -n 20 "$LOG"
    return 1
}

stop_server() {
    pkill -f "web.server --port $PORT" 2>/dev/null
    termux-wake-unlock 2>/dev/null
}

open_ui() {
    termux-open-url "$URL"
}

case "$1" in
    stop)
        stop_server
        echo "서버를 껐습니다."
        ;;
    restart)
        stop_server
        sleep 1
        start_server && open_ui
        ;;
    update)
        (cd "$APP_DIR" && git pull --ff-only) || echo "[경고] 프로그램 업데이트 실패 (git pull)"
        pip install -U "yt-dlp[default]" 2>/dev/null || pip install -U yt-dlp yt-dlp-ejs
        stop_server
        sleep 1
        start_server && open_ui
        ;;
    add)
        start_server || exit 1
        body=$(python -c 'import json, sys; print(json.dumps({"jobs": [{"url": sys.argv[1], "playlist": False}]}))' "$2")
        curl -s -X POST -H "Content-Type: application/json" -H "X-Requested-With: yta" -d "$body" "$URL/api/add" >/dev/null
        open_ui
        ;;
    log)
        tail -n 50 "$LOG"
        ;;
    *)
        start_server && open_ui
        ;;
esac
