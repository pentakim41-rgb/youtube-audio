"""가사 찾기·저장·읽기.

가사는 두 곳에 함께 저장한다.
  1) 음악 파일 안의 USLT 태그 — 시간 없이 순수 가사만
  2) 같은 이름의 .lrc 파일 — 줄마다 시간이 붙어 있어 워크맨 등에서 노래에 맞춰 넘어간다
화면에는 시간을 빼고 순수 가사만 보여 주고, 저장할 때 시간은 프로그램이 알아서 맞춘다.

가사 출처: LRCLIB (https://lrclib.net, 무료·키 없음). 못 찾으면 가사 없이 저장한다.
"""
from __future__ import annotations

import difflib
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from mutagen import File as MutagenFile
from mutagen.id3 import ID3, USLT, ID3NoHeaderError
from mutagen.wave import WAVE

from utils.logger import get_logger

log = get_logger("lyrics")

_API = "https://lrclib.net/api"
_UA = {"User-Agent": "YouTubeAudioDownloader (https://github.com/pentakim41-rgb/youtube-audio)"}
_UTF16 = 1  # mutagen Encoding.UTF16
_TIME = re.compile(r"\[(\d{1,3}):(\d{1,2}(?:[.:]\d{1,3})?)\]")
_SEC_PER_LINE = 4.0  # 곡 길이를 모를 때 줄 간격


@dataclass
class Lyrics:
    synced: str = ""  # LRC 형식 ([mm:ss.xx] 가사)
    plain: str = ""  # 시간 없는 가사

    def __bool__(self) -> bool:
        return bool(self.synced.strip() or self.plain.strip())


# ---------------------------------------------------------------- LRC 다루기
def parse_lrc(text: str) -> list[tuple[float, str]]:
    """LRC → [(초, 가사)] 시간순. [ar:..] 같은 정보 줄은 버린다."""
    out: list[tuple[float, str]] = []
    for raw in text.splitlines():
        stamps = []
        rest = raw.strip()
        while (m := _TIME.match(rest)):
            stamps.append(int(m.group(1)) * 60 + float(m.group(2).replace(":", ".")))
            rest = rest[m.end():]
        for sec in stamps:  # 한 줄에 시간이 여러 개면 (후렴 반복) 각각 한 줄로
            out.append((sec, rest.strip()))
    out.sort(key=lambda x: x[0])
    return out


def strip_timestamps(text: str) -> str:
    """LRC 에서 시간을 빼고 순수 가사만. 시간이 없는 글이면 그대로."""
    entries = parse_lrc(text)
    if not entries:
        return text.strip()
    return "\n".join(line for _, line in entries).strip()


def _fmt_time(sec: float) -> str:
    cs = int(round(max(sec, 0.0) * 100))
    m, cs = divmod(cs, 6000)
    return f"[{m:02d}:{cs // 100:02d}.{cs % 100:02d}]"


def build_lrc(lines: list[str], old: list[tuple[float, str]], duration: float | None) -> str:
    """고친 가사 줄에 시간을 붙인다.

    예전 .lrc 와 같거나 고쳐 쓴 줄은 예전 시간을 그대로 쓰고,
    새로 끼워 넣은 줄은 앞뒤 줄 사이 시간으로 나눠 준다.
    예전 시간이 없으면 곡 길이에 고르게 나눈다.
    """
    n = len(lines)
    if not n:
        return ""
    times: list[float | None] = [None] * n
    if old:
        sm = difflib.SequenceMatcher(a=[t for _, t in old], b=lines, autojunk=False)
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag in ("equal", "replace"):
                for k in range(min(i2 - i1, j2 - j1)):
                    times[j1 + k] = old[i1 + k][0]
    end = float(duration) if duration else None

    # 비어 있는 구간을 앞뒤 시간 사이로 고르게 채운다
    i = 0
    while i < n:
        if times[i] is not None:
            i += 1
            continue
        j = i
        while j < n and times[j] is None:
            j += 1
        start = times[i - 1] if i > 0 else 0.0
        if j < n:
            stop = times[j]
        elif end and end > start:
            stop = end
        else:
            stop = start + _SEC_PER_LINE * (j - i + 1)
        step = (stop - start) / (j - i + 1)
        for k in range(i, j):
            times[k] = start + step * (k - i + (1 if i > 0 else 0))
        i = j

    out, last = [], 0.0
    for sec, line in zip(times, lines):
        last = max(last, sec or 0.0)  # 시간은 뒤로 가지 않게
        out.append(f"{_fmt_time(last)}{line}")
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------- 가사 찾기 (LRCLIB)
def _get(path: str, params: dict, timeout: float):
    url = f"{_API}/{path}?{urllib.parse.urlencode(params)}"
    try:
        req = urllib.request.Request(url, headers=_UA)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            log.info("LRCLIB 오류 %s: %s", exc.code, url)
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
        log.info("LRCLIB 연결 실패: %s", exc)
    return None


