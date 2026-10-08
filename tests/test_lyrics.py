"""가사: 시간 붙이기/빼기, 파일 안(USLT) + .lrc 저장, 고친 가사의 시간 유지."""
import subprocess

import pytest
from mutagen.id3 import ID3

from core import lyrics as lyr
from core.lyrics import build_lrc, parse_lrc, read_lyrics, save_lyrics, strip_timestamps
from utils.paths import find_ffmpeg

SYNCED = "[ar:IU]\n[00:10.00]첫 줄\n[00:15.50]둘째 줄\n[00:20.00][01:00.00]후렴\n[00:30.00]넷째 줄\n"


def test_parse_and_strip():
    entries = parse_lrc(SYNCED)
    assert entries[0] == (10.0, "첫 줄")
    assert [t for _, t in entries] == ["첫 줄", "둘째 줄", "후렴", "넷째 줄", "후렴"]
    assert strip_timestamps(SYNCED) == "첫 줄\n둘째 줄\n후렴\n넷째 줄\n후렴"
    assert strip_timestamps("그냥 가사\n둘째") == "그냥 가사\n둘째"


def test_build_lrc_keeps_old_times_and_fills_inserted():
    old = parse_lrc("[00:10.00]a\n[00:20.00]b\n[00:30.00]c\n")
    out = parse_lrc(build_lrc(["a", "b 고침", "새 줄", "c"], old, 60))
    assert out[0] == (10.0, "a")
    assert out[1] == (20.0, "b 고침")  # 고친 줄은 예전 시간
    assert out[2] == (25.0, "새 줄")  # 끼운 줄은 앞뒤 사이
    assert out[3] == (30.0, "c")


def test_build_lrc_without_times_spreads_over_duration():
    out = parse_lrc(build_lrc(["a", "b", "c"], [], 40))
    times = [t for t, _ in out]
    assert times == sorted(times) and times[0] == 0.0 and times[-1] < 40


@pytest.fixture
def mp3(tmp_path):
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        pytest.skip("ffmpeg 없음")
    path = tmp_path / "곡.mp3"
    subprocess.run([ffmpeg, "-y", "-f", "lavfi", "-i", "sine=duration=3", str(path)], check=True, capture_output=True)
    return path


def test_save_read_and_edit(mp3):
    save_lyrics(mp3, strip_timestamps(SYNCED), SYNCED)
    lrc = mp3.with_suffix(".lrc")
    assert lrc.read_bytes().startswith(b"\xef\xbb\xbf")  # UTF-8 BOM
    assert "[00:15.50]둘째 줄" in lrc.read_text(encoding="utf-8-sig")
    uslt = ID3(str(mp3)).getall("USLT")[0].text
    assert "[" not in uslt and uslt.startswith("첫 줄")  # 파일 안에는 순수 가사만
    assert read_lyrics(mp3) == "첫 줄\n둘째 줄\n후렴\n넷째 줄\n후렴"

    # 화면에서 고친 뒤 '다시저장' → 시간은 그대로, 글만 바뀐다
    save_lyrics(mp3, "첫 줄\n둘째 줄 (고침)\n후렴\n넷째 줄\n후렴")
    assert "[00:15.50]둘째 줄 (고침)" in lrc.read_text(encoding="utf-8-sig")
    assert read_lyrics(mp3).splitlines()[1] == "둘째 줄 (고침)"
    assert ID3(str(mp3)).getall("USLT")[0].text.splitlines()[1] == "둘째 줄 (고침)"

    # 다 지우고 저장 → 가사 없음
    save_lyrics(mp3, "")
    assert not lrc.exists() and not ID3(str(mp3)).getall("USLT")
    assert read_lyrics(mp3) == ""


def test_fetch_prefers_synced_and_close_duration(monkeypatch):
    def fake_get(path, params, timeout):
        if path == "get":
            return None
        return [
            {"trackName": "Blueming", "duration": 400, "syncedLyrics": "[00:01.00]라이브", "plainLyrics": "라이브"},
            {"trackName": "Blueming", "duration": 201, "syncedLyrics": None, "plainLyrics": "글만"},
            {"trackName": "Blueming (블루밍)", "duration": 199, "syncedLyrics": "[00:01.00]정답", "plainLyrics": "정답"},
        ]

    monkeypatch.setattr(lyr, "_get", fake_get)
    found = lyr.fetch_lyrics("IU", "Blueming", 200)
    assert found.synced == "[00:01.00]정답"



def test_fetch_skips_other_song_of_same_artist(monkeypatch):
    monkeypatch.setattr(lyr, "_get", lambda path, params, timeout: None if path == "get" else
                        [{"trackName": "Love wins all", "duration": 200, "syncedLyrics": "[00:01.00]x"}])
    assert lyr.fetch_lyrics("IU", "Blueming", 200) is None
