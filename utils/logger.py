"""로그 설정. 문제가 생기면 사용자 폴더의 logs/app.log 를 확인한다."""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from utils.paths import user_data_dir

_configured = False


def setup_logging(level: int = logging.INFO) -> logging.Logger:
    global _configured
    logger = logging.getLogger("ytaudio")
    if _configured:
        return logger
    logger.setLevel(level)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    try:
        log_dir = user_data_dir() / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        fh = RotatingFileHandler(
            log_dir / "app.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8"
        )
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    except OSError:
        pass
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    logger.addHandler(sh)
    _configured = True
    return logger


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"ytaudio.{name}")
