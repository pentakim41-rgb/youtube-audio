"""다운로드만 가짜로 대체하고 (ffmpeg 변환 → 태그 → 커버 → 정리 저장 → 기록) 전체 흐름을 실제로 실행."""
import io
import shutil
import subprocess
import threading
import time

import pytest
from mutagen.id3 import ID3
from PIL import Image

from config import Settings
from core import pipeline as pipeline_mod
from core.errors import AppError, CancelledError
from core.history import ArtistMemory, History
from core.models import Status, Track
from core.queue_manager import QueueManager
from core.tagger import read_tags
from utils.paths import find_ffmpeg

FFMPEG = find_ffmpeg()
pytestmark = pytest.mark.skipif(not FFMPEG, reason="ffmpeg 없음")


@pytest.fixture
def source(tmp_path):
    src = tmp_path / "src.m4a"
    subprocess.run(
        [FFMPEG, "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=3", "-c:a", "aac", str(src)],
        check=True, capture_output=True,
    )
    return src


@pytest.fixture
def cover_bytes():
    img = Image.new("RGB", (640, 360), (200, 30, 30))  # 16:9 → 정사각형 크롭 확인용
    buf = io.BytesIO()
    img.save(buf, "JPEG")
    return buf.getvalue()


def run_track(tmp_path, monkeypatch, source, cover_bytes, track, **settings_kw):
    settings = Settings(output_dir=str(tmp_path / "out"), retries=0, **settings_kw)
    settings.normalize()
    monkeypatch.setattr(pipeline_mod, "work_root", lambda: tmp_path / "work")

    def fake_download(t, workdir, s, on_progress, cancel):
        workdir.mkdir(parents=True, exist_ok=True)
        dst = workdir / f"{t.video_id}.m4a"
        shutil.copy(source, dst)
        on_progress(1.0, "")
        return dst, {"duration": 3, "thumbnails": []}

    monkeypatch.setattr(pipeline_mod, "download_audio", fake_download)
    monkeypatch.setattr(pipeline_mod, "fetch_cover", lambda urls, crop: __import__("core.cover", fromlist=["x"]).make_cover(cover_bytes, crop))
    history = History(tmp_path / "history.json")
    memory = ArtistMemory(tmp_path / "mem.json")
    events, done = [], threading.Event()

    def on_event(t):
        events.append((t.status, round(t.progress)))
        if t.status.finished:
            done.set()

    qm = QueueManager(settings, history, memory, on_event)
    qm.enqueue(track)
    assert done.wait(60), "시간 초과"
    qm.shutdown()
    return settings, history, memory, events


def make_track(**kw):
    base = dict(video_id="abcdefghijk", url="u", title="Blueming", artist="IU", album="Love poem",
                year="2019", track_no=3, genre="K-Pop", duration=3, thumbnail="http://x/y.jpg", channel="IU Official")
    base.update(kw)
    return Track(**base)


def test_mp3_full_flow(tmp_path, monkeypatch, source, cover_bytes):
    t = make_track()
    t.edited.add("artist")
    settings, history, memory, events = run_track(tmp_path, monkeypatch, source, cover_bytes, t, format="mp3")
    assert t.status == Status.DONE, t.message
    out = tmp_path / "out" / "IU - Blueming.mp3"
    assert out.is_file() and str(out) == t.output_path
    tags = read_tags(out)
    assert tags["title"] == "Blueming" and tags["artist"] == "IU" and tags["album"] == "Love poem"
    assert tags["year"] == "2019" and tags["track"] == "3" and tags["genre"] == "K-Pop" and tags["has_cover"]
    assert not ID3(str(out)).getall("COMM")  # 설명란에 유튜브 링크를 넣지 않는다
    apic = ID3(str(out)).getall("APIC")[0]
    assert apic.encoding == 0 and apic.desc == ""  # 워크맨 호환: Latin-1 + 빈 설명
    assert history.find("abcdefghijk", "mp3") == str(out)
    assert memory.lookup("IU Official") == "IU"  # 직접 고친 가수 이름 기억
    pcts = [p for _, p in events]
    assert max(pcts) == 100 and pcts[0] <= 70
    assert not (tmp_path / "work" / "abcdefghijk").exists()  # 임시 파일 정리


