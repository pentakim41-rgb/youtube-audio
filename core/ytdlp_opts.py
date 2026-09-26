"""yt-dlp 공통 옵션 (조회/다운로드에서 함께 사용)."""
from __future__ import annotations

import os
import shutil
from pathlib import Path

from config import Settings
from utils.paths import app_dir, resource_dir


class QuietLogger:
    """yt-dlp 가 콘솔에 출력하지 않도록 하고 앱 로그로만 남긴다."""

    def __init__(self, log):
        self._log = log

    def debug(self, msg):  # noqa: D401
        pass

    def info(self, msg):
        pass

    def warning(self, msg):
        self._log.info("yt-dlp warning: %s", msg)

    def error(self, msg):
        self._log.warning("yt-dlp error: %s", msg)


def available_js_runtimes() -> dict:
    """JavaScript 런타임 (유튜브 추출에 필요). 없으면 빈 dict.

    앱과 함께 배포한 bin/deno.exe 를 먼저 쓰고, 그다음 PATH 에서 찾는다.
    """
    exe = "deno.exe" if os.name == "nt" else "deno"
    for base in (app_dir(), resource_dir()):
        bundled = base / "bin" / exe
        if bundled.is_file():
            return {"deno": {"path": str(bundled)}}
    return {name: {} for name in ("deno", "node", "bun", "quickjs") if shutil.which(name)}


def base_opts(settings: Settings, logger=None) -> dict:
    opts: dict = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "socket_timeout": 20,
        "retries": 5,
        "extractor_retries": 3,
        "noplaylist": True,
    }
    if logger is not None:
        opts["logger"] = QuietLogger(logger)
    runtimes = available_js_runtimes()
    if runtimes:
        opts["js_runtimes"] = runtimes
    if settings.cookies_file and Path(settings.cookies_file).is_file():
        opts["cookiefile"] = settings.cookies_file
    elif settings.cookies_browser:
        opts["cookiesfrombrowser"] = (settings.cookies_browser,)
    return opts
