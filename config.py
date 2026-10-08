"""사용자 설정 (JSON 으로 저장/불러오기)."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from utils.paths import default_output_dir, legacy_output_dir, user_data_dir

# 설정 화면과 검증에 함께 쓰는 선택지
FORMATS = ("mp3", "wav")
MP3_BITRATES = (128, 192, 256, 320)
WAV_SAMPLE_RATES = (44100, 48000)
WAV_BIT_DEPTHS = (16, 24)
FILENAME_STYLES = {
    "artist_title": "가수 - 제목",
    "track_title": "트랙번호 - 제목",
    "title": "제목",
}
ON_EXISTS = {"rename": "번호 붙여 저장", "overwrite": "덮어쓰기", "skip": "건너뛰기"}
BROWSERS = ("", "firefox", "edge", "chrome", "brave", "opera", "vivaldi", "safari")


@dataclass
class Settings:
    output_dir: str = ""
    format: str = "mp3"
    mp3_bitrate: int = 320
    wav_sample_rate: int = 44100
    wav_bit_depth: int = 16
    filename_style: str = "artist_title"
    on_exists: str = "rename"
    max_concurrent: int = 2
    retries: int = 3
    strip_emoji: bool = True
    crop_cover_square: bool = True
    use_musicbrainz: bool = True
    fetch_lyrics: bool = True  # LRCLIB 에서 가사를 찾아 함께 저장
    wav_embed_cover: bool = False  # wav 의 커버 삽입은 플레이어 호환성이 낮아 기본 꺼짐
    cookies_browser: str = ""  # 연령 제한/봇 확인 시 사용
    cookies_file: str = ""  # cookies.txt (Netscape 형식)
    ffmpeg_path: str = ""
    auto_start: bool = False  # 조회 후 바로 다운로드 시작
    check_updates: bool = True

    # ---- 저장/불러오기 ----
    @staticmethod
    def path() -> Path:
        return user_data_dir() / "settings.json"

    @classmethod
    def load(cls) -> "Settings":
        s = cls()
        try:
            data = json.loads(cls.path().read_text(encoding="utf-8"))
            names = {f.name for f in fields(cls)}
            for k, v in data.items():
                if k in names:
                    setattr(s, k, v)
        except (OSError, ValueError):
            pass
        s.normalize()
        return s

    def save(self) -> None:
        self.normalize()
        try:
            self.path().write_text(
                json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError:
            pass

    def normalize(self) -> None:
        """잘못된 값이 들어와도 프로그램이 죽지 않도록 보정."""
        if not self.output_dir or Path(self.output_dir) == legacy_output_dir():
            self.output_dir = str(default_output_dir())
        if self.format not in FORMATS:
            self.format = "mp3"
        if self.mp3_bitrate not in MP3_BITRATES:
            self.mp3_bitrate = 320
        if self.wav_sample_rate not in WAV_SAMPLE_RATES:
            self.wav_sample_rate = 44100
        if self.wav_bit_depth not in WAV_BIT_DEPTHS:
            self.wav_bit_depth = 16
        if self.filename_style not in FILENAME_STYLES:
            self.filename_style = "artist_title"
        if self.on_exists not in ON_EXISTS:
            self.on_exists = "rename"
        if self.cookies_browser not in BROWSERS:
            self.cookies_browser = ""
        self.max_concurrent = min(max(int(self.max_concurrent), 1), 4)
        self.retries = min(max(int(self.retries), 0), 10)
