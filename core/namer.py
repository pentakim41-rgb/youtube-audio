"""파일명/폴더 정리: Windows 금지문자, 이모지, 예약어, 길이 제한, 중복 이름 처리."""
from __future__ import annotations

import re
from pathlib import Path

from config import Settings
from core.models import Track

_INVALID = {
    "<": "(", ">": ")", ":": " -", '"': "'", "/": "-", "\\": "-",
    "|": "-", "?": "", "*": "",
}
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_EMOJI = re.compile(
    "[\U0001F300-\U0001FAFF\U0001F000-\U0001F2FF\U0001F1E6-\U0001F1FF"
    "☀-➿⬀-⯿︀-️‍⃣←-⇿⌀-⏿]"
)
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
_WS = re.compile(r"\s+")

MAX_COMPONENT = 80  # 폴더/파일 이름 한 칸의 최대 글자 수
MAX_TOTAL_PATH = 240  # Windows MAX_PATH(260) 여유
MIN_COMPONENT = 20  # 경로가 길 때 줄이더라도 남겨둘 최소 글자 수


def sanitize(name: str, strip_emoji: bool = True, fallback: str = "Unknown") -> str:
    """파일/폴더 이름 한 칸으로 쓸 수 있게 정리."""
    text = name or ""
    if strip_emoji:
        text = _EMOJI.sub("", text)
    text = _CONTROL.sub("", text)
    text = "".join(_INVALID.get(ch, ch) for ch in text)
    text = _WS.sub(" ", text).strip(" .")
    if len(text) > MAX_COMPONENT:
        text = text[:MAX_COMPONENT].rstrip(" .")
    if not text:
        return fallback
    if text.split(".")[0].upper() in _RESERVED:
        text = "_" + text
    return text


def build_filename(track: Track, settings: Settings) -> str:
    """확장자를 뺀 파일 이름."""
    strip = settings.strip_emoji
    title = sanitize(track.title, strip, fallback=track.video_id or "Unknown")
    artist = sanitize(track.artist, strip, fallback="") if track.artist else ""
    style = settings.filename_style
    if style == "track_title" and track.track_no:
        return f"{int(track.track_no):02d} - {title}"
    if style == "artist_title" and artist:
        return f"{artist} - {title}"
    return title


def build_output_path(track: Track, settings: Settings) -> Path:
    """최종 저장 경로 (중복 처리 전). 하위 폴더 없이 저장 폴더에 바로 저장한다."""
    ext = "." + settings.format
    folder = Path(settings.output_dir)
    name = build_filename(track, settings)
    overflow = len(str(folder / (name + ext))) - MAX_TOTAL_PATH
    if overflow > 0:  # 경로가 너무 길면 파일명을 줄인다 (최소 MIN_COMPONENT 글자는 남김)
        name = name[: max(MIN_COMPONENT, len(name) - overflow)].rstrip(" .")
    return folder / (name + ext)


def unique_path(path: Path) -> Path:
    """이미 있으면 'name (2).ext' 처럼 번호를 붙인다."""
    if not path.exists():
        return path
    for i in range(2, 1000):
        candidate = path.with_name(f"{path.stem} ({i}){path.suffix}")
        if not candidate.exists():
            return candidate
    return path
