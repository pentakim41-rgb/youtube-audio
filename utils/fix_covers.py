"""이미 받은 mp3 의 커버를 기기 호환 형식으로 다시 쓴다 (다시 다운로드하지 않음).

커버 프레임을 Latin-1 + 빈 설명으로 바꾸고, 600px 보다 크면 줄인다.
사용법: python -m utils.fix_covers [폴더]   (폴더를 생략하면 설정의 저장 폴더)
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

from mutagen.id3 import APIC, ID3, ID3NoHeaderError
from PIL import Image

from config import Settings

MAX_SIZE = 600


def _needs_fix(frame: APIC) -> bool:
    if frame.encoding != 0 or frame.desc or frame.mime != "image/jpeg":
        return True
    try:
        img = Image.open(io.BytesIO(frame.data))
        return max(img.size) > MAX_SIZE or bool(img.info.get("progressive"))
    except Exception:
        return False


def _to_jpeg(data: bytes) -> bytes:
    img = Image.open(io.BytesIO(data)).convert("RGB")
    if max(img.size) > MAX_SIZE:
        img.thumbnail((MAX_SIZE, MAX_SIZE), Image.LANCZOS)
    out = io.BytesIO()
    img.save(out, "JPEG", quality=92, progressive=False)
    return out.getvalue()


def fix_file(path: Path) -> bool:
    """고쳤으면 True. 커버가 없거나 이미 호환 형식이면 False."""
    try:
        tags = ID3(str(path))
    except ID3NoHeaderError:
        return False
    frames = tags.getall("APIC")
    if not frames:
        return False
    front = next((f for f in frames if f.type == 3), frames[0])
    if len(frames) == 1 and not _needs_fix(front):
        return False
    tags.delall("APIC")
    tags.add(APIC(encoding=0, mime="image/jpeg", type=3, desc="", data=_to_jpeg(front.data)))
    tags.save(str(path), v2_version=3)
    return True


def main(argv: list[str]) -> int:
    folder = Path(argv[0]) if argv else Path(Settings.load().output_dir)
    if not folder.is_dir():
        print(f"폴더가 없습니다: {folder}")
        return 1
    fixed = checked = 0
    for path in sorted(folder.rglob("*.mp3")):
        checked += 1
        try:
            if fix_file(path):
                fixed += 1
                print(f"수정: {path.name}")
        except Exception as exc:
            print(f"실패: {path.name} ({exc})")
    print(f"완료: mp3 {checked}개 중 {fixed}개 커버 수정")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main(sys.argv[1:]))
