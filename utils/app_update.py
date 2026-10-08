"""배포판(exe) 자동 업데이트.

깃허브 Releases 에 새 zip 을 올리면, 배포판을 켤 때 확인해서 받아 바꾸고 다시 실행한다.
  - 버전 = 빌드 시각 ("20261008_1530"). 릴리스 태그는 "v20261008_1530", 첨부 파일은 zip (안에 YouTubeAudio/ 폴더).
  - 실행 중인 exe 는 스스로 덮어쓸 수 없으므로, 작은 PowerShell 스크립트가 프로그램이 꺼지길 기다렸다가
    exe 와 _internal 폴더만 바꾸고 다시 실행한다. 같은 폴더의 '추출사운드' 등 다른 파일은 건드리지 않는다.
소스(python main.py)로 실행할 때는 동작하지 않는다.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from utils.logger import get_logger
from utils.paths import app_dir, build_stamp, is_frozen

log = get_logger("app_update")

REPO = "pentakim41-rgb/youtube-audio"
API_LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases/latest"
_UA = {"User-Agent": "YouTubeAudio-updater", "Accept": "application/vnd.github+json"}
_STAMP = re.compile(r"^\d{8}_\d{4}$")


@dataclass
class Release:
    stamp: str  # "20261008_1530"
    zip_url: str
    page: str = RELEASES_PAGE
    notes: str = ""


def parse_release(data: dict) -> Release | None:
    """깃허브 'latest release' 응답 → Release. 형식이 맞지 않으면 None."""
    if not isinstance(data, dict):
        return None
    stamp = str(data.get("tag_name") or "").lstrip("vV")
    if not _STAMP.match(stamp):
        return None
    for asset in data.get("assets") or []:
        name = str(asset.get("name") or "")
        url = asset.get("browser_download_url") or ""
        if name.lower().endswith(".zip") and url:
            return Release(stamp, url, data.get("html_url") or RELEASES_PAGE, (data.get("body") or "").strip())
    return None


def is_newer(current: str, latest: str) -> bool:
    return bool(_STAMP.match(current or "") and _STAMP.match(latest or "") and latest > current)


def check_latest(timeout: float = 6.0) -> Release | None:
    try:
        req = urllib.request.Request(API_LATEST, headers=_UA)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return parse_release(json.loads(resp.read().decode("utf-8")))
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        log.info("업데이트 확인 실패: %s", exc)
        return None


def update_available() -> Release | None:
    """배포판이고 더 새 버전이 올라와 있으면 그 Release. 아니면 None."""
    current = build_stamp()
    if not is_frozen() or not current:
        return None
    latest = check_latest()
    if latest and is_newer(current, latest.stamp):
        return latest
    return None


def can_install(folder: Path | None = None) -> bool:
    """프로그램 폴더에 쓸 수 있는지 (Program Files 등은 관리자 권한이 필요해 자동 교체 불가)."""
    folder = folder or app_dir()
    try:
        fd, name = tempfile.mkstemp(dir=folder, prefix=".update_check_")
        os.close(fd)
        os.unlink(name)
        return True
    except OSError:
        return False


def download_and_extract(url: str, progress: Callable[[float], None] | None = None) -> tuple[Path, Path]:
    """zip 을 임시 폴더에 받아서 푼다. (새 프로그램 폴더, 임시 작업 폴더)."""
    work = Path(tempfile.mkdtemp(prefix="yt_audio_update_"))
    zpath = work / "update.zip"
    req = urllib.request.Request(url, headers={"User-Agent": _UA["User-Agent"]})
    with urllib.request.urlopen(req, timeout=30) as resp, open(zpath, "wb") as out:
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        while chunk := resp.read(256 * 1024):
            out.write(chunk)
            done += len(chunk)
            if progress and total:
                progress(done / total)
    with zipfile.ZipFile(zpath) as zf:
        zf.extractall(work / "new")
    zpath.unlink(missing_ok=True)
    exe_name = Path(sys.executable).name if is_frozen() else "YouTubeAudio.exe"
    for cand in (work / "new" / "YouTubeAudio", work / "new"):
        if (cand / exe_name).is_file() and (cand / "_internal").is_dir():
            return cand, work
    shutil.rmtree(work, ignore_errors=True)
    raise RuntimeError("받은 파일에 프로그램이 들어 있지 않습니다.")


# 실행 중인 프로그램이 꺼지길 기다렸다가 exe 와 _internal 만 바꾸고 다시 실행.
# 옛 파일은 먼저 이름만 바꿔 두고(.old), 복사가 실패하면 되돌린다.
_PS_SCRIPT = r"""param([int]$ProcId, [string]$Src, [string]$Dst, [string]$ExeName, [string]$Work)
$ErrorActionPreference = 'Stop'
$log = Join-Path $Work 'update.log'
function Log($m) { Add-Content -LiteralPath $log -Value ((Get-Date -Format s) + ' ' + $m) -Encoding UTF8 }
$exe = Join-Path $Dst $ExeName
$exeOld = "$exe.old"
$int = Join-Path $Dst '_internal'
$intOld = Join-Path $Dst '_internal.old'
try {
  if ($ProcId -gt 0) { Wait-Process -Id $ProcId -Timeout 60 -ErrorAction SilentlyContinue }
  if (Test-Path -LiteralPath $exeOld) { Remove-Item -LiteralPath $exeOld -Force }
  if (Test-Path -LiteralPath $intOld) { Remove-Item -LiteralPath $intOld -Recurse -Force }
  $moved = $false
  for ($i = 0; $i -lt 30 -and -not $moved; $i++) {
    try {
      if (Test-Path -LiteralPath $exe) { Rename-Item -LiteralPath $exe -NewName "$ExeName.old" }
      if (Test-Path -LiteralPath $int) { Rename-Item -LiteralPath $int -NewName '_internal.old' }
      $moved = $true
    } catch {
      if ((Test-Path -LiteralPath $exeOld) -and -not (Test-Path -LiteralPath $exe)) { Rename-Item -LiteralPath $exeOld -NewName $ExeName }
      Start-Sleep -Milliseconds 500
    }
  }
  if (-not $moved) { throw 'old files are still in use' }
  try {
    Copy-Item -LiteralPath (Join-Path $Src '_internal') -Destination $int -Recurse -Force
    Copy-Item -LiteralPath (Join-Path $Src $ExeName) -Destination $exe -Force
  } catch {
    Log ("copy failed, restoring: " + $_)
    if (Test-Path -LiteralPath $int) { Remove-Item -LiteralPath $int -Recurse -Force }
    if (Test-Path -LiteralPath $exe) { Remove-Item -LiteralPath $exe -Force }
    if (Test-Path -LiteralPath $intOld) { Rename-Item -LiteralPath $intOld -NewName '_internal' }
    if (Test-Path -LiteralPath $exeOld) { Rename-Item -LiteralPath $exeOld -NewName $ExeName }
    throw
  }
  Remove-Item -LiteralPath $intOld -Recurse -Force -ErrorAction SilentlyContinue
  Remove-Item -LiteralPath $exeOld -Force -ErrorAction SilentlyContinue
  Log 'updated'
  $ok = $true
} catch {
  Log ("update failed: " + $_)
  $ok = $false
}
if (Test-Path -LiteralPath $exe) { Start-Process -FilePath $exe -WorkingDirectory $Dst }
if ($ok) { Remove-Item -LiteralPath $Work -Recurse -Force -ErrorAction SilentlyContinue }
"""


def write_script(work: Path) -> Path:
    script = work / "update.ps1"
    script.write_text(_PS_SCRIPT, encoding="utf-8-sig")
    return script


def install_command(new_dir: Path, work: Path, pid: int, dst: Path, exe_name: str) -> list[str]:
    return [
        "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden",
        "-File", str(write_script(work)),
        "-ProcId", str(pid), "-Src", str(new_dir), "-Dst", str(dst), "-ExeName", exe_name, "-Work", str(work),
    ]


def install_and_restart(new_dir: Path, work: Path) -> None:
    """교체 스크립트를 띄운다. 호출한 뒤 프로그램은 바로 종료해야 한다."""
    cmd = install_command(new_dir, work, os.getpid(), app_dir(), Path(sys.executable).name)
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen(cmd, creationflags=flags, close_fds=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
