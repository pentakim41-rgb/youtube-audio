"""오류 분류: yt-dlp/ffmpeg/OS 의 어려운 오류 문구를 사용자용 메시지로 바꾼다."""
from __future__ import annotations

import re


class CancelledError(Exception):
    """사용자가 취소함."""


class AppError(Exception):
    """사용자에게 보여 줄 수 있는 분류된 오류."""

    def __init__(self, code: str, message: str, retryable: bool = False, detail: str = ""):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.detail = detail

    def __str__(self) -> str:
        return self.message


_ANSI = re.compile(r"\x1b\[[0-9;]*m")

# (코드, 재시도 여부, 사용자 메시지, 매칭 패턴)  ← 위에서부터 먼저 맞는 것을 사용
_RULES: list[tuple[str, bool, str, re.Pattern]] = [
    (
        "PRIVATE", False, "비공개 영상입니다.",
        re.compile(r"private video|video is private", re.I),
    ),
    (
        "MEMBERS", False, "채널 멤버십 전용 영상입니다.",
        re.compile(r"members-only|join this channel|membership", re.I),
    ),
    (
        "AGE", False,
        "연령 제한 영상입니다. 설정에서 '브라우저 쿠키'(로그인된 브라우저)를 지정하면 받을 수 있습니다.",
        re.compile(r"confirm your age|age-restricted|inappropriate for some users", re.I),
    ),
    (
        "BOT", False,
        "유튜브가 봇 확인을 요구합니다. 설정에서 '브라우저 쿠키'나 cookies.txt 를 지정하고, yt-dlp 를 최신으로 업데이트해 보세요.",
        re.compile(r"not a bot|confirm you.re not a bot|sign in to confirm", re.I),
    ),
    (
        "GEO", False, "지역 제한으로 이 나라에서는 볼 수 없는 영상입니다.",
        re.compile(r"available in your country|blocked .* in your country|geo.?restrict", re.I),
    ),
    (
        "LIVE", False, "라이브/예정된 방송은 지원하지 않습니다. 방송이 끝난 뒤 다시 시도하세요.",
        re.compile(r"live event will begin|is live|premieres in|this live event|live stream", re.I),
    ),
    (
        "UNAVAILABLE", False, "삭제되었거나 볼 수 없는 영상입니다.",
        re.compile(
            r"video unavailable|has been removed|no longer available|account .* terminated|"
            r"copyright|does not exist",
            re.I,
        ),
    ),
    (
        "RATE", True, "요청이 너무 많아 유튜브가 잠시 차단했습니다. 잠시 후 자동으로 다시 시도합니다.",
        re.compile(r"HTTP Error 429|too many requests", re.I),
    ),
    (
        "EXTRACT", False,
        "유튜브 구조가 바뀌어 추출에 실패했습니다. yt-dlp 를 최신으로 업데이트하고, "
        "JavaScript 런타임(Deno)이 설치돼 있는지 확인하세요.",
        re.compile(
            r"unable to extract|signature extraction|nsig|requested format is not available|"
            r"no supported javascript runtime|challenge solving failed|player response|"
            r"only images are available",
            re.I,
        ),
    ),
    (
        "FORBIDDEN", True, "유튜브가 접근을 거부했습니다(403). 잠시 후 다시 시도하거나 yt-dlp 를 업데이트하세요.",
        re.compile(r"HTTP Error 403|forbidden", re.I),
    ),
    (
        "NETWORK", True, "네트워크 오류입니다. 인터넷 연결을 확인하세요.",
        re.compile(
            r"timed out|timeout|connection (reset|refused|aborted|error)|temporary failure|"
            r"network is unreachable|getaddrinfo|name or service not known|incomplete read|"
            r"remote end closed|urlopen error|unable to download|ssl",
            re.I,
        ),
    ),
    (
        "DISK", False, "저장 공간이 부족합니다. 디스크 여유 공간을 확보하세요.",
        re.compile(r"no space left|not enough space|disk full", re.I),
    ),
    (
        "FFMPEG", False, "ffmpeg 를 찾을 수 없습니다. 설정에서 경로를 지정하거나 ffmpeg 를 설치하세요.",
        re.compile(r"ffmpeg.*not found|ffmpeg is not installed|ffprobe.*not found", re.I),
    ),
]


def clean_message(text: str) -> str:
    text = _ANSI.sub("", text or "")
    text = re.sub(r"^\s*ERROR:\s*", "", text.strip())
    return text


def classify(exc: BaseException) -> AppError:
    """임의의 예외를 AppError 로 변환."""
    if isinstance(exc, AppError):
        return exc
    raw = clean_message(str(exc))
    if isinstance(exc, OSError) and getattr(exc, "errno", None) == 28:
        raw = raw + " no space left"
    if isinstance(exc, PermissionError):
        return AppError("PERMISSION", "저장 폴더에 쓸 수 없습니다. 폴더 권한을 확인하세요.", False, raw)
    for code, retryable, message, pattern in _RULES:
        if pattern.search(raw):
            return AppError(code, message, retryable, raw)
    short = raw.splitlines()[0][:160] if raw else exc.__class__.__name__
    return AppError("UNKNOWN", f"알 수 없는 오류: {short}", True, raw)
