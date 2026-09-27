"""mutagen 으로 태그·커버 삽입.

mp3: ID3v2.3 + UTF-16 (Windows 탐색기/대부분의 플레이어에서 한글이 깨지지 않는 조합)
wav: 'id3 ' 청크에 ID3 태그를 추가 (LIST INFO 청크는 ffmpeg 가 변환 때 기록).
     wav 는 플레이어마다 태그 지원이 달라 표시되지 않을 수 있다.
"""
from __future__ import annotations

from pathlib import Path

from mutagen.id3 import APIC, ID3, ID3NoHeaderError, TALB, TCON, TDRC, TIT2, TPE1, TPE2, TRCK
from mutagen.wave import WAVE

from core.models import Track

_LATIN1 = 0  # mutagen Encoding.LATIN1
_UTF16 = 1  # mutagen Encoding.UTF16


def _frames(track: Track, cover: bytes | None, embed_cover: bool) -> list:
    frames = []
    if track.title:
        frames.append(TIT2(encoding=_UTF16, text=track.title))
    if track.artist:
        frames.append(TPE1(encoding=_UTF16, text=track.artist))
    if track.album:
        frames.append(TALB(encoding=_UTF16, text=track.album))
    if track.album_artist:
        frames.append(TPE2(encoding=_UTF16, text=track.album_artist))
    if track.track_no:
        frames.append(TRCK(encoding=_UTF16, text=str(int(track.track_no))))
    if track.year:
        frames.append(TDRC(encoding=_UTF16, text=str(track.year)))
    if track.genre:
        frames.append(TCON(encoding=_UTF16, text=track.genre))
    if cover and embed_cover:
        # 소니 워크맨 등 일부 기기는 커버 프레임이 Latin-1 + 빈 설명일 때만 커버를 표시한다
        frames.append(APIC(encoding=_LATIN1, mime="image/jpeg", type=3, desc="", data=cover))
    return frames


def write_tags(
    path: Path,
    fmt: str,
    track: Track,
    cover: bytes | None,
    wav_embed_cover: bool = False,
) -> None:
    if fmt == "mp3":
        try:
            tags = ID3(str(path))
            tags.delete()  # ffmpeg 가 넣은 인코더 태그 등 제거
            tags = ID3()
        except ID3NoHeaderError:
            tags = ID3()
        for frame in _frames(track, cover, embed_cover=True):
            tags.add(frame)
        tags.save(str(path), v2_version=3)
    elif fmt == "wav":
        audio = WAVE(str(path))
        if audio.tags is None:
            audio.add_tags()
        for frame in _frames(track, cover, embed_cover=wav_embed_cover):
            audio.tags.add(frame)
        audio.save(v2_version=3)
    else:
        raise ValueError(f"지원하지 않는 포맷: {fmt}")


def read_tags(path: Path) -> dict:
    """저장된 태그를 dict 로 읽는다 (테스트/확인용)."""
    if path.suffix.lower() == ".wav":
        tags = WAVE(str(path)).tags
    else:
        tags = ID3(str(path))
    if tags is None:
        return {}
    out = {}
    for key, name in (("TIT2", "title"), ("TPE1", "artist"), ("TALB", "album"),
                      ("TPE2", "album_artist"), ("TRCK", "track"), ("TDRC", "year"),
                      ("TCON", "genre")):
        if key in tags:
            out[name] = str(tags[key].text[0])
    out["has_cover"] = any(k.startswith("APIC") for k in tags.keys())
    return out
