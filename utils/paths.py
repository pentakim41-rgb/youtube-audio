"""경로 관련 유틸: 앱 폴더, 사용자 데이터 폴더, ffmpeg 탐색, 폴더 열기."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

APP_NAME = "YouTubeAudio"


def is_frozen() -> bool:
    """PyInstaller 등으로 묶인 exe 로 실행 중인지."""
    return bool(getattr(sys, "frozen", False))


def app_dir() -> Path:
    """소스(또는 exe)가 있는 폴더."""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def resource_dir() -> Path:
    """번들된 리소스(ffmpeg 등)가 있는 폴더."""
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS", app_dir()))
    return app_dir()


def user_data_dir() -> Path:
    """설정/기록/로그를 저장하는 사용자 폴더."""
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    path = base / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


OUTPUT_FOLDER_NAME = "추출사운드"


def default_output_dir() -> Path:
    """기본 저장 폴더: 프로그램(또는 exe) 폴더 안의 '추출사운드' (없으면 만든다)."""
    path = app_dir() / OUTPUT_FOLDER_NAME
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return path


def legacy_output_dir() -> Path:
    """예전 기본 저장 폴더 (저장된 설정에 이 값이 있으면 새 기본값으로 바꾼다)."""
    return Path.home() / "Music" / "YouTubeAudio"


def work_root() -> Path:
    """다운로드/변환 중간 파일을 두는 임시 폴더 (이어받기용으로 영상 ID별 하위 폴더 사용)."""
    path = Path(tempfile.gettempdir()) / "yt_audio_work"
    path.mkdir(parents=True, exist_ok=True)
    return path


def find_ffmpeg(custom: str = "") -> str | None:
    """ffmpeg 실행 파일 경로를 찾는다.

    우선순위: 설정에서 지정한 경로 → 앱 옆 bin 폴더 → PATH → imageio-ffmpeg 번들.
    """
    exe = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
    if custom:
        p = Path(custom)
        if p.is_dir():
            p = p / exe
        if p.is_file():
            return str(p)
    for base in (app_dir(), resource_dir()):
        p = base / "bin" / exe
        if p.is_file():
            return str(p)
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:  # pip install imageio-ffmpeg 하면 ffmpeg 바이너리가 같이 설치된다
        import imageio_ffmpeg

        p = imageio_ffmpeg.get_ffmpeg_exe()
        if p and Path(p).is_file():
            return p
    except Exception:
        pass
    return None


def subprocess_flags() -> int:
    """Windows 에서 ffmpeg 실행 시 콘솔 창이 깜빡이지 않게 한다."""
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0


def open_folder(path: str | Path) -> None:
    path = str(path)
    try:
        if os.name == "nt":
            os.startfile(path)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
    except Exception:
        pass


def cleanup_old_work_dirs(days: int = 7) -> None:
    """오래된 임시 작업 폴더 정리 (실패한 항목의 이어받기 파일이 쌓이지 않도록)."""
    import time

    limit = time.time() - days * 86400
    try:
        for child in work_root().iterdir():
            try:
                if child.is_dir() and child.stat().st_mtime < limit:
                    shutil.rmtree(child, ignore_errors=True)
            except OSError:
                continue
    except OSError:
        pass
