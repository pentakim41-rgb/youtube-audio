import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest


@pytest.fixture(autouse=True)
def _no_lyrics_network(monkeypatch):
    """테스트 중에는 가사 사이트(LRCLIB)에 접속하지 않는다."""
    from core import pipeline
    monkeypatch.setattr(pipeline, "fetch_lyrics", lambda *a, **k: None)
