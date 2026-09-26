"""웹 버전 서버: python -m web.server

데스크톱 판과 같은 core/ 를 그대로 쓰고, 화면만 브라우저로 옮긴 것이다.

  - 안드로이드(Termux): 휴대폰 안에서 서버를 돌리고 휴대폰 브라우저로 127.0.0.1 에 접속한다 (android/ 참고).
    곡은 휴대폰 Music/추출사운드 에 바로 저장된다.
  - 같은 기기(127.0.0.1)에서 접속하면 PIN 이 필요 없다.
  - 다른 기기에서 접속할 때는 PIN 으로 로그인하고, 완료된 곡을 '받기'로 그 기기에 저장한다 (여러 곡은 zip).

추가 패키지 없이 파이썬 내장 http.server 만 사용한다.
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import secrets
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import webbrowser
import zipfile
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

from config import (
    BROWSERS,
    FILENAME_STYLES,
    FORMATS,
    MP3_BITRATES,
    ON_EXISTS,
    WAV_BIT_DEPTHS,
    WAV_SAMPLE_RATES,
    Settings,
)
from core.errors import AppError, classify
from core.fetcher import FetchResult, UrlInfo, analyze_url, extract_urls, fetch_tracks
from core.history import ArtistMemory, History
from core.models import EDITABLE_FIELDS, Status, Track
from core.queue_manager import QueueManager
from utils import updater
from utils.logger import get_logger, setup_logging
from utils.paths import cleanup_old_work_dirs, default_output_dir, open_folder, subprocess_flags, user_data_dir

log = get_logger("web")

STATIC_DIR = Path(__file__).resolve().parent / "static"
DEFAULT_PORT = 8765
COOKIE_NAME = "yta_token"
MAX_BODY = 1_000_000

# 웹 화면에서 바꿀 수 있는 설정 (키 → 허용값 검사는 Settings.normalize 가 한다)
SETTING_KEYS = (
    "output_dir", "format", "mp3_bitrate", "wav_sample_rate", "wav_bit_depth", "filename_style",
    "on_exists", "max_concurrent", "retries", "strip_emoji", "crop_cover_square", "use_musicbrainz",
    "wav_embed_cover", "cookies_browser", "cookies_file", "ffmpeg_path", "auto_start", "check_updates",
)
# ---- 안드로이드 (Termux) -------------------------------------------------------------
IS_ANDROID = "com.termux" in os.environ.get("PREFIX", "") or Path("/system/build.prop").exists()
ANDROID_SHARED = Path("/storage/emulated/0")


def android_music_dir() -> Path | None:
    """휴대폰 공용 음악 폴더 (termux-setup-storage 로 저장소 권한을 줘야 쓸 수 있다)."""
    target = ANDROID_SHARED / "Music" / "추출사운드"
    try:
        target.mkdir(parents=True, exist_ok=True)
        return target if os.access(target, os.W_OK) else None
    except OSError:
        return None


def media_scan(path: str) -> None:
    """새 파일을 음악 앱에 바로 보이게 한다 (Termux:API 가 있을 때만, 없으면 조용히 넘어감)."""
    exe = shutil.which("termux-media-scan")
    if not exe:
        return
    try:
        subprocess.run([exe, path], capture_output=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        pass


BOOL_KEYS = {"strip_emoji", "crop_cover_square", "use_musicbrainz", "wav_embed_cover", "auto_start", "check_updates"}
INT_KEYS = {"mp3_bitrate", "wav_sample_rate", "wav_bit_depth", "max_concurrent", "retries"}


# ====================================================================== 접속 인증 (PIN)
class Auth:
    """PIN 과 로그인 토큰을 web.json 에 저장 (서버를 다시 켜도 휴대폰 로그인이 유지되도록)."""

    def __init__(self) -> None:
        self.path = user_data_dir() / "web.json"
        self._lock = threading.Lock()
        self.data: dict = {}
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                self.data = loaded
        except (OSError, ValueError):
            pass
        if not str(self.data.get("pin") or "").isdigit():
            self.data["pin"] = f"{secrets.randbelow(1_000_000):06d}"
            self.data["tokens"] = []
            self._save()
        self._fails: dict[str, list[float]] = {}
        self._all_fails: list[float] = []

    @property
    def pin(self) -> str:
        return self.data["pin"]

    def _save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError:
            pass

    def valid(self, token: str) -> bool:
        with self._lock:
            return bool(token) and token in (self.data.get("tokens") or [])

    def login(self, pin: str, client: str) -> str | None:
        """맞으면 새 토큰. PIN 무작위 대입 방지:
        기기별 1분에 5번, 전체 합쳐 10분에 30번 넘게 틀리면 잠시 거부 (주소를 바꿔 가며 시도해도 막히도록)."""
        now = time.time()
        with self._lock:
            fails = [t for t in self._fails.get(client, []) if now - t < 60]
            self._fails[client] = fails
            self._all_fails = [t for t in self._all_fails if now - t < 600]
            if len(fails) >= 5 or len(self._all_fails) >= 30:
                return None
            if not secrets.compare_digest(str(pin).strip(), self.pin):
                fails.append(now)
                self._all_fails.append(now)
                return None
            token = secrets.token_urlsafe(24)
            tokens = (self.data.get("tokens") or [])[-19:]  # 기기 20대까지 기억
            tokens.append(token)
            self.data["tokens"] = tokens
            self._save()
            return token

    def reset_pin(self) -> str:
        """새 PIN 발급 + 모든 기기 로그아웃."""
        with self._lock:
            self.data["pin"] = f"{secrets.randbelow(1_000_000):06d}"
            self.data["tokens"] = []
            self._save()
            return self.pin


# ====================================================================== 앱 상태
def fmt_duration(sec: int | None) -> str:
    if not sec:
        return ""
    m, s = divmod(int(sec), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


class WebApp:
    """데스크톱 MainWindow 의 동작을 화면 없이 옮긴 것. 모든 메서드는 여러 요청 스레드에서 호출된다."""

    def __init__(self, settings: Settings, history: History, memory: ArtistMemory):
        self.settings = settings
        self.history = history
        self.memory = memory
        self.lock = threading.RLock()
        self.tracks: dict[int, Track] = {}
        self.order: list[int] = []
        self.already: set[int] = set()  # 예전에 받은 적 있는 곡 (표시용)
        self.pending_fetches = 0
        self.messages: list[dict] = []  # {id, level, text}
        self._msg_seq = 0
        self.version = 0  # 상태가 바뀔 때마다 증가 → 화면이 바뀐 것만 다시 그린다
        self.update_info: dict = {}
        self.updating = False
        self.warnings = updater.environment_warnings(settings.ffmpeg_path)
        self.qm = QueueManager(settings, history, memory, on_event=self._on_track_event)
        if settings.check_updates:
            threading.Thread(target=self._check_update, daemon=True).start()

    # ---- 내부 -------------------------------------------------------------
    def _bump(self) -> None:
        with self.lock:
            self.version += 1

    def _on_track_event(self, track: Track) -> None:  # 워커 스레드
        if IS_ANDROID and track.status == Status.DONE and track.output_path:
            threading.Thread(target=media_scan, args=(track.output_path,), daemon=True).start()
        self._bump()

    def notify(self, text: str, level: str = "info") -> None:
        with self.lock:
            self._msg_seq += 1
            self.messages.append({"id": self._msg_seq, "level": level, "text": text})
            self.messages = self.messages[-50:]
            self.version += 1

    def _check_update(self) -> None:
        current, latest = updater.installed_version(), updater.latest_version()
        if updater.is_outdated(current, latest):
            with self.lock:
                self.update_info = {"current": current, "latest": latest, "can_update": updater.can_self_update()}
            self._bump()

    def _get(self, uids) -> list[Track]:
        out = []
        for u in uids or []:
            try:
                t = self.tracks.get(int(u))
            except (TypeError, ValueError):
                continue
            if t:
                out.append(t)
        return out

    # ---- 상태 조회 -----------------------------------------------------------
    def track_dict(self, t: Track, no: int) -> dict:
        return {
            "uid": t.uid,
            "no": no,
            "video_id": t.video_id,
            "url": t.url,
            "title": t.title,
            "artist": t.artist,
            "album": t.album,
            "album_artist": t.album_artist,
            "track_no": t.track_no,
            "year": t.year,
            "genre": t.genre,
            "channel": t.channel,
            "duration": fmt_duration(t.duration),
            "thumbnail": t.thumbnail,
            "status": t.status.name,
            "status_text": t.status.value,
            "active": t.status.active,
            "finished": t.status.finished,
            "progress": round(t.progress, 1),
            "message": t.message,
            "has_file": bool(t.output_path) and Path(t.output_path).is_file(),
            "file_name": Path(t.output_path).name if t.output_path else "",
            "already": t.uid in self.already,
        }

    def state(self, since_msg: int = 0) -> dict:
        with self.lock:
            tracks = [self.track_dict(self.tracks[u], i + 1) for i, u in enumerate(self.order)]
            started = [t for t in self.tracks.values() if t.status != Status.READY]
            overall = (
                sum(100.0 if t.status.finished else t.progress for t in started) / len(started) if started else 0.0
            )
            return {
                "version": self.version,
                "tracks": tracks,
                "pending": self.pending_fetches,
                "overall": round(overall, 1),
                "counts": {
                    "total": len(self.order),
                    "done": sum(t.status == Status.DONE for t in self.tracks.values()),
                    "failed": sum(t.status == Status.FAILED for t in self.tracks.values()),
                    "active": sum(t.status.active for t in self.tracks.values()),
                    "ready": sum(t.status == Status.READY for t in self.tracks.values()),
                },
                "messages": [m for m in self.messages if m["id"] > since_msg],
                "settings": {k: getattr(self.settings, k) for k in SETTING_KEYS},
                "update": self.update_info,
                "updating": self.updating,
                "warnings": self.warnings,
                "android": IS_ANDROID,
            }

    # ---- 링크 추가 -------------------------------------------------------------
    def analyze(self, text: str) -> list[dict]:
        out = []
        for raw in extract_urls(text):
            info = analyze_url(raw)
            out.append({"url": raw, "kind": info.kind, "reason": info.reason})
        return out

    def add(self, jobs: list[dict]) -> int:
        """jobs: [{url, playlist(bool)}]. 조회는 백그라운드에서 하고 결과는 상태 폴링으로 보인다."""
        parsed: list[tuple[UrlInfo, bool]] = []
        for job in jobs or []:
            info = analyze_url(str(job.get("url") or ""))
            if info.kind == "invalid":
                self.notify(f"{str(job.get('url'))[:60]} → {info.reason}", "error")
                continue
            playlist = info.kind == "playlist" or (info.kind == "video_in_playlist" and bool(job.get("playlist")))
            parsed.append((info, playlist))
        if not parsed:
            return 0
        with self.lock:
            self.pending_fetches += len(parsed)
            self.version += 1
        threading.Thread(target=self._fetch_worker, args=(parsed,), daemon=True).start()
        return len(parsed)

    def _fetch_worker(self, jobs: list[tuple[UrlInfo, bool]]) -> None:
        for info, playlist in jobs:
            label = info.video_id or info.playlist_id
            try:
                result = fetch_tracks(info, self.settings, playlist, self.memory)
                self._on_fetched(result)
            except Exception as exc:
                err = exc if isinstance(exc, AppError) else classify(exc)
                with self.lock:
                    self.pending_fetches = max(self.pending_fetches - 1, 0)
                self.notify(f"링크 조회 실패 ({label}): {err.message}", "error")

    def _on_fetched(self, result: FetchResult) -> None:
        with self.lock:
            self.pending_fetches = max(self.pending_fetches - 1, 0)
            existing = {t.video_id for t in self.tracks.values()}
            fresh = [t for t in result.tracks if t.video_id not in existing]
            in_list = len(result.tracks) - len(fresh)
            dupes = 0
            for t in fresh:
                self.tracks[t.uid] = t
                self.order.append(t.uid)
                if self.history.find(t.video_id, self.settings.format):
                    self.already.add(t.uid)
                    dupes += 1
            self.version += 1
        notes = []
        if result.playlist_title:
            notes.append(f"재생목록 '{result.playlist_title}' {len(fresh)}곡 추가")
        if in_list:
            notes.append(f"이미 목록에 있는 {in_list}곡은 건너뜀")
        if result.skipped:
            notes.append(f"비공개/삭제된 항목 {result.skipped}개 제외")
        if dupes:
            notes.append(f"예전에 받은 적 있는 곡 {dupes}개 ('이미 받음' 표시)")
        if notes:
            self.notify(" · ".join(notes))
        if self.settings.auto_start:
            for t in fresh:
                self.qm.enqueue(t)

    # ---- 목록 조작 -------------------------------------------------------------
    def delete(self, uids) -> None:
        with self.lock:
            for t in self._get(uids):
                if t.status.active:
                    self.qm.cancel(t)
                self.tracks.pop(t.uid, None)
                self.already.discard(t.uid)
                if t.uid in self.order:
                    self.order.remove(t.uid)
            self.version += 1

    def clear_finished(self) -> None:
        with self.lock:
            for uid in [u for u in self.order if self.tracks[u].status in (Status.DONE, Status.SKIPPED)]:
                self.tracks.pop(uid, None)
                self.already.discard(uid)
                self.order.remove(uid)
            self.version += 1

    def move(self, uid: int, delta: int) -> None:
        with self.lock:
            if uid not in self.order:
                return
            i = self.order.index(uid)
            j = min(max(i + delta, 0), len(self.order) - 1)
            self.order.insert(j, self.order.pop(i))
            self.version += 1

    def edit(self, uids, values: dict, remember: bool) -> str:
        tracks = self._get(uids)
        if not tracks:
            return "편집할 곡을 선택하세요."
        if any(t.status.active for t in tracks):
            return "진행 중인 곡은 편집할 수 없습니다."
        multi = len(tracks) > 1
        with self.lock:
            for t in tracks:
                for key, text in (values or {}).items():
                    if key not in EDITABLE_FIELDS:
                        continue
                    text = str(text if text is not None else "").strip()
                    if multi and (key in ("title", "track_no") or not text):
                        continue  # 일괄 편집: 제목/트랙번호 제외, 비운 칸은 유지
                    if key == "track_no":
                        try:
                            t.track_no = int(text) if text else None
                        except ValueError:
                            continue
                    else:
                        setattr(t, key, text)
                    t.edited.add(key)
                if "artist" in t.edited:
                    t.artist_source = "user"
                t.remember_artist = bool(remember)
            self.version += 1
        return ""

    def retry(self, uids) -> None:
        for t in self._get(uids):
            if t.status in (Status.FAILED, Status.CANCELLED):
                self.qm.enqueue(t)

    def download(self, uids=None) -> int:
        with self.lock:
            pool = self._get(uids) if uids else [self.tracks[u] for u in self.order]
            targets = [t for t in pool if t.status in (Status.READY, Status.FAILED, Status.CANCELLED)]
        self.qm.set_concurrency(self.settings.max_concurrent)
        for t in targets:
            self.qm.enqueue(t)
        return len(targets)

    def cancel(self, uids=None) -> None:
        with self.lock:
            pool = self._get(uids) if uids else list(self.tracks.values())
        for t in pool:
            if t.status.active:
                self.qm.cancel(t)

    # ---- 설정 -----------------------------------------------------------------------
    def update_settings(self, values: dict) -> None:
        with self.lock:
            for key, value in (values or {}).items():
                if key not in SETTING_KEYS:
                    continue
                try:
                    if key in BOOL_KEYS:
                        value = bool(value)
                    elif key in INT_KEYS:
                        value = int(value)
                    else:
                        value = str(value or "").strip()
                except (TypeError, ValueError):
                    continue
                setattr(self.settings, key, value)
            self.settings.save()  # normalize 포함
            self.qm.set_concurrency(self.settings.max_concurrent)
            self.warnings = updater.environment_warnings(self.settings.ffmpeg_path)
            self.version += 1

    def run_update(self) -> None:
        if self.updating:
            return
        self.updating = True
        self.notify("yt-dlp 업데이트 중...")

        def work():
            ok, out = updater.update_ytdlp()
            self.updating = False
            if ok:
                self.update_info = {}
                self.notify("yt-dlp 업데이트 완료 - 서버(run_web.bat)를 다시 시작하면 적용됩니다.")
            else:
                self.notify(f"업데이트 실패: {out[-200:]}", "error")

        threading.Thread(target=work, daemon=True).start()

    def file_of(self, uid: int) -> Path | None:
        """목록에 있는 완료 곡의 파일만 내보낸다 (임의 경로 요청 차단)."""
        with self.lock:
            t = self.tracks.get(uid)
            if not t or not t.output_path:
                return None
            p = Path(t.output_path)
        return p if p.is_file() else None


# ====================================================================== HTTP
def lan_addresses() -> list[str]:
    ips: list[str] = []
    try:  # 실제로 패킷을 보내지 않고, 바깥으로 나갈 때 쓰는 인터페이스 주소를 얻는다
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ips.append(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip not in ips and not ip.startswith("127."):
                ips.append(ip)
    except OSError:
        pass
    try:  # Tailscale 주소가 위에서 안 잡히는 경우 대비
        proc = subprocess.run(["tailscale", "ip", "-4"], capture_output=True, text=True, timeout=3,
                              creationflags=subprocess_flags())
        for ip in proc.stdout.split():
            if ip not in ips:
                ips.append(ip)
    except (OSError, subprocess.SubprocessError):
        pass
    return ips


def is_tailscale(ip: str) -> bool:
    """Tailscale 은 100.64.0.0/10 대역 주소를 준다 → 밖에서(휴대폰 데이터) 접속할 때 쓰는 주소."""
    parts = ip.split(".")
    return len(parts) == 4 and parts[0] == "100" and 64 <= int(parts[1]) <= 127


def access_urls(port: int) -> list[dict]:
    return [
        {"url": f"http://{ip}:{port}", "where": "밖에서도 (Tailscale)" if is_tailscale(ip) else "같은 와이파이"}
        for ip in lan_addresses()
    ]


def content_disposition(name: str) -> str:
    ascii_name = name.encode("ascii", "replace").decode().replace('"', "'").replace("?", "_")
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name)}"


class Handler(BaseHTTPRequestHandler):
    app: WebApp
    auth: Auth
    port: int
    lan: bool
    protocol_version = "HTTP/1.1"
    server_version = "YouTubeAudioWeb"

    def log_message(self, fmt, *args) -> None:  # 기본 콘솔 로그는 너무 시끄러워서 끈다
        pass

    # ---- 공통 --------------------------------------------------------------------
    def _is_local(self) -> bool:
        # 터널(cloudflared 등)을 거친 요청도 127.0.0.1 에서 온 것처럼 보이므로, 전달 헤더가 있으면 외부로 본다
        if any(self.headers.get(h) for h in ("X-Forwarded-For", "Forwarded", "CF-Connecting-IP", "X-Real-IP")):
            return False
        return self.client_address[0] in ("127.0.0.1", "::1")

    def _client_id(self) -> str:
        return (self.headers.get("CF-Connecting-IP") or self.headers.get("X-Forwarded-For") or
                self.client_address[0]).split(",")[0].strip()

    def _token(self) -> str:
        cookie = SimpleCookie(self.headers.get("Cookie") or "")
        return cookie[COOKIE_NAME].value if COOKIE_NAME in cookie else ""

    def _authed(self) -> bool:
        return self._is_local() or self.auth.valid(self._token())

    def _send(self, code: int, body: bytes, ctype: str, extra: dict | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, data, code: int = 200, extra: dict | None = None) -> None:
        self._send(code, json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8", extra)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > MAX_BODY:
            return {}
        try:
            data = json.loads(self.rfile.read(length).decode("utf-8"))
            return data if isinstance(data, dict) else {}
        except (ValueError, UnicodeDecodeError):
            return {}

    def _send_file(self, path: Path, name: str, ctype: str, delete_after: bool = False) -> None:
        try:
            size = path.stat().st_size
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(size))
            self.send_header("Content-Disposition", content_disposition(name))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            with path.open("rb") as f:
                shutil.copyfileobj(f, self.wfile, 256 * 1024)
        except (ConnectionError, OSError):
            pass
        finally:
            if delete_after:
                try:
                    path.unlink()
                except OSError:
                    pass

    def _static(self, name: str) -> None:
        path = (STATIC_DIR / name).resolve()
        if STATIC_DIR.resolve() not in path.parents or not path.is_file():
            self._send(404, b"not found", "text/plain")
            return
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
            ctype += "; charset=utf-8"
        self._send(200, path.read_bytes(), ctype)

    # ---- GET -----------------------------------------------------------------------
    def do_GET(self) -> None:
        url = urlparse(self.path)
        qs = parse_qs(url.query)
        path = url.path
        if path in ("/", "/index.html"):
            return self._static("index.html")
        if path.startswith("/static/"):
            return self._static(path[len("/static/"):])
        if path == "/api/me":
            return self._json({
                "authed": self._authed(),
                "local": self._is_local(),
                "android": IS_ANDROID,
            })
        if not self._authed():
            return self._json({"error": "login"}, 401)

        if path == "/api/state":
            try:
                since = int((qs.get("since") or ["0"])[0])
            except ValueError:
                since = 0
            return self._json(self.app.state(since))
        if path == "/api/access":  # PC 화면에만 보여 주는 휴대폰 접속 정보
            if not self._is_local():
                return self._json({"error": "forbidden"}, 403)
            urls = access_urls(self.port) if self.lan else []
            return self._json({"pin": self.auth.pin, "urls": urls, "lan": self.lan})
        if path.startswith("/api/file/"):
            try:
                uid = int(path.rsplit("/", 1)[1])
            except ValueError:
                return self._send(404, b"not found", "text/plain")
            p = self.app.file_of(uid)
            if not p:
                return self._send(404, "파일이 없습니다".encode(), "text/plain; charset=utf-8")
            ctype = "audio/mpeg" if p.suffix.lower() == ".mp3" else "audio/wav"
            return self._send_file(p, p.name, ctype)
        if path == "/api/zip":
            uids = [u for u in ",".join(qs.get("uids") or []).split(",") if u]
            return self._zip(uids)
        self._send(404, b"not found", "text/plain")

    do_HEAD = do_GET

    def _zip(self, uids: list[str]) -> None:
        files = []
        for u in uids:
            try:
                p = self.app.file_of(int(u))
            except ValueError:
                continue
            if p:
                files.append(p)
        if not files:
            return self._send(404, "받을 파일이 없습니다".encode(), "text/plain; charset=utf-8")
        tmp = Path(tempfile.gettempdir()) / f"yta_{secrets.token_hex(6)}.zip"
        used: set[str] = set()
        # mp3/wav 는 이미 압축돼 있거나 압축 이득이 작아서 STORED 로 빠르게 묶는다
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_STORED) as z:
            for p in files:
                name = p.name
                n = 2
                while name in used:
                    name = f"{p.stem} ({n}){p.suffix}"
                    n += 1
                used.add(name)
                z.write(p, name)
        self._send_file(tmp, f"유튜브음악_{time.strftime('%Y%m%d_%H%M')}.zip", "application/zip", delete_after=True)

    # ---- POST ------------------------------------------------------------------------
    def do_POST(self) -> None:
        path = urlparse(self.path).path
        # 다른 사이트가 몰래 요청을 보내지 못하도록 (브라우저는 이 헤더를 붙인 교차 출처 요청을 막는다)
        if self.headers.get("X-Requested-With") != "yta":
            return self._json({"error": "bad request"}, 400)
        body = self._body()

        if path == "/api/login":
            token = self.auth.login(str(body.get("pin") or ""), self._client_id())
            if not token:
                return self._json({"ok": False, "error": "PIN 이 맞지 않습니다. (여러 번 틀리면 1분간 잠깁니다)"}, 403)
            cookie = f"{COOKIE_NAME}={token}; Path=/; Max-Age=31536000; HttpOnly; SameSite=Strict"
            return self._json({"ok": True}, extra={"Set-Cookie": cookie})
        if not self._authed():
            return self._json({"error": "login"}, 401)

        app = self.app
        uids = body.get("uids") or []
        result: dict = {"ok": True}
        if path == "/api/analyze":
            result["items"] = app.analyze(str(body.get("text") or ""))
        elif path == "/api/add":
            result["count"] = app.add(body.get("jobs") or [])
        elif path == "/api/delete":
            app.delete(uids)
        elif path == "/api/edit":
            err = app.edit(uids, body.get("values") or {}, bool(body.get("remember", True)))
            if err:
                result = {"ok": False, "error": err}
        elif path == "/api/move":
            try:
                app.move(int(body.get("uid")), int(body.get("delta")))
            except (TypeError, ValueError):
                pass
        elif path == "/api/retry":
            app.retry(uids)
        elif path == "/api/download":
            result["count"] = app.download(uids or None)
        elif path == "/api/cancel":
            app.cancel(uids or None)
        elif path == "/api/clear_finished":
            app.clear_finished()
        elif path == "/api/settings":
            app.update_settings(body.get("settings") or {})
        elif path == "/api/update_ytdlp":
            app.run_update()
        elif path == "/api/open_folder":  # PC 에서만 의미가 있다
            if self._is_local():
                open_folder(app.settings.output_dir)
        elif path == "/api/reset_pin":
            if not self._is_local():
                return self._json({"ok": False, "error": "PC 에서만 바꿀 수 있습니다."}, 403)
            result["pin"] = self.auth.reset_pin()
        else:
            return self._json({"error": "not found"}, 404)
        self._json(result)


def options_payload() -> dict:
    return {
        "formats": list(FORMATS),
        "mp3_bitrates": list(MP3_BITRATES),
        "wav_sample_rates": list(WAV_SAMPLE_RATES),
        "wav_bit_depths": list(WAV_BIT_DEPTHS),
        "filename_styles": FILENAME_STYLES,
        "on_exists": ON_EXISTS,
        "browsers": list(BROWSERS),
    }


def write_options_js() -> None:
    """설정 선택지를 config.py 와 같은 값으로 맞춰 둔다 (화면이 따로 목록을 들고 있지 않도록)."""
    js = "window.YTA_OPTIONS = " + json.dumps(options_payload(), ensure_ascii=False) + ";\n"
    target = STATIC_DIR / "options.js"
    try:
        if not target.exists() or target.read_text(encoding="utf-8") != js:
            target.write_text(js, encoding="utf-8")
    except OSError:
        pass


def main() -> None:
    parser = argparse.ArgumentParser(description="유튜브 → MP3/WAV 변환기 (웹 버전)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--local-only", action="store_true", help="이 기기에서만 접속 허용")
    parser.add_argument("--lan", action="store_true", help="안드로이드에서도 다른 기기의 접속 허용 (기본은 이 휴대폰만)")
    parser.add_argument("--no-browser", action="store_true", help="시작할 때 브라우저를 열지 않음")
    args = parser.parse_args()

    setup_logging()
    cleanup_old_work_dirs()
    write_options_js()
    settings = Settings.load()
    if IS_ANDROID:
        # 기본 저장 폴더(Termux 내부)는 다른 앱에서 안 보이므로 휴대폰 Music 폴더로 바꾼다
        music = android_music_dir()
        if music and Path(settings.output_dir) == default_output_dir():
            settings.output_dir = str(music)
            settings.save()
        elif not music:
            print("[경고] 휴대폰 저장소 권한이 없습니다. Termux 에서 termux-setup-storage 를 실행하고 '허용'을 누르세요.")
    app = WebApp(settings, History(), ArtistMemory())
    auth = Auth()

    local_only = args.local_only or (IS_ANDROID and not args.lan)
    host = "127.0.0.1" if local_only else "0.0.0.0"
    Handler.app, Handler.auth, Handler.port, Handler.lan = app, auth, args.port, not local_only
    try:
        server = ThreadingHTTPServer((host, args.port), Handler)
    except OSError as exc:
        print(f"\n포트 {args.port} 를 열 수 없습니다: {exc}")
        print("이미 실행 중인지 확인하거나 --port 8766 처럼 다른 포트를 지정하세요.")
        raise SystemExit(1)
    server.daemon_threads = True

    local_url = f"http://127.0.0.1:{args.port}"
    print("=" * 60)
    print(" 유튜브 → MP3/WAV 변환기 (웹 버전) 실행 중")
    print(f"  이 PC 에서 : {local_url}")
    if not local_only:
        for a in access_urls(args.port):
            print(f"  휴대폰에서 : {a['url']}   ({a['where']})")
        print(f"  접속 PIN   : {auth.pin}")
        print("  * 처음 실행 때 Windows 방화벽 창이 뜨면 '허용'을 눌러야 휴대폰에서 접속됩니다.")
    print("  종료하려면 이 창을 닫거나 Ctrl+C")
    print("=" * 60)
    for w in app.warnings:
        print("[경고]", w)

    if not args.no_browser and not IS_ANDROID:  # 안드로이드는 android/yta.sh 가 브라우저를 연다
        threading.Timer(0.8, lambda: webbrowser.open(local_url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        app.qm.shutdown()
        app.settings.save()
        server.server_close()


if __name__ == "__main__":
    main()
