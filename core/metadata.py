"""가수/앨범/제목 추정.

우선순위
  1) yt-dlp 가 준 track/artist/album 필드 (YouTube Music, '- Topic' 채널의 자동 생성 영상)
  2) 설명란의 "Provided to YouTube by ..." 블록
  3) 제목 패턴 파싱 ('가수 - 제목', "가수 '제목' MV" 등) + 잡음([MV], (Official Video)) 제거
  4) 채널명 (VEVO, '- Topic' 등 제거)  ← 채널→가수 기억이 있으면 그것을 사용
  5) MusicBrainz 검색으로 앨범/발매연도 보완 (선택)
마지막 확정은 언제나 사용자의 편집 화면에서 한다.
"""
from __future__ import annotations

import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from core.models import Track
from utils.logger import get_logger

log = get_logger("metadata")

# ----------------------------------------------------------------------------
# 제목 정리
# ----------------------------------------------------------------------------
_BRACKETS = re.compile(r"[\(\[\{【（［「『<]([^\)\]\}】）］」』>]*)[\)\]\}】）］」』>]")

# 괄호 안에 있어도 지우면 안 되는 의미 있는 표기
_KEEP = re.compile(
    r"(?<![\w])(feat|ft|featuring|remix|live|acoustic|cover|inst|instrumental|ver|version|"
    r"remaster(?:ed)?|ost|demo|edit|mix|prod|with)(?![a-z])|"
    r"리믹스|라이브|어쿠스틱|커버|인스|버전|리마스터|ost|피처링",
    re.I,
)
# 지워도 되는 잡음
_NOISE = re.compile(
    r"(?<![\w])(official|m/?v|music\s*video|lyrics?|lyric\s*video|visuali[sz]er|audio|video|"
    r"hd|hq|4k|8k|1080p|720p|teaser|full\s*album|mv)(?![\w])|"
    r"공식|뮤직\s*비디오|가사|음원|영상|고음질",
    re.I,
)
_TRAILING_NOISE = re.compile(
    r"\s+(?:official\s+)?(?:m/?v|music\s+video|lyric\s+video|lyrics|audio|visuali[sz]er)\s*$",
    re.I,
)
_PIPE_TAIL = re.compile(r"\s*[|｜]\s*([^|｜]*)$")
_DASH_SPLIT = re.compile(r"\s+[-–—―]\s+")
_QUOTED = re.compile(r"^(?P<artist>.+?)\s*['\"‘“「『](?P<title>.+?)['\"’”」』]")
_WS = re.compile(r"\s+")


def clean_title(title: str) -> str:
    """[MV], (Official Video) 같은 잡음을 제거한 제목."""
    text = title or ""

    def repl(m: re.Match) -> str:
        inner = m.group(1)
        if _KEEP.search(inner):
            return m.group(0)
        if _NOISE.search(inner):
            return " "
        return m.group(0)

    text = _BRACKETS.sub(repl, text)
    tail = _PIPE_TAIL.search(text)
    if tail and _NOISE.search(tail.group(1)) and not _KEEP.search(tail.group(1)):
        text = text[: tail.start()]
    text = _TRAILING_NOISE.sub("", text)
    return _WS.sub(" ", text).strip(" -–—―|｜")


def clean_channel(name: str) -> str:
    """채널명에서 '- Topic', VEVO, Official 등을 제거."""
    text = (name or "").strip()
    text = re.sub(r"\s*[-–]\s*Topic$", "", text, flags=re.I)
    text = re.sub(r"(?i)\s*(official\s*(youtube\s*)?channel|official|vevo)$", "", text)
    text = re.sub(r"\s*(공식\s*(유튜브\s*)?채널|공식)$", "", text)
    return _WS.sub(" ", text).strip(" -–—_")


def _norm(text: str) -> str:
    return re.sub(r"[\W_]+", "", (text or "").casefold())


def split_artist_title(title: str, channel: str = "") -> tuple[str, str]:
    """정리된 제목에서 (가수, 곡명) 추출. 못 찾으면 ('', title)."""
    text = clean_title(title)
    parts = _DASH_SPLIT.split(text, maxsplit=1)
    if len(parts) == 2 and parts[0].strip() and parts[1].strip():
        left, right = parts[0].strip(), parts[1].strip()
        ch = _norm(clean_channel(channel))
        # '제목 - 가수' 형태 (오른쪽이 채널명과 같고 왼쪽은 아닌 경우) → 뒤집기
        if ch and _norm(right) == ch and _norm(left) != ch:
            left, right = right, left
        return left.strip("'\"‘’“” "), right
    m = _QUOTED.match(text)
    if m:
        return m.group("artist").strip(), m.group("title").strip()
    return "", text


