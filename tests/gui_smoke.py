"""GUI 동작 점검용 스크립트 (pytest 아님). xvfb-run python3 tests/gui_smoke.py
가짜 조회/다운로드로 '추가 → 목록 → 선택 삭제 → 전체 다운로드' 흐름을 실제 창에서 실행하고 스크린샷을 남긴다."""
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
tmp = Path(tempfile.mkdtemp())
os.environ["XDG_CONFIG_HOME"] = str(tmp / "cfg")

import customtkinter as ctk  # noqa: E402
from tkinter import messagebox  # noqa: E402

from config import Settings  # noqa: E402
from core import pipeline as pipeline_mod  # noqa: E402
from core.fetcher import FetchResult  # noqa: E402
from core.history import ArtistMemory, History  # noqa: E402
from core.models import Track  # noqa: E402
from ui import main_window as mw  # noqa: E402
from utils.paths import find_ffmpeg  # noqa: E402

ffmpeg = find_ffmpeg()
src = tmp / "src.m4a"
subprocess.run([ffmpeg, "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-c:a", "aac", str(src)],
               check=True, capture_output=True)

NAMES = {"aaaaaaaaaaa": ("IU", "Blueming"), "bbbbbbbbbbb": ("아이유", "좋은 날"), "ccccccccccc": ("BTS", "Dynamite")}


def fake_fetch(info, settings, playlist, memory=None):
    a, t = NAMES[info.video_id]
    return FetchResult([Track(video_id=info.video_id, url="u", title=t, artist=a, album="", duration=200)])


def fake_download(t, workdir, s, cb, cancel):
    workdir.mkdir(parents=True, exist_ok=True)
    for i in range(1, 6):
        cb(i / 5, "1.0MB/s")
        time.sleep(0.05)
    dst = workdir / f"{t.video_id}.m4a"
    shutil.copy(src, dst)
    return dst, {"duration": 2}


mw.fetch_tracks = fake_fetch
pipeline_mod.download_audio = fake_download
pipeline_mod.fetch_cover = lambda *a, **k: None
pipeline_mod.fetch_lyrics = lambda *a, **k: None
pipeline_mod.work_root = lambda: tmp / "work"
messagebox.askyesno = lambda *a, **k: True
messagebox.askyesnocancel = lambda *a, **k: True
messagebox.showwarning = messagebox.showerror = messagebox.showinfo = lambda *a, **k: None

settings = Settings(output_dir=str(tmp / "out"), retries=0)
settings.normalize()
ctk.set_appearance_mode("light")
app = mw.MainWindow(settings, History(tmp / "h.json"), ArtistMemory(tmp / "m.json"))
app.after_cancel  # noqa
app._startup_checks = lambda: None


def pump(seconds):
    end = time.time() + seconds
    while time.time() < end:
        app.update()
        time.sleep(0.02)


def shot(name):
    pump(0.3)
    if sys.platform == "win32":  # 윈도우: 창 부분만 캡처
        from PIL import ImageGrab
        x, y = app.winfo_rootx(), app.winfo_rooty()
        ImageGrab.grab((x, y, x + app.winfo_width(), y + app.winfo_height())).save(tmp / name)
    else:
        subprocess.run(["import", "-window", "root", str(tmp / name)], capture_output=True)


pump(0.5)
# 1) 링크를 하나씩 계속 추가
for vid in NAMES:
    app.url_entry.insert(0, f"https://youtu.be/{vid}")
    app._add_from_entry()
    pump(0.5)
assert len(app.order) == 3, app.order
# 2) 같은 링크를 또 넣으면 중복으로 무시
app.url_entry.insert(0, "https://youtu.be/aaaaaaaaaaa")
app._add_from_entry()
pump(0.6)
assert len(app.order) == 3
# 3) 잘못된 링크는 목록에 안 들어감
app.url_entry.insert(0, "https://example.com/x")
app._add_from_entry()
pump(0.2)
assert len(app.order) == 3
shot("1_list.png")
# 4) 하나 선택 → 삭제
uid = app.order[1]
app.tree.selection_set(str(uid))
app.update()
app._delete_selected()
pump(0.2)
assert len(app.order) == 2 and uid not in app.tracks
# 5) 편집 (다중 선택 일괄 적용)
app._select_all()
app._apply_edit(app._selected_tracks(), {"album": "테스트 앨범"}, True)
assert all(t.album == "테스트 앨범" for t in app.tracks.values())
# 6) 전체 다운로드
app._download_all()
pump(0.4)
shot("2_downloading.png")
end = time.time() + 30
while time.time() < end and not all(t.status.finished for t in app.tracks.values()):
    pump(0.1)
pump(0.5)
shot("3_done.png")
# 7) 받은 곡 클릭 → 가사 구역에 가사 → 고쳐서 '다시저장'
from core.lyrics import read_lyrics, save_lyrics  # noqa: E402
t = next(t for t in app.tracks.values() if t.title == "Blueming")
save_lyrics(Path(t.output_path), "첫 줄\n둘째 줄")
app.tree.selection_set(str(t.uid))
pump(0.3)
assert app._lyrics_text() == "첫 줄\n둘째 줄", app._lyrics_text()
app.lyrics_box.delete("1.0", "end")
app.lyrics_box.insert("1.0", "첫 줄\n둘째 줄 고침")
shot("4_lyrics.png")
app._save_lyrics()
assert read_lyrics(Path(t.output_path)) == "첫 줄\n둘째 줄 고침"
res = {t.title: (t.status.value, t.output_path) for t in app.tracks.values()}
print(res)
assert all(v[0] == "완료" for v in res.values()), res
outs = sorted(p.relative_to(tmp / "out").as_posix() for p in (tmp / "out").rglob("*.mp3"))
print(outs)
assert outs == ["BTS - Dynamite.mp3", "IU - Blueming.mp3"], outs
app._clear_finished()
pump(0.2)
assert app.order == []
app._on_close()
print("GUI OK", tmp)
