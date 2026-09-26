# 휴대폰(안드로이드)에서 실행하기

PC 없이 휴대폰 안에서 프로그램이 직접 돌아갑니다.
**Termux**(휴대폰용 리눅스 앱)에서 프로그램을 켜고, 화면은 휴대폰 브라우저로 봅니다.
받은 곡은 휴대폰 **Music/추출사운드** 폴더에 바로 저장되어 음악 앱에서 들을 수 있습니다.

## 1. 설치 (처음 한 번, 10~20분)

1. **Termux 설치** — Play 스토어 판은 오래되어 동작하지 않습니다. 아래 둘 중 한 곳에서 받으세요.
   - F-Droid: https://f-droid.org/packages/com.termux/
   - GitHub: https://github.com/termux/termux-app/releases (`arm64-v8a` apk)
2. Termux 를 열고 아래를 한 줄씩 입력:
   ```
   pkg install -y git
   git clone https://github.com/pentakim41-rgb/youtube-audio.git ~/youtube_audio
   bash ~/youtube_audio/android/install.sh
   ```
3. 중간에 "저장소 접근 허용" 창이 뜨면 **허용**을 누르세요.

## 2. 사용

- Termux 에서 **`yta`** 입력 → 브라우저에 화면이 열립니다.
- 또는 **유튜브 앱 → 공유 → Termux** → 목록에 바로 추가되고 화면이 열립니다. (재생목록도 가능)
- 링크 추가 → **전체 다운로드** → 완료되면 Music/추출사운드 에 저장됩니다.
- 브라우저 메뉴의 **홈 화면에 추가**를 하면 앱처럼 쓸 수 있습니다. (Termux 에서 `yta` 로 켜 둔 상태여야 열립니다)

| 명령 | 하는 일 |
|---|---|
| `yta` | 켜기 + 화면 열기 |
| `yta stop` | 끄기 (다 받았으면 꺼 두면 배터리 절약) |
| `yta update` | 프로그램 + yt-dlp 업데이트 — **갑자기 다운로드가 안 되면 먼저 이것** |
| `yta log` | 오류 기록 보기 |

## 3. 꼭 해 둘 설정

- **배터리 최적화 해제**: 설정 → 애플리케이션 → Termux → 배터리 → **제한 없음**.
  안 하면 화면을 끄거나 다른 앱을 쓸 때 다운로드가 멈출 수 있습니다.
- Termux 알림창에 "wake lock held" 가 보이면 백그라운드에서 계속 동작 중인 것입니다.

## 선택 사항

- **홈 화면 위젯**: F-Droid 에서 *Termux:Widget* 설치 → 홈 화면에 위젯 추가 → "유튜브음악" 누르면 바로 실행
- **음악 앱에 바로 보이게**: 새 곡이 음악 앱에 늦게 나타나면 F-Droid 에서 *Termux:API* 앱 설치 후 Termux 에서 `pkg install termux-api`
  (Termux 와 Termux:API 는 같은 곳(F-Droid 또는 GitHub)에서 받아야 합니다)

## 문제 해결

| 증상 | 대응 |
|---|---|
| 모두 실패 / "추출 실패" / "봇 확인" | `yta update` 후 다시 시도. 모바일 데이터 ↔ 와이파이를 바꿔 보기 |
| 연령 제한 영상 | 설정 → cookies.txt 경로 지정 (유튜브에 로그인된 브라우저에서 내보낸 파일) |
| 곡이 음악 앱에 안 보임 | 파일 앱에서 Music/추출사운드 확인. 위 Termux:API 설치 |
| 화면이 안 열림 | Termux 에서 `yta restart`, 그래도 안 되면 `yta log` 내용 확인 |

- 저장 폴더는 설정에서 바꿀 수 있습니다. (예: `/storage/emulated/0/Download`)
- 설정·기록은 데스크톱 판과 형식이 같고 휴대폰 안(`~/.config/YouTubeAudio`)에 따로 저장됩니다.
- 이 휴대폰에서만 접속됩니다. 같은 와이파이의 다른 기기에서도 쓰려면 `yta` 대신 `cd ~/youtube_audio && python -m web.server --lan` (PIN 필요).

## 파일

```
android/install.sh   처음 설치
android/yta.sh       실행/종료/업데이트 ('yta' 명령)
web/server.py        서버 (core/ 를 그대로 사용, 파이썬 내장 http.server)
web/static/          화면 (index.html, app.js, app.css)
```
