"""링크 분석과 정보 조회. 여기서는 절대 다운로드하지 않는다(메타데이터만)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlparse

import yt_dlp

from config import Settings
from core.errors import AppError, classify
from core.history import ArtistMemory
from core.metadata import (
    apply_guess,
    clean_album_name,
    enrich_with_musicbrainz,
    guess_from_info,
)
from core.models import Track
from core.ytdlp_opts import base_opts
from utils.logger import get_logger

log = get_logger("fetcher")

_YT_HOSTS = {
    "youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com",
    "youtu.be", "www.youtu.be", "youtube-nocookie.com", "www.youtube-nocookie.com",
}
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
_SKIP_TITLES = {"[private video]", "[deleted video]", "private video", "deleted video"}


@dataclass
class UrlInfo:
    kind: str  # video / playlist / video_in_playlist / invalid
    video_id: str = ""
    playlist_id: str = ""
    reason: str = ""

    @property
    def video_url(self) -> str:
        return f"https://www.youtube.com/watch?v={self.video_id}"

    @property
    def playlist_url(self) -> str:
        return f"https://www.youtube.com/playlist?list={self.playlist_id}"


def extract_urls(text: str) -> list[str]:
    """붙여넣은 글에서 링크 후보를 줄/공백/쉼표 단위로 분리 (중복 제거, 순서 유지)."""
    seen, out = set(), []
    for token in re.split(r"[\s,]+", text or ""):
        token = token.strip()
        if token and token not in seen:
            seen.add(token)
            out.append(token)
    return out


def analyze_url(text: str) -> UrlInfo:
    """유튜브 링크 종류 판별. 잘못된 링크는 kind='invalid' + 이유."""
    raw = (text or "").strip()
    if not raw:
        return UrlInfo("invalid", reason="링크가 비어 있습니다.")
    if not re.match(r"^[a-z]+://", raw, re.I):
        raw = "https://" + raw
    try:
        parsed = urlparse(raw)
    except ValueError:
        return UrlInfo("invalid", reason="올바른 링크 형식이 아닙니다.")
    host = (parsed.hostname or "").lower()
    if host not in _YT_HOSTS:
        return UrlInfo("invalid", reason="유튜브 링크가 아닙니다.")

    qs = parse_qs(parsed.query)
    vid = (qs.get("v") or [""])[0]
    pid = (qs.get("list") or [""])[0]
    parts = [p for p in parsed.path.split("/") if p]

    if host.endswith("youtu.be") and parts:
        vid = parts[0]
    elif parts and parts[0] in ("shorts", "live", "embed", "v") and len(parts) > 1:
        vid = parts[1]

    if vid and not _ID_RE.match(vid):
        return UrlInfo("invalid", reason="영상 ID 형식이 올바르지 않습니다.")

    # RD…(자동 생성 믹스)는 사실상 끝없는 목록이라 재생목록으로 취급하지 않는다
    is_mix = pid.startswith("RD") or pid.startswith("UL")
    if vid and pid and not is_mix:
        return UrlInfo("video_in_playlist", vid, pid)
    if vid:
        return UrlInfo("video", vid)
    if pid and not is_mix:
        return UrlInfo("playlist", playlist_id=pid)
    return UrlInfo("invalid", reason="영상이나 재생목록 링크가 아닙니다. (채널/검색 링크는 지원하지 않습니다)")


@dataclass
class FetchResult:
    tracks: list[Track] = field(default_factory=list)
    skipped: int = 0  # 비공개/삭제로 건너뛴 재생목록 항목 수
    playlist_title: str = ""


def _thumbnail_candidates(info: dict) -> list[dict]:
    thumbs = [t for t in (info.get("thumbnails") or []) if t.get("url")]
    return thumbs


def track_from_info(
    info: dict,
    memory: ArtistMemory | None = None,
    playlist_title: str = "",
    index: int | None = None,
    resolved: bool = True,
) -> Track:
    vid = info.get("id") or ""
    g = guess_from_info(info, playlist_title=playlist_title, memory=memory)
    duration = info.get("duration")
    t = Track(
        video_id=vid,
        url=f"https://www.youtube.com/watch?v={vid}",
        channel=info.get("channel") or info.get("uploader") or "",
        duration=int(duration) if duration else None,
        thumbnail=info.get("thumbnail") or "",
        thumbnails=_thumbnail_candidates(info),
        playlist_title=playlist_title,
        is_playlist_item=index is not None,
        resolved=resolved,
    )
    apply_guess(t, g)
    if index is not None:
        t.track_no = index
    if not t.title:
        t.title = info.get("title") or vid
    return t


def fetch_tracks(
    info: UrlInfo,
    settings: Settings,
    playlist: bool,
    memory: ArtistMemory | None = None,
) -> FetchResult:
    """링크 하나를 조회해 Track 목록으로 만든다. 실패 시 AppError.

    playlist=True 면 재생목록 전체, False 면 영상 한 개.
    """
    try:
        if playlist and info.playlist_id:
            return _fetch_playlist(info, settings, memory)
        return _fetch_video(info, settings, memory)
    except AppError:
        raise
    except Exception as exc:  # yt-dlp 의 DownloadError 등
        log.info("조회 실패: %s", exc)
        raise classify(exc) from exc


def _fetch_video(info: UrlInfo, settings: Settings, memory) -> FetchResult:
    opts = base_opts(settings, log)
    opts["skip_download"] = True
    with yt_dlp.YoutubeDL(opts) as ydl:
        data = ydl.extract_info(info.video_url, download=False)
    if not data:
        raise AppError("UNAVAILABLE", "영상 정보를 가져오지 못했습니다.")
    if data.get("is_live") or data.get("live_status") in ("is_live", "is_upcoming"):
        raise AppError("LIVE", "라이브/예정된 방송은 지원하지 않습니다. 방송이 끝난 뒤 다시 시도하세요.")
    track = track_from_info(data, memory)
    if settings.use_musicbrainz and track.artist_source != "memory":
        try:
            enrich_with_musicbrainz(track)
        except Exception as exc:  # 보완 기능이라 실패해도 무시
            log.info("MusicBrainz 보완 실패: %s", exc)
    return FetchResult([track], 0, "")


def _fetch_playlist(info: UrlInfo, settings: Settings, memory) -> FetchResult:
    opts = base_opts(settings, log)
    opts.update({"skip_download": True, "extract_flat": "in_playlist", "noplaylist": False})
    with yt_dlp.YoutubeDL(opts) as ydl:
        data = ydl.extract_info(info.playlist_url, download=False)
    if not data:
        raise AppError("UNAVAILABLE", "재생목록 정보를 가져오지 못했습니다.")
    title = data.get("title") or ""
    album = clean_album_name(title)
    result = FetchResult(playlist_title=title)
    number = 0
    for entry in data.get("entries") or []:
        if not entry or not entry.get("id"):
            result.skipped += 1
            continue
        if (entry.get("title") or "").strip().lower() in _SKIP_TITLES:
            result.skipped += 1
            continue
        number += 1
        t = track_from_info(entry, memory, playlist_title=album, index=number, resolved=False)
        result.tracks.append(t)
    return result
