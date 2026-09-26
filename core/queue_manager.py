"""작업 큐: 동시 실행 개수 제한, 취소, 실패 시 간격을 늘려 자동 재시도."""
from __future__ import annotations

import copy
import queue
import threading
from typing import Callable

from config import Settings
from core.errors import CancelledError, classify
from core.history import ArtistMemory, History
from core.models import Status, Track
from core.pipeline import Pipeline
from utils.logger import get_logger

log = get_logger("queue")
_STOP = object()


class QueueManager:
    """on_event(track) 는 워커 스레드에서 호출된다 → UI 쪽에서는 스레드 안전한 큐로 받아 처리할 것."""

    def __init__(
        self,
        settings: Settings,
        history: History,
        memory: ArtistMemory,
        on_event: Callable[[Track], None],
    ):
        self.settings = settings
        self.on_event = on_event
        self.pipeline = Pipeline(history, memory)
        self._q: "queue.Queue[object]" = queue.Queue()
        self._lock = threading.Lock()
        self._workers = 0
        self.set_concurrency(settings.max_concurrent)

    # ---- 워커 관리 -----------------------------------------------------------
    def set_concurrency(self, n: int) -> None:
        n = min(max(int(n), 1), 4)
        with self._lock:
            while self._workers < n:
                threading.Thread(target=self._worker, daemon=True, name="ytaudio-worker").start()
                self._workers += 1
            while self._workers > n:
                self._q.put(_STOP)  # 여유 워커 하나가 종료 신호를 받아 빠진다
                self._workers -= 1

    def shutdown(self) -> None:
        self.cancel_all()
        with self._lock:
            for _ in range(self._workers):
                self._q.put(_STOP)
            self._workers = 0

    # ---- 작업 등록/취소 ------------------------------------------------------------
    def enqueue(self, track: Track) -> None:
        if track.cancel_event.is_set() or track.status.finished:
            track.reset_for_retry()
        track.status, track.progress, track.message = Status.QUEUED, 0.0, ""
        self.on_event(track)
        self._q.put(track)

    def cancel(self, track: Track) -> None:
        track.cancel_event.set()
        if track.status in (Status.QUEUED, Status.READY, Status.RETRYING):
            track.status, track.message = Status.CANCELLED, "취소됨"
            self.on_event(track)

    def cancel_all(self) -> None:
        # 대기열에 남은 작업도 워커가 꺼낼 때 취소 상태로 건너뛴다 (cancel_event 확인)
        for item in list(self._q.queue):
            if isinstance(item, Track):
                self.cancel(item)

    # ---- 내부 -----------------------------------------------------------------------
    def _worker(self) -> None:
        while True:
            job = self._q.get()
            try:
                if job is _STOP:
                    return
                assert isinstance(job, Track)
                self._run(job)
            except Exception:  # 워커가 죽지 않도록 최후의 방어
                log.exception("작업 처리 중 예기치 못한 오류")
            finally:
                self._q.task_done()

    def _set(self, track: Track, status: Status, message: str = "", code: str = "") -> None:
        track.status, track.message, track.error_code = status, message, code
        self.on_event(track)

    def _run(self, track: Track) -> None:
        if track.cancel_event.is_set():
            self._set(track, Status.CANCELLED, "취소됨")
            return
        settings = copy.copy(self.settings)  # 실행 중 설정이 바뀌어도 한 곡은 일관되게
        while True:
            track.attempts += 1
            try:
                self.pipeline.run(track, settings, self.on_event)
                return
            except CancelledError:
                self._set(track, Status.CANCELLED, "취소됨")
                return
            except Exception as exc:
                err = classify(exc)
                log.warning("[%s] %s (%s) %s", track.video_id, err.code, err.message, err.detail[:500])
                if err.retryable and track.attempts <= settings.retries:
                    delay = min(5 * 3 ** (track.attempts - 1), 120)  # 5, 15, 45, 120초…
                    self._set(
                        track,
                        Status.RETRYING,
                        f"{err.message} {delay}초 후 재시도 ({track.attempts}/{settings.retries})",
                        err.code,
                    )
                    if track.cancel_event.wait(delay):
                        self._set(track, Status.CANCELLED, "취소됨")
                        return
                    continue
                self._set(track, Status.FAILED, err.message, err.code)
                return
