"""yt-dlp 버전 확인/업데이트와 실행 환경 점검.

유튜브는 자주 바뀌므로 yt-dlp 가 오래되면 갑자기 다운로드가 실패한다.
시작할 때 최신 버전인지 확인하고, 사용자가 원하면 pip 로 업데이트한다.
"""
from __future__ import annotations

import json
import subprocess
import sys
import urllib.error
import urllib.request

from core.ytdlp_opts import available_js_runtimes
from utils.logger import get_logger
from utils.paths import find_ffmpeg, is_frozen, subprocess_flags

log = get_logger("updater")


def installed_version() -> str:
    try:
        from yt_dlp.version import __version__

        return __version__
    except Exception:
        return ""


def latest_version(timeout: float = 6.0) -> str:
    """PyPI 에서 최신 yt-dlp 버전. 실패하면 ''."""
    try:
        with urllib.request.urlopen("https://pypi.org/pypi/yt-dlp/json", timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))["info"]["version"]
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, KeyError):
        return ""


def _key(version: str) -> tuple:
    parts = []
    for p in version.replace("-", ".").split("."):
        parts.append(int(p) if p.isdigit() else 0)
    return tuple(parts)


def is_outdated(current: str, latest: str) -> bool:
    return bool(current and latest and _key(latest) > _key(current))


def can_self_update() -> bool:
    """exe 로 묶인 상태에서는 pip 로 갱신할 수 없다 (재빌드 필요)."""
    return not is_frozen()


def update_ytdlp(timeout: int = 300) -> tuple[bool, str]:
    """pip 로 yt-dlp 업데이트. (성공 여부, 출력). 성공 후에는 프로그램을 다시 시작해야 적용된다."""
    if not can_self_update():
        return False, "exe 배포판은 자동 업데이트를 지원하지 않습니다. 새로 빌드해 주세요."
    cmd = [sys.executable, "-m", "pip", "install", "-U", "yt-dlp[default]"]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout, creationflags=subprocess_flags(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    out = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode == 0, out[-800:]


def environment_warnings(ffmpeg_custom: str = "") -> list[str]:
    """시작 시 보여 줄 환경 경고 목록 (없으면 빈 리스트)."""
    warnings = []
    if not find_ffmpeg(ffmpeg_custom):
        warnings.append("ffmpeg 를 찾을 수 없습니다. 설정에서 경로를 지정하거나 설치하세요. (pip install imageio-ffmpeg 도 가능)")
    if not available_js_runtimes():
        warnings.append(
            "JavaScript 런타임(Deno)이 없습니다. 최신 yt-dlp 는 유튜브 추출에 필요합니다. "
            "Windows: winget install DenoLand.Deno"
        )
    return warnings
