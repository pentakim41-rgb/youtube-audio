"""yt-dlp 로 최고 음질 오디오 스트림을 내려받는다 (변환은 converter 가 담당)."""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable

import yt_dlp

from config import Settings
from core.errors import AppError, CancelledError, classify
from core.models import Track
from core.ytdlp_opts import base_opts
from utils.logger import get_logger

log = get_logger("downloader")

# on_progress(0.0~1.0, 표시용 문구)
ProgressCb = Callable[[float, str], None]


def _fmt_speed(speed: float | None) -> str:
    if not speed:
        return ""
    return f"{speed / 1024 / 1024:.1f}MB/s" if speed >= 1024 * 1024 else f"{speed / 1024:.0f}KB/s"


def download_audio(
    track: Track,
    workdir: Path,
    settings: Settings,
    on_progress: ProgressCb,
    cancel_event: threading.Event,
) -> tuple[Path, dict]:
    """(내려받은 원본 파일 경로, 전체 info dict) 반환. 실패 시 AppError/CancelledError."""
    workdir.mkdir(parents=True, exist_ok=True)

    def hook(d: dict) -> None:
        if cancel_event.is_set():
            raise CancelledError()
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            done = d.get("downloaded_bytes") or 0
            frac = min(done / total, 1.0) if total else 0.0
            on_progress(frac, _fmt_speed(d.get("speed")))
        elif d.get("status") == "finished":
            on_progress(1.0, "")

    opts = base_opts(settings, log)
    opts.update(
        {
            "format": "bestaudio/best",
            "outtmpl": str(workdir / "%(id)s.%(ext)s"),
            "continuedl": True,  # 끊긴 .part 파일 이어받기
            "fragment_retries": 5,
            "file_access_retries": 3,
            "concurrent_fragment_downloads": 4,
            "progress_hooks": [hook],
        }
    )
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(track.url, download=True)
            if not info:
                raise AppError("UNAVAILABLE", "영상 정보를 가져오지 못했습니다.")
            if info.get("is_live") or info.get("live_status") in ("is_live", "is_upcoming"):
                raise AppError("LIVE", "라이브/예정된 방송은 지원하지 않습니다.")
            path = _locate_file(ydl, info, workdir, track.video_id)
    except CancelledError:
        raise
    except AppError:
        raise
    except Exception as exc:
        if cancel_event.is_set():
            raise CancelledError() from exc
        raise classify(exc) from exc
    if cancel_event.is_set():
        raise CancelledError()
    return path, info


def _locate_file(ydl: yt_dlp.YoutubeDL, info: dict, workdir: Path, video_id: str) -> Path:
    reqs = info.get("requested_downloads") or []
    if reqs and reqs[0].get("filepath") and Path(reqs[0]["filepath"]).is_file():
        return Path(reqs[0]["filepath"])
    guess = Path(ydl.prepare_filename(info))
    if guess.is_file():
        return guess
    for p in sorted(workdir.glob(f"{video_id}.*")):
        if p.suffix not in (".part", ".ytdl", ".temp") and p.is_file():
            return p
    raise AppError("UNKNOWN", "다운로드한 파일을 찾을 수 없습니다.", True)