def test_wav_full_flow(tmp_path, monkeypatch, source, cover_bytes):
    t = make_track(title="한글 제목: 테스트?", artist="아이유", album="러브 포엠")
    settings, *_ = run_track(tmp_path, monkeypatch, source, cover_bytes, t, format="wav", wav_bit_depth=24)
    assert t.status == Status.DONE, t.message
    out = tmp_path / "out" / "아이유 - 한글 제목 - 테스트.wav"
    assert out.is_file()
    tags = read_tags(out)
    assert tags["title"] == "한글 제목: 테스트?" and tags["artist"] == "아이유"
    probe = subprocess.run([FFMPEG, "-i", str(out)], capture_output=True, text=True, encoding="utf-8", errors="replace").stderr
    assert "pcm_s24le" in probe and "44100 Hz" in probe


def test_existing_file_policies(tmp_path, monkeypatch, source, cover_bytes):
    out = tmp_path / "out" / "IU - Blueming.mp3"
    out.parent.mkdir(parents=True)
    out.write_bytes(b"old")
    t = make_track()
    run_track(tmp_path, monkeypatch, source, cover_bytes, t, format="mp3", on_exists="rename")
    assert t.status == Status.DONE and t.output_path.endswith("IU - Blueming (2).mp3")
    assert out.read_bytes() == b"old"

    t2 = make_track()
    run_track(tmp_path, monkeypatch, source, cover_bytes, t2, format="mp3", on_exists="skip")
    assert t2.status == Status.SKIPPED

    t3 = make_track()
    run_track(tmp_path, monkeypatch, source, cover_bytes, t3, format="mp3", on_exists="overwrite")
    assert t3.status == Status.DONE and out.read_bytes() != b"old"


def test_unresolved_playlist_track_gets_metadata_from_download(tmp_path, monkeypatch, source, cover_bytes):
    t = Track(video_id="abcdefghijk", url="u", title="IU - Blueming (MV)", artist="1theK", album="Love poem",
              track_no=2, resolved=False, is_playlist_item=True, playlist_title="Love poem")
    settings = Settings(output_dir=str(tmp_path / "out"), retries=0)
    monkeypatch.setattr(pipeline_mod, "work_root", lambda: tmp_path / "work")

    def fake_download(t_, workdir, s, cb, cancel):
        workdir.mkdir(parents=True, exist_ok=True)
        dst = workdir / "abcdefghijk.m4a"
        shutil.copy(source, dst)
        return dst, {"title": "IU - Blueming (MV)", "track": "Blueming", "artists": ["IU"], "duration": 3}

    monkeypatch.setattr(pipeline_mod, "download_audio", fake_download)
    monkeypatch.setattr(pipeline_mod, "fetch_cover", lambda urls, crop: None)
    p = pipeline_mod.Pipeline(History(tmp_path / "h.json"), ArtistMemory(tmp_path / "m.json"))
    p.run(t, settings, lambda _t: None)
    assert (t.title, t.artist, t.album, t.track_no) == ("Blueming", "IU", "Love poem", 2)
    assert (tmp_path / "out" / "IU - Blueming.mp3").is_file()


def test_download_error_marks_failed_without_retry(tmp_path, monkeypatch, source, cover_bytes):
    settings = Settings(output_dir=str(tmp_path / "out"), retries=3)
    monkeypatch.setattr(pipeline_mod, "work_root", lambda: tmp_path / "work")

    def boom(*a, **k):
        raise AppError("PRIVATE", "비공개 영상입니다.")

    monkeypatch.setattr(pipeline_mod, "download_audio", boom)
    done = threading.Event()
    t = make_track()
    qm = QueueManager(settings, History(tmp_path / "h.json"), ArtistMemory(tmp_path / "m.json"),
                      lambda x: done.set() if x.status.finished else None)
    qm.enqueue(t)
    assert done.wait(10)
    qm.shutdown()
    assert t.status == Status.FAILED and t.error_code == "PRIVATE" and t.attempts == 1


