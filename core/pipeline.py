"""한 곡을 처리하는 전체 순서.

  준비 점검 → 다운로드(0~70%) → 메타데이터 확정 → 변환(70~90%) → 커버·태그(90~97%) → 정리 저장(100%)

중간 파일은 임시 폴더에서만 만들고, 모든 처리가 끝난 뒤에 최종 폴더로 옮긴다.
(도중에 실패/취소해도 사용자의 음악 폴더에는 반쪽짜리 파일이 남지 않는다.)
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Callable

from config import Settings
from core import namer
from core.converter import convert
from core.cover import candidate_urls, fetch_cover
from core.downloader import download_audio
from core.errors import AppError, CancelledError
from core.history import ArtistMemory, History
from core.lyrics import fetch_lyrics, save_lyrics, strip_timestamps
from core.metadata import apply_guess, guess_from_info
from core.models import Status, Track
from core.tagger import write_tags
from utils.logger import get_logger
from utils.paths import find_ffmpeg, work_root

log = get_logger("pipeline")

Notify = Callable[[Track], None]

# 진행률 구간
DL_END, CONV_END, TAG_END = 70.0, 90.0, 97.0


def _existing_parent(path: Path) -> Path:
    p = path
    while not p.exists() and p != p.parent:
        p = p.parent
    return p


def estimate_bytes(track: Track, settings: Settings) -> tuple[int, int]:
    """(최종 파일 예상 용량, 원본 스트림 예상 용량). 길이를 모르면 5분으로 가정."""
    dur = track.duration or 300
    if settings.format == "wav":
        final = dur * settings.wav_sample_rate * 2 * (settings.wav_bit_depth // 8)
    else:
        final = dur * settings.mp3_bitrate * 1000 // 8
    return int(final), int(dur * 20_000)


def check_disk(track: Track, settings: Settings, workdir: Path) -> None:
    final, raw = estimate_bytes(track, settings)
    margin = 50 * 1024 * 1024
    checks = [(workdir, int((final + raw) * 1.2) + margin), (Path(settings.output_dir), int(final * 1.1) + margin)]
    for path, need in checks:
        free = shutil.disk_usage(_existing_parent(path)).free
        if free < need:
            raise AppError(
                "DISK",
                f"저장 공간이 부족합니다. (필요 약 {need // 1024 // 1024}MB, 여유 {free // 1024 // 1024}MB)",
            )


class Pipeline:
    def __init__(self, history: History, memory: ArtistMemory):
        self.history = history
        self.memory = memory

    # ------------------------------------------------------------------
    def run(self, track: Track, settings: Settings, notify: Notify) -> None:
        cancel = track.cancel_event

        def update(status: Status, pct: float, msg: str = "") -> None:
            track.status, track.progress, track.message = status, pct, msg
            notify(track)

        def check_cancel() -> None:
            if cancel.is_set():
                raise CancelledError()

        # ---- 0. 준비 점검 ------------------------------------------------
        ffmpeg = find_ffmpeg(settings.ffmpeg_path)
        if not ffmpeg:
            raise AppError("FFMPEG", "ffmpeg 를 찾을 수 없습니다. 설정에서 경로를 지정하거나 ffmpeg 를 설치하세요.")
        out_dir = Path(settings.output_dir)
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise AppError("PERMISSION", f"저장 폴더를 만들 수 없습니다: {exc}") from exc
        workdir = work_root() / track.video_id
        check_disk(track, settings, workdir)
        check_cancel()

        # 이미 정보가 확정된 곡은 다운로드 전에 '건너뛰기' 판단
        if track.resolved and self._skip_existing(track, settings, update):
            return

        # ---- 1. 다운로드 ---------------------------------------------------
        update(Status.DOWNLOADING, 0.0, "다운로드 준비 중")

        def dl_progress(frac: float, text: str) -> None:
            update(Status.DOWNLOADING, frac * DL_END, text)

        raw_path, info = download_audio(track, workdir, settings, dl_progress, cancel)
        check_cancel()

        # ---- 2. 메타데이터 확정 ---------------------------------------------
        if not track.resolved:
            g = guess_from_info(info, playlist_title=track.playlist_title, memory=self.memory)
            apply_guess(track, g, keep_album=True, keep_track_no=True)
            track.resolved = True
        if not track.duration and info.get("duration"):
            track.duration = int(info["duration"])
        # 재생목록 조회 때 받은 썸네일은 작은 것(최대 336x188)뿐이라 다운로드 때 받은 전체 목록으로 바꾼다
        if info.get("thumbnails"):
            track.thumbnails = [t for t in info["thumbnails"] if t.get("url")]
        if info.get("thumbnail"):
            track.thumbnail = info["thumbnail"]
        notify(track)
        if self._skip_existing(track, settings, update):
            shutil.rmtree(workdir, ignore_errors=True)
            return

        # ---- 3. 변환 -------------------------------------------------------
        converted = workdir / f"{track.video_id}.{settings.format}"
        if converted.exists():
            converted.unlink()

        def conv_progress(frac: float) -> None:
            update(Status.CONVERTING, DL_END + frac * (CONV_END - DL_END), "")

        update(Status.CONVERTING, DL_END, "")
        wav_meta = None
        if settings.format == "wav":
            wav_meta = {
                "title": track.title,
                "artist": track.artist,
                "album": track.album,
                "date": track.year,
                "track": str(track.track_no or ""),
                "genre": track.genre,
            }
        convert(ffmpeg, raw_path, converted, settings, track.duration, conv_progress, cancel, wav_meta)
        check_cancel()

        # ---- 4. 커버 + 태그 ---------------------------------------------------
        update(Status.TAGGING, CONV_END, "")
        cover = None
        urls = candidate_urls(track.thumbnails, track.thumbnail)
        if urls:
            cover = fetch_cover(urls, settings.crop_cover_square)
        check_cancel()
        try:
            write_tags(converted, settings.format, track, cover, settings.wav_embed_cover)
        except Exception as exc:
            log.warning("태그 삽입 실패: %s", exc)
            raise AppError("TAG", f"태그를 쓰지 못했습니다: {exc}", False) from exc
        update(Status.TAGGING, TAG_END, "")
        check_cancel()

        # ---- 5. 정리 저장 ---------------------------------------------------
        final = namer.build_output_path(track, settings)
        final.parent.mkdir(parents=True, exist_ok=True)
        if final.exists():
            if settings.on_exists == "overwrite":
                final.unlink()
                final.with_suffix(".lrc").unlink(missing_ok=True)  # 예전 곡의 가사가 남지 않게
            else:
                final = namer.unique_path(final)
        shutil.move(str(converted), str(final))
        if settings.fetch_lyrics:
            update(Status.TAGGING, TAG_END, "가사 찾는 중")
            self._add_lyrics(track, final)

        track.output_path = str(final)
        track.formats.add(settings.format)
        self.history.add(track, settings.format, str(final))
        if track.remember_artist and "artist" in track.edited and track.channel:
            self.memory.remember(track.channel, track.artist)
        shutil.rmtree(workdir, ignore_errors=True)
        update(Status.DONE, 100.0, "")

    # ------------------------------------------------------------------
    @staticmethod
    def _add_lyrics(track: Track, final: Path) -> None:
        """가사를 찾아 파일 안(USLT)과 .lrc 에 저장. 못 찾거나 실패해도 곡 저장은 그대로 완료."""
        try:
            found = fetch_lyrics(track.artist, track.title, track.duration)
            if found:
                text = strip_timestamps(found.synced) if found.synced else found.plain
                save_lyrics(final, text, found.synced)
        except Exception as exc:
            log.warning("가사 저장 실패 (%s): %s", track.display_name(), exc)

    # ------------------------------------------------------------------
    def _skip_existing(self, track: Track, settings: Settings, update) -> bool:
        if settings.on_exists != "skip":
            return False
        final = namer.build_output_path(track, settings)
        if final.exists():
            track.output_path = str(final)
            track.formats.add(settings.format)
            update(Status.SKIPPED, 100.0, "이미 같은 이름의 파일이 있어 건너뜀")
            return True
        return False
