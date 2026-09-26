"""앨범 커버: 썸네일을 받아 (검은 띠 제거 →) 정사각형으로 자른 JPEG 로 만든다."""
from __future__ import annotations

import io
import urllib.error
import urllib.request

from PIL import Image

from utils.logger import get_logger

log = get_logger("cover")
_UA = {"User-Agent": "Mozilla/5.0 (YouTubeAudioDownloader)"}


def candidate_urls(thumbnails: list[dict], fallback: str = "", limit: int = 4) -> list[str]:
    """해상도가 큰 순서대로 후보 URL 목록."""

    def area(t: dict) -> int:
        return int(t.get("width") or 0) * int(t.get("height") or 0)

    ordered = sorted((t for t in thumbnails if t.get("url")), key=area, reverse=True)
    urls: list[str] = []
    for t in ordered:
        if t["url"] not in urls:
            urls.append(t["url"])
    if fallback and fallback not in urls:
        urls.append(fallback)
    return urls[:limit]


def _trim_letterbox(img: Image.Image) -> Image.Image:
    """4:3 썸네일(hqdefault)의 위아래 검은 띠 제거. 확실할 때만 자른다."""
    w, h = img.size
    mask = img.convert("L").point(lambda p: 255 if p > 14 else 0)
    box = mask.getbbox()
    if not box:
        return img
    left, top, right, bottom = box
    if (right - left) >= w * 0.98 and 0.6 * h <= (bottom - top) < h * 0.97:
        return img.crop((0, top, w, bottom))
    return img


def make_cover(raw: bytes, crop_square: bool = True, max_size: int = 1000) -> bytes:
    img = Image.open(io.BytesIO(raw))
    img = img.convert("RGB")
    if crop_square:
        img = _trim_letterbox(img)
        w, h = img.size
        side = min(w, h)
        left, top = (w - side) // 2, (h - side) // 2
        img = img.crop((left, top, left + side, top + side))
    if max(img.size) > max_size:
        img.thumbnail((max_size, max_size), Image.LANCZOS)
    out = io.BytesIO()
    img.save(out, "JPEG", quality=92)
    return out.getvalue()


def fetch_cover(urls: list[str], crop_square: bool = True, timeout: float = 12.0) -> bytes | None:
    """후보 URL 을 차례로 시도해 커버 JPEG 바이트를 반환. 전부 실패하면 None (태그만 넣고 계속 진행)."""
    for url in urls:
        try:
            req = urllib.request.Request(url, headers=_UA)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
            return make_cover(raw, crop_square)
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            log.info("커버 다운로드 실패(%s): %s", url, exc)
        except Exception as exc:  # 이미지 해석 오류 등
            log.info("커버 처리 실패(%s): %s", url, exc)
    return None