def test_cancel_during_download(tmp_path, monkeypatch):
    settings = Settings(output_dir=str(tmp_path / "out"), retries=0)
    monkeypatch.setattr(pipeline_mod, "work_root", lambda: tmp_path / "work")
    started = threading.Event()

    def slow(t, workdir, s, cb, cancel):
        started.set()
        while not cancel.is_set():
            time.sleep(0.02)
        raise CancelledError()

    monkeypatch.setattr(pipeline_mod, "download_audio", slow)
    done = threading.Event()
    t = make_track()
    qm = QueueManager(settings, History(tmp_path / "h.json"), ArtistMemory(tmp_path / "m.json"),
                      lambda x: done.set() if x.status.finished else None)
    qm.enqueue(t)
    assert started.wait(10)
    qm.cancel(t)
    assert done.wait(10)
    qm.shutdown()
    assert t.status == Status.CANCELLED
    assert not (tmp_path / "out").exists() or not list((tmp_path / "out").rglob("*.mp3"))


def test_cancel_queued_and_concurrency_limit(tmp_path, monkeypatch):
    settings = Settings(output_dir=str(tmp_path / "out"), retries=0, max_concurrent=2)
    monkeypatch.setattr(pipeline_mod, "work_root", lambda: tmp_path / "work")
    lock, running, peak = threading.Lock(), [0], [0]
    release = threading.Event()

    def blocking(t, workdir, s, cb, cancel):
        with lock:
            running[0] += 1
            peak[0] = max(peak[0], running[0])
        release.wait(5)
        with lock:
            running[0] -= 1
        raise AppError("PRIVATE", "x")

    monkeypatch.setattr(pipeline_mod, "download_audio", blocking)
    finished = []
    qm = QueueManager(settings, History(tmp_path / "h.json"), ArtistMemory(tmp_path / "m.json"),
                      lambda x: finished.append(x.uid) if x.status.finished else None)
    tracks = [make_track(video_id=f"vid{i:08d}") for i in range(5)]
    for t in tracks:
        qm.enqueue(t)
    time.sleep(0.5)
    qm.cancel(tracks[4])  # 아직 대기열에 있는 곡
    assert tracks[4].status == Status.CANCELLED
    release.set()
    deadline = time.time() + 10
    while len(set(finished)) < 5 and time.time() < deadline:
        time.sleep(0.05)
    qm.shutdown()
    assert peak[0] <= 2
    assert len(set(finished)) == 5


def test_same_track_in_other_format(tmp_path, monkeypatch, source, cover_bytes):
    """목록의 한 곡을 mp3 로 받은 뒤 같은 줄에서 wav 로도 받는다 (같은 형식은 다시 받지 않음)."""
    t = make_track()
    run_track(tmp_path, monkeypatch, source, cover_bytes, t, format="mp3", on_exists="skip")
    assert t.status == Status.DONE and t.formats == {"mp3"} and t.formats_text() == "mp3"
    assert not t.needs("mp3") and t.needs("wav")

    _, history, *_ = run_track(tmp_path, monkeypatch, source, cover_bytes, t, format="wav", on_exists="skip")
    assert t.status == Status.DONE, t.message
    assert t.formats_text() == "mp3 wav" and not t.needs("wav")
    assert (tmp_path / "out" / "IU - Blueming.mp3").is_file()
    assert (tmp_path / "out" / "IU - Blueming.wav").is_file()
    assert history.formats("abcdefghijk") == {"mp3", "wav"}


def test_needs():
    t = make_track()
    assert t.needs("mp3") and t.needs("wav") and t.formats_text() == ""
    t.formats = {"wav", "mp3"}
    assert t.formats_text() == "mp3 wav" and not t.needs("mp3")
    t.formats = set()
    t.status = Status.DOWNLOADING
    assert not t.needs("mp3")  # 진행 중인 곡은 다시 넣지 않는다
