"""다운로드 기록(중복 방지)과 '채널 → 가수 이름' 기억."""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime
from pathlib import Path

from core.models import Track
from utils.paths import user_data_dir


class _JsonStore:
    filename = "store.json"

    def __init__(self, path: Path | None = None):
        self.path = path or (user_data_dir() / self.filename)
        self._lock = threading.Lock()
        self.data: dict = {}
        self._load()

    def _load(self) -> None:
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                self.data = loaded
        except (OSError, ValueError):
            self.data = {}

    def _save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), encoding="utf-8")
            os.replace(tmp, self.path)  # 저장 중 종료돼도 파일이 깨지지 않도록
        except OSError:
            pass


class History(_JsonStore):
    """영상 ID 별로 어떤 포맷을 어디에 저장했는지 기록한다."""

    filename = "history.json"

    def find(self, video_id: str, fmt: str) -> str:
        """이미 받은 파일 경로(파일이 실제로 남아 있을 때만). 없으면 ''."""
        with self._lock:
            rec = self.data.get(video_id)
        if not rec:
            return ""
        path = (rec.get("files") or {}).get(fmt, "")
        return path if path and Path(path).is_file() else ""

    def formats(self, video_id: str) -> set[str]:
        """파일이 아직 남아 있는, 예전에 받은 형식들."""
        with self._lock:
            files = dict((self.data.get(video_id) or {}).get("files") or {})
        return {fmt for fmt, path in files.items() if path and Path(path).is_file()}

    def add(self, track: Track, fmt: str, path: str) -> None:
        with self._lock:
            rec = self.data.setdefault(track.video_id, {"files": {}})
            rec["title"] = track.title
            rec["artist"] = track.artist
            rec["album"] = track.album
            rec["date"] = datetime.now().isoformat(timespec="seconds")
            rec.setdefault("files", {})[fmt] = path
            self._save()


class ArtistMemory(_JsonStore):
    """사용자가 고친 가수 이름을 채널 별로 기억. 제목에서 가수를 못 찾을 때만 사용된다."""

    filename = "artist_memory.json"

    @staticmethod
    def _key(channel: str) -> str:
        return (channel or "").strip().casefold()

    def lookup(self, channel: str) -> str:
        with self._lock:
            return self.data.get(self._key(channel), "")

    def remember(self, channel: str, artist: str) -> None:
        key = self._key(channel)
        if not key or not artist.strip():
            return
        with self._lock:
            self.data[key] = artist.strip()
            self._save()
