import core.metadata as metadata_mod
from core.metadata import (
    apply_guess,
    clean_channel,
    clean_title,
    enrich_with_musicbrainz,
    guess_from_info,
    parse_provided_by,
    pick_release,
    split_artist_title,
)
from core.models import Track


class FakeMemory:
    def __init__(self, data):
        self.data = data

    def lookup(self, channel):
        return self.data.get(channel, "")


def test_clean_title_removes_noise_but_keeps_meaning():
    assert clean_title("IU - Blueming (Official Video)") == "IU - Blueming"
    assert clean_title("[MV] 아이유(IU) - 라일락") == "아이유(IU) - 라일락"
    assert clean_title("Song 【Official Audio】 [4K]") == "Song"
    assert clean_title("Song (feat. Someone) [Lyrics]") == "Song (feat. Someone)"
    assert clean_title("Song (Live at Budokan)") == "Song (Live at Budokan)"
    assert clean_title("Song (Remix)") == "Song (Remix)"
    assert clean_title("Blueming | Official Video") == "Blueming"
    assert clean_title("Dynamite Official MV") == "Dynamite"


def test_clean_channel():
    assert clean_channel("IU - Topic") == "IU"
    assert clean_channel("BTSVEVO") == "BTS"
    assert clean_channel("HYBE LABELS Official") == "HYBE LABELS"


def test_split_artist_title():
    assert split_artist_title("IU - Blueming (Official Video)") == ("IU", "Blueming")
    assert split_artist_title("아이유 – 좋은 날") == ("아이유", "좋은 날")
    assert split_artist_title("BTS (방탄소년단) 'Dynamite' Official MV") == ("BTS (방탄소년단)", "Dynamite")
    assert split_artist_title("그냥 제목만 있는 영상") == ("", "그냥 제목만 있는 영상")
    # 제목 - 가수 순서인데 채널이 가수인 경우 뒤집기
    assert split_artist_title("Blueming - IU", channel="IU") == ("IU", "Blueming")
    # 제목에 ' - ' 가 더 있어도 첫 구분자 기준
    assert split_artist_title("A - B - C") == ("A", "B - C")


def test_guess_prefers_fields():
    info = {"title": "whatever", "track": "Blueming", "artists": ["IU"], "album": "Love poem",
            "release_year": 2019, "channel": "IU - Topic"}
    g = guess_from_info(info)
    assert (g.title, g.artist, g.album, g.year, g.artist_source) == ("Blueming", "IU", "Love poem", "2019", "field")


def test_guess_from_topic_description():
    desc = "Provided to YouTube by Kakao Entertainment\n\nBlueming · IU\n\nLove poem\n\n℗ 2019 EDAM\n"
    parsed = parse_provided_by(desc)
    assert parsed == {"track": "Blueming", "artists": ["IU"], "album": "Love poem"}
    g = guess_from_info({"title": "Blueming", "description": desc, "channel": "IU - Topic"})
    assert (g.title, g.artist, g.album) == ("Blueming", "IU", "Love poem")


def test_guess_from_title_then_channel_then_memory():
    g = guess_from_info({"title": "IU - Blueming [MV]", "channel": "1theK"})
    assert (g.artist, g.title, g.artist_source) == ("IU", "Blueming", "title")

    g = guess_from_info({"title": "라일락", "channel": "아이유 - Topic"})
    assert (g.artist, g.artist_source) == ("아이유", "channel")

    g = guess_from_info({"title": "라일락", "channel": "이지금 [IU Official]"}, memory=FakeMemory({"이지금 [IU Official]": "아이유"}))
    assert (g.artist, g.artist_source) == ("아이유", "memory")

    # 제목에서 가수를 찾았으면 기억된 값으로 덮어쓰지 않는다 (레이블 채널 오염 방지)
    g = guess_from_info({"title": "IU - Blueming", "channel": "1theK"}, memory=FakeMemory({"1theK": "Wrong"}))
    assert g.artist == "IU"


def test_playlist_title_becomes_album():
    g = guess_from_info({"title": "IU - Blueming", "channel": "x"}, playlist_title="Album - Love poem")
    assert g.album == "Love poem"


def test_apply_guess_keeps_user_edits():
    t = Track(video_id="abc", url="u", title="내가 고친 제목", artist="내가 고친 가수")
    t.edited = {"title", "artist"}
    apply_guess(t, guess_from_info({"title": "IU - Blueming", "channel": "x"}))
    assert (t.title, t.artist) == ("내가 고친 제목", "내가 고친 가수")


def test_pick_release_prefers_official_album_then_earliest():
    rec = {"releases": [
        {"title": "Compilation", "status": "Official", "date": "2021-01-01",
         "release-group": {"primary-type": "Album", "secondary-types": ["Compilation"]}},
        {"title": "Single", "status": "Official", "date": "2019-03-01", "release-group": {"primary-type": "Single"}},
        {"title": "Real Album", "status": "Official", "date": "2019-11-18", "release-group": {"primary-type": "Album"}},
    ]}
    assert pick_release(rec)["title"] == "Real Album"
    assert pick_release({"releases": []}) is None


def test_genre_from_info():
    g = guess_from_info({"title": "IU - Blueming", "channel": "x", "genres": ["K-Pop"]})
    assert g.genre == "K-Pop"
    assert guess_from_info({"title": "IU - Blueming", "channel": "x"}).genre == ""


def test_musicbrainz_fills_track_and_genre(monkeypatch):
    data = {"recordings": [{
        "score": 100, "title": "Blueming",
        "tags": [{"name": "k-pop", "count": 3}, {"name": "ballad", "count": 1}],
        "releases": [{"title": "Love poem", "status": "Official", "date": "2019-11-18",
                      "release-group": {"primary-type": "Album"},
                      "media": [{"track": [{"number": "3", "title": "Blueming"}]}]}],
    }]}
    monkeypatch.setattr(metadata_mod, "_mb_get", lambda url, timeout: data)
    t = Track(video_id="x", url="u", title="Blueming", artist="IU")
    assert enrich_with_musicbrainz(t)
    assert (t.album, t.year, t.track_no, t.genre) == ("Love poem", "2019", 3, "k-pop")

    # 다른 앨범이 이미 있으면 트랙 번호는 넣지 않는다, 못 찾은 값은 비워 둔다
    data["recordings"][0]["tags"] = []
    t2 = Track(video_id="x", url="u", title="Blueming", artist="IU", album="Other")
    enrich_with_musicbrainz(t2)
    assert (t2.album, t2.track_no, t2.genre) == ("Other", None, "")
