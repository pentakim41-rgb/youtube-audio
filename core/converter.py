"""ffmpeg 로 mp3 / wav 변환 (진행률 파싱, 취소 지원)."""
from __future__ import annotations

import re
import subprocess
import threading
from collections import deque
from pathlib import Path
from typing import Callable

from config import Settings
from core.errors import AppError, CancelledError
from utils.logger import get_logger
from utils.paths import subprocess_flags

log = get_logger("converter")

ProgressCb = Callable[[float], None]  # 0.0~1.0
_DURATION = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")


def build_command(
    ffmpeg: str,
    src: Path,
    dst: Path,
    settings: Settings,
    wav_metadata: dict[str, str] | None = None,
) -> list[str]:
    """ffmpeg 명령 생성. 원본에 들어 있는 잡다한 메타데이터/영상은 버린다(-vn, -map_metadata -1)."""
    cmd = [ffmpeg, "-hide_banner", "-nostdin", "-y", "-i", str(src), "-vn", "-map_metadata", "-1"]
    if settings.format == "mp3":
        cmd += ["-c:a", "libmp3lame", "-b:a", f"{settings.mp3_bitrate}k", "-ac", "2"]
        cmd += ["-id3v2_version", "3"]
    else:
        codec = "pcm_s24le" if settings.wav_bit_depth == 24 else "pcm_s16le"
        cmd += ["-c:a", codec, "-ar", str(settings.wav_sample_rate), "-ac", "2"]
        for key, value in (wav_metadata or {}).items():
            if value:
                cmd += ["-metadata", f"{key}={value}"]
    cmd += ["-progress", "pipe:1", "-nostats", str(dst)]
    return cmd


def convert(
    ffmpeg: str,
    src: Path,
    dst: Path,
    settings: Settings,
    duration: float | None,
    on_progress: ProgressCb,
    cancel_event: threading.Event,
    wav_metadata: dict[str, str] | None = None,
) -> None:
    """src → dst 변환. 실패하면 AppError, 취소하면 CancelledError."""
    cmd = build_command(ffmpeg, src, dst, settings, wav_metadata)
    log.info("ffmpeg: %s", " ".join(cmd))
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,  # 한 줄씩 읽어 파이프가 막히지 않게 합친다
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=subprocess_flags(),
        )
    except FileNotFoundError as exc:
        raise AppError("FFMPEG", "ffmpeg 를 찾을 수 없습니다. 설정에서 경로를 지정하세요.") from exc
    except OSError as exc:
        raise AppError("FFMPEG", f"ffmpeg 실행 실패: {exc}") from exc

    tail: deque[str] = deque(maxlen=25)
    total = float(duration) if duration else 0.0
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            if cancel_event.is_set():
                proc.kill()
                proc.wait()
                raise CancelledError()
            line = line.strip()
            if not line:
                continue
            tail.append(line)
            if not total:
                m = _DURATION.search(line)
                if m:
                    total = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
            if line.startswith(("out_time_us=", "out_time_ms=")) and total:
                try:
                    micro = int(line.split("=", 1)[1])
                except ValueError:
                    continue
                if micro >= 0:
                    on_progress(min(micro / 1_000_000 / total, 1.0))
        code = proc.wait()
    except CancelledError:
        raise
    except Exception:
        proc.kill()
        proc.wait()
        raise
    if cancel_event.is_set():
        raise CancelledError()
    if code != 0 or not dst.is_file() or dst.stat().st_size == 0:
        detail = "\n".join(tail)
        log.warning("ffmpeg 실패(code=%s): %s", code, detail)
        if "No space left" in detail:
            raise AppError("DISK", "저장 공간이 부족합니다. 디스크 여유 공간을 확보하세요.", False, detail)
        raise AppError("CONVERT", "오디오 변환에 실패했습니다. (로그 확인)", False, detail)
    on_progress(1.0)
