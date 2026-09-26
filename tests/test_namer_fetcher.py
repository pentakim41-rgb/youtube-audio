from pathlib import Path

import pytest

from config import Settings
from core import namer
from core.errors import classify
from core.fetcher import analyze_url, extract_urls
from core.models import Track


def make_settings(tmp_path, **kw):
    s = Settings(output_dir=str(tmp_path), **kw)
    s.normalize()
    return s


# ---------------- namer ----------------
def test_sanitize_windows_rules():
    assert namer.sanitize('a<b>c:d"e/f\\g|h?i*j') == "a(b)c -d'e-f-g-hij"
    assert namer.sanitize("CON") == "_CON"
    assert namer.sanitize("lpt1.txt") == "_lpt1.txt"
    assert namer.sanitize("끝에 점...") == "끝에 점"
    assert namer.sanitize("   ") == "Unknown"
    assert namer.sanitize("🔥Fire🔥 노래 ❤️") == "Fire 노래"
    assert namer.sanitize("🔥Fire", strip_emoji=False) == "🔥Fire"
    assert len(namer.sanitize("가" * 500)) <= namer.MAX_COMPONENT


def test_output_path_saved_directly_in_output_dir(tmp_path):
    t = Track(video_id="x", url="u", title="Blueming", artist="IU", album="Love poem", track_no=3)
    assert namer.build_output_path(t, make_settings(tmp_path)) == tmp_path / "IU - Blueming.mp3"
    assert namer.build_output_path(t, make_settings(tmp_path, format="wav")) == tmp_path / "IU - Blueming.wav"
    assert namer.build_output_path(t, make_settings(tmp_path, filename_style="track_title")) == tmp_path / "03 - Blueming.mp3"
    no_artist = Track(video_id="x", url="u", title="Only")
    assert namer.build_output_path(no_artist, make_settings(tmp_path)) == tmp_path / "Only.mp3"


def test_default_saves_without_subfolders(tmp_path):
    s = Settings(output_dir=str(tmp_path))
    s.normalize()
    t = Track(video_id="x", url="u", title="Blueming", artist="IU", album="Love poem")
    assert namer.build_output_path(t, s) == tmp_path / "IU - Blueming.mp3"


def test_long_path_is_shortened(tmp_path):
    s = make_settings(tmp_path)
    t = Track(video_id="x", url="u", title="가" * 300, artist="나" * 300, album="다" * 300)
    p = namer.build_output_path(t, s)
    assert p.parent == tmp_path
    assert len(str(p)) <= namer.MAX_TOTAL_PATH


def test_unique_path(tmp_path):
    f = tmp_path / "a.mp3"
    f.write_bytes(b"1")
    assert namer.unique_path(f) == tmp_path / "a (2).mp3"
    (tmp_path / "a (2).mp3").write_bytes(b"1")
    assert namer.unique_path(f) == tmp_path / "a (3).mp3"


# ---------------- url ----------------
@pytest.mark.parametrize(
    "url,kind,vid,pid",
    [
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "video", "dQw4w9WgXcQ", ""),
        ("youtube.com/watch?v=dQw4w9WgXcQ&t=30s", "video", "dQw4w9WgXcQ", ""),
        ("https://youtu.be/dQw4w9WgXcQ?si=abc", "video", "dQw4w9WgXcQ", ""),
        ("https://music.youtube.com/watch?v=dQw4w9WgXcQ", "video", "dQw4w9WgXcQ", ""),
        ("https://www.youtube.com/shorts/dQw4w9WgXcQ", "video", "dQw4w9WgXcQ", ""),
        ("https://www.youtube.com/playlist?list=PLabc123", "playlist", "", "PLabc123"),
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PLabc123", "video_in_playlist", "dQw4w9WgXcQ", "PLabc123"),
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=RDdQw4w9WgXcQ", "video", "dQw4w9WgXcQ", ""),
    ],
)
def test_analyze_url_ok(url, kind, vid, pid):
    info = analyze_url(url)
    assert (info.kind, info.video_id, info.playlist_id) == (kind, vid, pid)


@pytest.mark.parametrize(
    "url",
    ["", "hello", "https://example.com/watch?v=dQw4w9WgXcQ", "https://www.youtube.com/",
     "https://www.youtube.com/watch?v=short", "https://www.youtube.com/@channel"],
)
def test_analyze_url_invalid(url):
    info = analyze_url(url)
    assert info.kind == "invalid" and info.reason


def test_extract_urls_dedup():
    text = "https://youtu.be/aaaaaaaaaaa\n https://youtu.be/bbbbbbbbbbb, https://youtu.be/aaaaaaaaaaa"
    assert extract_urls(text) == ["https://youtu.be/aaaaaaaaaaa", "https://youtu.be/bbbbbbbbbbb"]


# ---------------- errors ----------------
@pytest.mark.parametrize(
    "msg,code,retry",
    [
        ("ERROR: [youtube] x: Private video. Sign in if you've been granted access", "PRIVATE", False),
        ("ERROR: [youtube] x: Sign in to confirm your age. This video may be inappropriate for some users.", "AGE", False),
        ("ERROR: [youtube] x: Sign in to confirm you’re not a bot", "BOT", False),
        ("ERROR: [youtube] x: Video unavailable", "UNAVAILABLE", False),
        ("ERROR: The uploader has not made this video available in your country", "GEO", False),
        ("ERROR: [youtube] x: This live event will begin in 2 hours.", "LIVE", False),
        ("HTTP Error 429: Too Many Requests", "RATE", True),
        ("Requested format is not available", "EXTRACT", False),
        ("<urlopen error [Errno -3] Temporary failure in name resolution>", "NETWORK", True),
        ("something weird", "UNKNOWN", True),
    ],
)
def test_classify(msg, code, retry):
    e = classify(Exception(msg))
    assert (e.code, e.retryable) == (code, retry)
    assert e.message


def test_classify_disk_full():
    e = classify(OSError(28, "No space left on device"))
    assert e.code == "DISK"