_PROVIDED = re.compile(r"Provided to YouTube by[^\n]*\n+(?P<body>.+)", re.S)


def parse_provided_by(description: str) -> dict:
    """Topic 채널 설명란("Provided to YouTube by ...")에서 곡/가수/앨범 추출."""
    m = _PROVIDED.search(description or "")
    if not m:
        return {}
    lines = [ln.strip() for ln in m.group("body").splitlines() if ln.strip()]
    if not lines:
        return {}
    out: dict = {}
    first = [p.strip() for p in lines[0].split("·") if p.strip()]
    if len(first) >= 2:
        out["track"] = first[0]
        out["artists"] = first[1:]
    if len(lines) > 1 and not lines[1].startswith("℗"):
        out["album"] = lines[1]
    return out


# ----------------------------------------------------------------------------
# yt-dlp info → 태그 추정
# ----------------------------------------------------------------------------
@dataclass
class Guess:
    title: str = ""
    artist: str = ""
    album: str = ""
    album_artist: str = ""
    track_no: int | None = None
    year: str = ""
    genre: str = ""
    artist_source: str = ""  # field / title / channel / memory


def _as_list(value) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        return [v.strip() for v in re.split(r"\s*[,;]\s*", value) if v.strip()]
    return [str(v).strip() for v in value if str(v).strip()]


def guess_from_info(info: dict, playlist_title: str = "", memory=None) -> Guess:
    """yt-dlp info dict 로부터 태그를 추정한다. memory: ArtistMemory (없어도 됨)."""
    g = Guess()
    raw_title = info.get("title") or ""
    channel = info.get("channel") or info.get("uploader") or ""

    track = info.get("track") or ""
    artists = _as_list(info.get("artists")) or _as_list(info.get("artist"))
    album = info.get("album") or ""

    if not (track and artists):  # 2) 설명란 블록
        prov = parse_provided_by(info.get("description") or "")
        track = track or prov.get("track", "")
        artists = artists or prov.get("artists", [])
        album = album or prov.get("album", "")

    if track and artists:
        g.title, g.artist, g.artist_source = track, ", ".join(artists), "field"
    else:
        art, title = split_artist_title(raw_title, channel)
        g.title = track or title or clean_title(raw_title) or raw_title
        if artists:
            g.artist, g.artist_source = ", ".join(artists), "field"
        elif art:
            g.artist, g.artist_source = art, "title"
        else:
            g.artist, g.artist_source = clean_channel(channel), "channel"
            remembered = memory.lookup(channel) if memory else ""
            if remembered:
                g.artist, g.artist_source = remembered, "memory"

    g.album = album
    if not g.album and playlist_title:
        g.album = clean_album_name(playlist_title)
    g.album_artist = g.artist.split(", ")[0] if g.artist and album else ""

    try:
        if info.get("track_number"):
            g.track_no = int(info["track_number"])
    except (TypeError, ValueError):
        pass
    year = info.get("release_year") or (info.get("release_date") or "")[:4]
    if year:
        g.year = str(year)
    genres = _as_list(info.get("genres")) or _as_list(info.get("genre"))
    g.genre = ", ".join(genres)
    return g


def clean_album_name(playlist_title: str) -> str:
    """재생목록 제목 → 앨범명 ('Album - 제목' 접두사 제거)."""
    text = re.sub(r"^\s*(album|앨범)\s*[-–:]\s*", "", playlist_title or "", flags=re.I)
    return text.strip()


def apply_guess(track: Track, g: Guess, keep_album: bool = False, keep_track_no: bool = False) -> None:
    """추정 결과를 Track 에 적용. 사용자가 직접 고친 필드(track.edited)는 건드리지 않는다."""
    mapping = {
        "title": g.title,
        "artist": g.artist,
        "album": g.album,
        "album_artist": g.album_artist,
        "year": g.year,
        "genre": g.genre,
    }
    for name, value in mapping.items():
        if name in track.edited:
            continue
        if name == "album" and keep_album and track.album:
            continue
        if value or not getattr(track, name):
            setattr(track, name, value)
    if not keep_track_no and "track_no" not in track.edited and g.track_no:
        track.track_no = g.track_no
    if "artist" not in track.edited:
        track.artist_source = g.artist_source


