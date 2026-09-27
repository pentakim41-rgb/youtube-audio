# 유튜브 → MP3/WAV 변환기

유튜브 링크를 하나씩 계속 추가해 목록을 만들고, **목록의 곡을 한 번에 전부** MP3 또는 WAV 로 받는 데스크톱 프로그램입니다.
가수·앨범·제목·트랙번호·연도·커버 이미지가 태그로 들어가고, `가수/앨범/가수 - 제목.mp3` 로 정리되어 저장됩니다.

> 본인이 올린 영상이나 재사용이 허용된 콘텐츠에만 사용하세요. 유튜브 약관상 다운로드는 허용되지 않으며, 저작권이 있는 콘텐츠는 개인 소장용이라도 문제가 될 수 있습니다.

## 실행 (Windows)

1. Python 3.10+ 설치 (설치 시 "Add python.exe to PATH" 체크)
2. `run.bat` 더블클릭 — 처음에는 가상환경을 만들고 패키지를 자동 설치합니다.
3. **Deno 설치 (중요)**: 최신 yt-dlp 는 유튜브 추출에 JavaScript 런타임이 필요합니다.
   `winget install DenoLand.Deno` 후 프로그램을 다시 실행하세요. (시작할 때 없으면 경고가 뜹니다)
4. ffmpeg 는 `imageio-ffmpeg` 패키지가 자동으로 제공합니다. 직접 설치한 ffmpeg 를 쓰려면 설정에서 경로를 지정하세요.

직접 실행: `pip install -r requirements.txt` → `python main.py`

## 사용법

1. 상단 입력칸에 유튜브 링크를 붙여넣고 **Enter** 또는 **리스트에 추가** (여러 링크를 한꺼번에 붙여넣어도 됩니다)
2. 목록에 곡이 쌓입니다. 클릭으로 선택(Ctrl/Shift 로 여러 개), **Delete 키/선택 삭제**로 삭제
3. 더블클릭(또는 선택 편집)으로 가수/앨범/제목/트랙번호/연도 수정 — 여러 곡을 선택하면 앨범·가수를 한 번에 적용
4. **전체 다운로드** — 목록의 모든 곡이 동시 2곡씩(설정에서 변경) 처리됩니다
- 재생목록 링크는 전체를 추가하며, 재생목록 제목이 앨범명, 순서가 트랙번호가 됩니다.
- `watch?v=...&list=...` 링크는 "전체 / 이 영상만" 을 물어봅니다.
- "추가하면 바로 다운로드"를 켜면 추가 즉시 시작합니다.

## 가수·앨범 이름 결정 순서

1. YouTube Music / "- Topic" 채널의 `artist`, `album`, `track` 필드 (+ 설명란 "Provided to YouTube by")
2. 제목 파싱: `가수 - 제목`, `가수 '제목' MV` (`[MV]`, `(Official Video)` 등 잡음 제거, `(feat.)`, `(Live)`, `(Remix)` 는 유지)
3. 채널명 (`- Topic`, VEVO, Official 제거) — 직접 고친 가수 이름은 채널별로 기억
4. MusicBrainz 검색으로 앨범/연도 보완 (단일 영상, 설정에서 끌 수 있음)
5. 사용자가 편집 화면에서 최종 수정

## 태그와 WAV 관련

- MP3: ID3v2.3(UTF-16) — 제목/가수/앨범/앨범아티스트/트랙/연도/커버 (한글이 깨지지 않는 방식)
- WAV: 태그 지원이 약해 플레이어에 따라 안 보일 수 있습니다. 그래서 파일명·폴더 구조로도 가수/앨범이 드러나고,
  커버는 기본으로 넣지 않습니다 (설정에서 WAV 안에 삽입할 수 있지만 일부 플레이어와 호환성이 낮습니다).
- 유튜브 원본이 손실 압축(opus/m4a)이라 WAV 로 바꿔도 음질이 좋아지지는 않고 용량만 커집니다 (1시간 ≈ 600MB).

## 오류 대응

| 증상 | 대응 |
|---|---|
| 연령 제한 / "봇 확인" | 설정 → 쿠키 가져올 브라우저(로그인된 Firefox 권장) 또는 cookies.txt 지정 |
| 갑자기 모두 실패 / "추출 실패" | 설정 → yt-dlp 업데이트, Deno 설치 확인 |
| 429 / 403 | 자동으로 간격을 늘려 재시도(5→15→45초). 동시 다운로드 수를 1~2로 낮추세요 |
| 비공개·삭제·지역제한·라이브 | 사유를 표시하고 나머지 곡은 계속 진행 |

로그: `%APPDATA%\YouTubeAudio\logs\app.log` (설정·기록도 같은 폴더)

## 코드 구조 (Cursor 에서 수정할 때)

```
main.py                 진입점
config.py               설정 (JSON 저장)
core/
  fetcher.py            링크 분석·정보 조회 (다운로드 X)   ← 링크 종류/재생목록 처리
  metadata.py           가수/앨범/제목 추정, MusicBrainz     ← 제목 파싱 규칙은 여기
  downloader.py         yt-dlp 오디오 다운로드
  converter.py          ffmpeg 변환 (mp3/wav, 진행률, 취소)
  tagger.py             mutagen 태그·커버 삽입
  cover.py              썸네일 → 정사각형 커버
  namer.py              파일명/폴더 정리, 중복 이름
  pipeline.py           한 곡 처리 순서 (다운로드→변환→태그→저장)
  queue_manager.py      동시 실행, 취소, 재시도
  history.py            중복 방지 기록, 채널→가수 기억
  errors.py             오류 분류·사용자 메시지
  ytdlp_opts.py         yt-dlp 공통 옵션 (쿠키, JS 런타임)
ui/
  main_window.py        링크 입력 + 목록 창 + 전체 다운로드
  edit_dialog.py        태그 편집 창
  settings_dialog.py    설정 창
utils/                  paths(ffmpeg 탐색), logger, updater(yt-dlp 버전/환경 점검)
tests/                  pytest (실제 ffmpeg 로 변환·태그까지 검증), gui_smoke.py(GUI 흐름 점검)
```

테스트: `pytest -q tests`

## exe 만들기

`build.bat` 를 실행하면 (PyInstaller, 빌드용 가상환경 `.venv-build` 자동 생성)

- `dist\YouTubeAudio_날짜_시간.zip` — 배포용 (결과물은 이 파일 하나, 예: `YouTubeAudio_20260927_2039.zip`).
  같은 "날짜_시간"이 프로그램 오른쪽 위에도 표시됩니다. 받는 사람은 압축을 풀고 `YouTubeAudio.exe` 를 실행하면 됩니다.
  (exe 는 같은 폴더의 `_internal` 이 있어야 실행됨)

Python, ffmpeg, Deno(유튜브 추출용 JavaScript 런타임)를 모두 포함하므로 받는 PC 에 따로 설치할 것이 없습니다.
`bin\deno.exe` 가 없으면 빌드 때 자동으로 내려받습니다. 저장 폴더는 기본으로 exe 옆의 `추출사운드` 입니다.

exe 판은 yt-dlp 자동 업데이트가 안 되므로, 유튜브 쪽이 바뀌어 다운로드가 안 되면 `build.bat` 로 다시 빌드해 배포하세요.
