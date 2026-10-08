"""배포판 자동 업데이트: 버전 비교, 릴리스 응답 해석, 교체 스크립트 실제 실행."""
import shutil
import subprocess
import sys

import pytest

from utils import app_update as au


def test_is_newer():
    assert au.is_newer("20261001_0900", "20261008_1530")
    assert not au.is_newer("20261008_1530", "20261008_1530")
    assert not au.is_newer("20261008_1530", "20261001_0900")
    assert not au.is_newer("", "20261008_1530")  # 소스 실행 (빌드 시각 없음)
    assert not au.is_newer("20261001_0900", "v1.0")


def test_parse_release():
    data = {
        "tag_name": "v20261008_1530",
        "html_url": "https://github.com/x/y/releases/tag/v20261008_1530",
        "body": "가사 기능",
        "assets": [
            {"name": "notes.txt", "browser_download_url": "https://e/notes.txt"},
            {"name": "YouTubeAudio_20261008_1530.zip", "browser_download_url": "https://e/a.zip"},
        ],
    }
    rel = au.parse_release(data)
    assert rel.stamp == "20261008_1530" and rel.zip_url == "https://e/a.zip" and rel.notes == "가사 기능"
    assert au.parse_release({"tag_name": "v20261008_1530", "assets": []}) is None  # zip 없음
    assert au.parse_release({"tag_name": "latest", "assets": data["assets"]}) is None  # 태그 형식 틀림
    assert au.parse_release(None) is None


def test_update_available_only_when_frozen(monkeypatch):
    monkeypatch.setattr(au, "check_latest", lambda: au.Release("20991231_2359", "https://e/a.zip"))
    monkeypatch.setattr(au, "is_frozen", lambda: False)
    monkeypatch.setattr(au, "build_stamp", lambda: "")
    assert au.update_available() is None  # 소스로 실행 중이면 확인하지 않는다
    monkeypatch.setattr(au, "is_frozen", lambda: True)
    monkeypatch.setattr(au, "build_stamp", lambda: "20261001_0900")
    assert au.update_available().stamp == "20991231_2359"
    monkeypatch.setattr(au, "build_stamp", lambda: "20991231_2359")
    assert au.update_available() is None


def _fake_app(folder, version):
    (folder / "_internal" / "sub").mkdir(parents=True)
    (folder / "_internal" / "sub" / "lib.txt").write_text(version, encoding="utf-8")
    (folder / "_internal" / f"only_{version}.txt").write_text(version, encoding="utf-8")
    (folder / "YouTubeAudio.exe").write_text(version, encoding="utf-8")


@pytest.mark.skipif(sys.platform != "win32" or not shutil.which("powershell"), reason="Windows PowerShell 필요")
def test_install_script_replaces_only_program_files(tmp_path):
    dst = tmp_path / "프로그램 [폴더]"  # 한글·공백·대괄호 경로
    _fake_app(dst, "old")
    (dst / "추출사운드").mkdir()
    (dst / "추출사운드" / "곡.mp3").write_text("음악", encoding="utf-8")
    work = tmp_path / "work"
    src = work / "new" / "YouTubeAudio"
    _fake_app(src, "new")

    cmd = au.install_command(src, work, 0, dst, "YouTubeAudio.exe")  # update.ps1 도 이때 만들어진다
    # 테스트에서는 가짜 exe 를 실행하지 않도록 Start-Process 줄만 끈다
    script = work / "update.ps1"
    text = script.read_text(encoding="utf-8-sig")
    assert "if (Test-Path -LiteralPath $exe) { Start-Process" in text
    script.write_text(text.replace("if (Test-Path -LiteralPath $exe) { Start-Process", "if ($false) { Start-Process"),
                      encoding="utf-8-sig")
    proc = subprocess.run(cmd, timeout=60, capture_output=True)
    assert proc.returncode == 0, proc.stderr.decode("cp949", "replace")

    assert (dst / "YouTubeAudio.exe").read_text(encoding="utf-8") == "new"
    assert (dst / "_internal" / "sub" / "lib.txt").read_text(encoding="utf-8") == "new"
    assert (dst / "_internal" / "only_new.txt").exists()
    assert not (dst / "_internal" / "only_old.txt").exists()  # 옛 파일은 남지 않는다
    assert not (dst / "_internal.old").exists() and not (dst / "YouTubeAudio.exe.old").exists()
    assert (dst / "추출사운드" / "곡.mp3").read_text(encoding="utf-8") == "음악"  # 받은 음악은 그대로
    assert not work.exists()  # 임시 파일 정리