# ----------------------------------------------------------------------------
# MusicBrainz (무료 API, 초당 1회 제한, User-Agent 필수)
# ----------------------------------------------------------------------------
_MB_URL = "https://musicbrainz.org/ws/2/recording"
_MB_UA = "YouTubeAudioDownloader/1.0 (personal-use desktop tool)"
_mb_lock = threading.Lock()
_mb_last = 0.0


def _mb_get(url: str, timeout: float) -> dict | None:
    global _mb_last
    with _mb_lock:  # 초당 1회 이하로 호출
        wait = 1.1 - (time.monotonic() - _mb_last)
        if wait > 0:
            time.sleep(wait)
        _mb_last = time.monotonic()
        req = urllib.request.Request(url, headers={"User-Agent": _MB_UA, "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
            log.info("MusicBrainz 조회 실패: %s", exc)
            return None


def _lucene_escape(text: str) -> str:
    return re.sub(r'([+\-&|!(){}\[\]^"~*?:\\/])', r"\\\1", text)


def pick_release(recording: dict) -> dict | None:
    """녹음 결과에서 가장 그럴듯한 릴리스(정규 앨범 우선, 이른 발매일 우선)를 고른다."""
    releases = recording.get("releases") or []
    if not releases:
        return None

    def rank(rel: dict):
        group = rel.get("release-group") or {}
        is_album = group.get("primary-type") == "Album" and not group.get("secondary-types")
        official = rel.get("status") == "Official"
        date = rel.get("date") or "9999"
        return (not is_album, not official, date)

    return sorted(releases, key=rank)[0]


def musicbrainz_lookup(artist: str, title: str, timeout: float = 8.0) -> dict | None:
    """{'album':..., 'year':...} 또는 None. 실패해도 예외를 내지 않는다."""
    if not artist or not title:
        return None
    query = f'recording:"{_lucene_escape(title)}" AND artist:"{_lucene_escape(artist.split(", ")[0])}"'
    url = f"{_MB_URL}?{urllib.parse.urlencode({'query': query, 'fmt': 'json', 'limit': 5})}"
    data = _mb_get(url, timeout)
    if not data:
        return None
    for rec in data.get("recordings", []):
        if int(rec.get("score", 0)) < 90:
            continue
        rt, t = _norm(rec.get("title", "")), _norm(title)
        if not (rt == t or (len(t) >= 3 and (t in rt or rt in t))):
            continue
        rel = pick_release(rec)
        if not rel:
            continue
        return {
            "album": rel.get("title", ""),
            "year": (rel.get("date") or "")[:4],
            "track_no": _release_track_no(rel),
            "genre": _top_tag(rec) or _top_tag(rel.get("release-group") or {}),
        }
    return None


def _release_track_no(release: dict) -> int | None:
    """릴리스 안에서 이 녹음의 트랙 번호."""
    for medium in release.get("media") or []:
        for tr in medium.get("track") or []:
            try:
                return int(tr.get("number") or 0) or None
            except (TypeError, ValueError):
                pass
    return None


def _top_tag(entity: dict) -> str:
    """MusicBrainz 태그 중 가장 많이 붙은 것 (장르로 사용)."""
    tags = [t for t in entity.get("tags") or [] if t.get("name") and int(t.get("count") or 0) > 0]
    if not tags:
        return ""
    return max(tags, key=lambda t: int(t.get("count") or 0))["name"]


def enrich_with_musicbrainz(track: Track, timeout: float = 8.0) -> bool:
    """앨범/연도/트랙/장르가 비어 있으면 MusicBrainz 로 채운다. 사용자가 고친 값은 유지."""
    need_album = not track.album and "album" not in track.edited
    need_year = not track.year and "year" not in track.edited
    need_track = not track.track_no and "track_no" not in track.edited
    need_genre = not track.genre and "genre" not in track.edited
    if not (need_album or need_year or need_track or need_genre):
        return False
    found = musicbrainz_lookup(track.artist, track.title, timeout)
    if not found:
        return False
    changed = False
    album = found.get("album", "")
    if need_album and album:
        track.album, changed = album, True
    if need_year and found.get("year"):
        track.year, changed = found["year"], True
    # 트랙 번호는 찾은 앨범과 같은 앨범일 때만 (다른 앨범의 번호가 들어가지 않도록)
    if need_track and found.get("track_no") and album and _norm(track.album) == _norm(album):
        track.track_no, changed = found["track_no"], True
    if need_genre and found.get("genre"):
        track.genre, changed = found["genre"], True
    return changed