def _simplify(title: str) -> str:
    """(feat. ..), [Live] 같은 괄호를 뺀 제목 (검색 보조용)."""
    return re.sub(r"\s*[(\[].*?[)\]]", "", title).strip() or title


def _key(text: str) -> str:
    """제목 비교용: 소문자, 글자·숫자만."""
    return "".join(ch for ch in text.casefold() if ch.isalnum())


def _to_lyrics(item) -> Lyrics | None:
    if not isinstance(item, dict) or item.get("instrumental"):
        return None
    found = Lyrics(item.get("syncedLyrics") or "", item.get("plainLyrics") or "")
    return found or None


def fetch_lyrics(artist: str, title: str, duration: int | None = None, timeout: float = 10.0) -> Lyrics | None:
    """가수·제목으로 가사를 찾는다. 시간이 붙은 가사를 우선한다. 못 찾으면 None."""
    if not title:
        return None
    if artist and duration:
        exact = _to_lyrics(_get("get", {"artist_name": artist, "track_name": title, "duration": int(duration)}, timeout))
        if exact and exact.synced:
            return exact

    results: list = []
    for params in ({"artist_name": artist, "track_name": title},
                   {"artist_name": artist, "track_name": _simplify(title)},
                   {"q": f"{artist} {_simplify(title)}".strip()}):
        if not params.get("artist_name", True):
            continue
        data = _get("search", params, timeout)
        if isinstance(data, list) and data:
            results = data
            break

    def rank(item: dict):
        gap = abs((item.get("duration") or 0) - duration) if duration else 0
        return (gap > 10, not item.get("syncedLyrics"), gap)

    want = _key(_simplify(title))
    for item in sorted((r for r in results if isinstance(r, dict)), key=rank):
        if duration and abs((item.get("duration") or 0) - duration) > 20:
            continue  # 길이가 너무 다르면 다른 곡(라이브/리믹스)일 가능성
        got = _key(item.get("trackName") or "")
        if not got or (want not in got and _key(_simplify(item.get("trackName") or "")) not in _key(title)):
            continue  # 같은 가수의 다른 곡
        lyr = _to_lyrics(item)
        if lyr:
            return lyr
    return None


# ---------------------------------------------------------------- 저장 / 읽기
def lrc_path(audio: Path) -> Path:
    return audio.with_suffix(".lrc")


def _id3_of(audio: Path):
    """(태그 객체, 저장 함수)."""
    if audio.suffix.lower() == ".wav":
        wav = WAVE(str(audio))
        if wav.tags is None:
            wav.add_tags()
        return wav.tags, lambda: wav.save(v2_version=3)
    try:
        tags = ID3(str(audio))
    except ID3NoHeaderError:
        tags = ID3()
    return tags, lambda: tags.save(str(audio), v2_version=3)


def embed_lyrics(audio: Path, text: str) -> None:
    """파일 안의 USLT 태그에 순수 가사를 넣는다. 빈 글이면 지운다."""
    tags, save = _id3_of(audio)
    tags.delall("USLT")
    text = text.strip()
    if text:
        tags.add(USLT(encoding=_UTF16, lang="kor", desc="", text=text))
    save()


def _duration(audio: Path) -> float | None:
    try:
        f = MutagenFile(str(audio))
        return float(f.info.length) if f and f.info else None
    except Exception:
        return None


def save_lyrics(audio: Path, text: str, synced: str = "") -> None:
    """가사를 음악 파일(USLT)과 .lrc 에 함께 저장.

    text  : 화면에 보이는 순수 가사
    synced: 처음 받은 시간 붙은 가사 (있으면 이 시간을 쓴다). 없으면 기존 .lrc 시간을 이어 쓴다.
    """
    plain = strip_timestamps(text) if _TIME.search(text) else text.strip()
    embed_lyrics(audio, plain)
    lrc = lrc_path(audio)
    if not plain:
        lrc.unlink(missing_ok=True)
        return
    old: list[tuple[float, str]] = []
    if synced:
        old = parse_lrc(synced)
    elif lrc.is_file():
        old = parse_lrc(lrc.read_text(encoding="utf-8-sig", errors="replace"))
    content = build_lrc(plain.splitlines(), old, _duration(audio))
    lrc.write_text(content, encoding="utf-8-sig")  # BOM: 기기들이 한글을 UTF-8 로 알아보게


def read_lyrics(audio: Path) -> str:
    """보여 줄 순수 가사. .lrc 가 있으면 그것을, 없으면 파일 안의 USLT 를 읽는다."""
    lrc = lrc_path(audio)
    if lrc.is_file():
        try:
            return strip_timestamps(lrc.read_text(encoding="utf-8-sig", errors="replace"))
        except OSError:
            pass
    try:
        tags, _ = _id3_of(audio)
        frames = tags.getall("USLT")
        return str(frames[0].text).strip() if frames else ""
    except Exception:
        return ""
