"""화면·큐·파이프라인이 함께 쓰는 데이터 모델."""
from __future__ import annotations

import itertools
import threading
from dataclasses import dataclass, field
from enum import Enum

_uid_counter = itertools.count(1)


class Status(str, Enum):
    READY = "준비"  # 조회 완료, 아직 다운로드 시작 전
    QUEUED = "대기열"
    DOWNLOADING = "다운로드 중"
    CONVERTING = "변환 중"
    TAGGING = "태그 작성 중"
    RETRYING = "재시도 대기"
    DONE = "완료"
    FAILED = "실패"
    CANCELLED = "취소됨"
    SKIPPED = "건너뜀"

    @property
    def finished(self) -> bool:
        return self in (Status.DONE, Status.FAILED, Status.CANCELLED, Status.SKIPPED)

    @property
    def active(self) -> bool:
        return self in (
            Status.QUEUED,
            Status.DOWNLOADING,
            Status.CONVERTING,
            Status.TAGGING,
            Status.RETRYING,
        )


# 사용자가 화면에서 직접 고칠 수 있는 태그 필드
EDITABLE_FIELDS = ("title", "artist", "album", "album_artist", "track_no", "year", "genre")


@dataclass
class Track:
    """한 곡(영상)에 대한 모든 정보."""

    video_id: str
    url: str
    title: str = ""
    artist: str = ""
    album: str = ""
    album_artist: str = ""
    track_no: int | None = None
    year: str = ""
    genre: str = ""
    channel: str = ""
    duration: int | None = None  # 초
    thumbnail: str = ""
    playlist_title: str = ""
    is_playlist_item: bool = False
    # False 면 재생목록 '요약' 정보만 있는 상태 → 다운로드 때 전체 정보로 보완한다
    resolved: bool = True
    artist_source: str = ""  # field / title / channel / memory / user
    edited: set[str] = field(default_factory=set)  # 사용자가 직접 고친 필드
    remember_artist: bool = True  # 채널 → 가수 이름 기억
    thumbnails: list[dict] = field(default_factory=list)

    # ---- 진행 상태 ----
    uid: int = field(default_factory=lambda: next(_uid_counter))
    status: Status = Status.READY
    progress: float = 0.0  # 0~100
    message: str = ""
    error_code: str = ""
    output_path: str = ""
    attempts: int = 0
    cancel_event: threading.Event = field(default_factory=threading.Event, repr=False)

    def display_name(self) -> str:
        if self.artist and self.title:
            return f"{self.artist} - {self.title}"
        return self.title or self.video_id

    def reset_for_retry(self) -> None:
        self.status = Status.READY
        self.progress = 0.0
        self.message = ""
        self.error_code = ""
        self.attempts = 0
        self.cancel_event = threading.Event()
