"""설정 창: 공통 / MP3 전용 / WAV 전용 / 다운로드·연결 구역으로 나눠 보여준다."""
from __future__ import annotations

import threading
from tkinter import filedialog, messagebox
from typing import Callable

import customtkinter as ctk

from config import (
    BROWSERS,
    FILENAME_STYLES,
    MP3_BITRATES,
    ON_EXISTS,
    WAV_BIT_DEPTHS,
    WAV_SAMPLE_RATES,
    Settings,
)
from utils import updater


def _inv(mapping: dict) -> dict:
    return {v: k for k, v in mapping.items()}


class SettingsDialog(ctk.CTkToplevel):
    def __init__(self, master, settings: Settings, on_save: Callable[[], None]):
        super().__init__(master)
        self.settings = settings
        self.on_save = on_save
        self.title("설정")
        self.geometry("580x760")
        self.transient(master)
        self._update_result: list = []

        self.scroll = ctk.CTkScrollableFrame(self)
        self.scroll.pack(fill="both", expand=True, padx=12, pady=(12, 0))
        self.body = self.scroll  # 헬퍼가 위젯을 넣을 현재 구역
        self.row = 0

        s = settings
        # ---- 공통 (mp3/wav 모두 적용) ----
        self._section("공통 설정", "MP3 와 WAV 모두에 적용됩니다.")
        self.v_format = self._menu("저장 포맷", ["mp3", "wav"], s.format, command=self._on_format)
        self.v_name = self._menu("파일 이름", list(FILENAME_STYLES.values()), FILENAME_STYLES[s.filename_style])
        self.v_exists = self._menu("같은 이름 파일이 있을 때", list(ON_EXISTS.values()), ON_EXISTS[s.on_exists])
        self.c_emoji = self._check("파일 이름에서 이모지 제거", s.strip_emoji)
        self.c_crop = self._check("커버를 정사각형으로 자르기", s.crop_cover_square)
        self.c_mb = self._check("MusicBrainz 로 앨범/연도/트랙/장르 보완 (단일 영상)", s.use_musicbrainz)
        self.c_lyr = self._check("가사 찾아서 함께 저장 (파일 안 + .lrc)", s.fetch_lyrics)

        # ---- MP3 전용 ----
        self.mp3_title = self._section("MP3 설정", "저장 포맷이 mp3 일 때만 적용됩니다. 커버는 항상 파일 안에 넣습니다.")
        self.v_bitrate = self._menu("음질 (kbps)", [str(b) for b in MP3_BITRATES], str(s.mp3_bitrate))

        # ---- WAV 전용 ----
        self.wav_title = self._section("WAV 설정", "저장 포맷이 wav 일 때만 적용됩니다.")
        self.v_sr = self._menu("샘플레이트 (Hz)", [str(x) for x in WAV_SAMPLE_RATES], str(s.wav_sample_rate))
        self.v_depth = self._menu("비트 깊이", [str(x) for x in WAV_BIT_DEPTHS], str(s.wav_bit_depth))
        self.c_wavcover = self._check("WAV 파일에 커버 삽입 (일부 플레이어 비호환)", s.wav_embed_cover)

        # ---- 다운로드 / 연결 ----
        self._section("다운로드 / 연결", "")
        self.v_conc = self._menu("동시 다운로드 수", ["1", "2", "3", "4"], str(s.max_concurrent))
        self.v_retries = self._menu("실패 시 재시도 횟수", [str(i) for i in range(0, 6)], str(s.retries))
        self.v_cookie = self._menu(
            "쿠키 가져올 브라우저", [b or "사용 안 함" for b in BROWSERS], s.cookies_browser or "사용 안 함"
        )
        self.e_cookiefile = self._entry("cookies.txt 파일 (선택)", s.cookies_file, browse=True)
        self.e_ffmpeg = self._entry("ffmpeg 경로 (비우면 자동)", s.ffmpeg_path, browse=True)

        ctk.CTkLabel(
            self.body,
            text="연령 제한/봇 확인 오류가 나면 위에서 로그인된 브라우저를 선택하세요. "
            "Chrome 은 실행 중이거나 최신 버전이면 실패할 수 있어 Firefox 를 권장합니다.",
            text_color="gray40", wraplength=480, justify="left",
        ).grid(row=self.row, column=0, columnspan=2, sticky="w", pady=(4, 8))
        self.row += 1

        self.c_updates = self._check("시작할 때 yt-dlp 업데이트 확인", s.check_updates)
        # yt-dlp 업데이트
        self.update_label = ctk.CTkLabel(self.body, text=f"yt-dlp 버전: {updater.installed_version() or '?'}", anchor="w")
        self.update_label.grid(row=self.row, column=0, sticky="w", pady=8)
        self.update_btn = ctk.CTkButton(self.body, text="yt-dlp 업데이트", width=140, command=self._update_ytdlp)
        self.update_btn.grid(row=self.row, column=1, sticky="e", pady=8)
        self.row += 1
        self._on_format(self.v_format.get())
        if not updater.can_self_update():
            self.update_btn.configure(state="disabled")

        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.pack(fill="x", padx=16, pady=12)
        ctk.CTkButton(bar, text="저장", width=100, command=self._save).pack(side="right")
        ctk.CTkButton(bar, text="취소", width=100, fg_color="gray60", hover_color="gray50", command=self.destroy).pack(
            side="right", padx=8
        )
        self.after(150, self._focus)

    # ---- 위젯 헬퍼 -------------------------------------------------------
    def _section(self, title: str, note: str) -> ctk.CTkLabel:
        """테두리 있는 구역을 만들고, 이후 헬퍼가 그 안에 위젯을 넣게 한다. 제목 라벨을 돌려준다."""
        frame = ctk.CTkFrame(self.scroll, border_width=1)
        frame.pack(fill="x", pady=(0, 12))
        frame.grid_columnconfigure(1, weight=1)
        header = ctk.CTkLabel(frame, text=title, font=ctk.CTkFont(size=15, weight="bold"), anchor="w")
        header.grid(row=0, column=0, columnspan=2, sticky="w", padx=12, pady=(10, 0))
        if note:
            ctk.CTkLabel(frame, text=note, text_color="gray40", anchor="w", wraplength=480, justify="left").grid(
                row=1, column=0, columnspan=2, sticky="w", padx=12
            )
        inner = ctk.CTkFrame(frame, fg_color="transparent")
        inner.grid(row=2, column=0, columnspan=2, sticky="ew", padx=12, pady=(4, 10))
        inner.grid_columnconfigure(1, weight=1)
        self.body, self.row = inner, 0
        return header

    def _on_format(self, fmt: str) -> None:
        """선택한 포맷의 구역 제목에 '사용 중' 표시."""
        self.mp3_title.configure(text="MP3 설정" + ("  ✔ 사용 중" if fmt == "mp3" else ""))
        self.wav_title.configure(text="WAV 설정" + ("  ✔ 사용 중" if fmt == "wav" else ""))

    def _menu(self, label: str, values: list[str], current: str, command=None) -> ctk.CTkOptionMenu:
        ctk.CTkLabel(self.body, text=label, anchor="w").grid(row=self.row, column=0, sticky="w", pady=6, padx=(0, 12))
        menu = ctk.CTkOptionMenu(self.body, values=values, width=220, command=command)
        menu.set(current if current in values else values[0])
        menu.grid(row=self.row, column=1, sticky="e", pady=6)
        self.row += 1
        return menu

    def _check(self, label: str, value: bool) -> ctk.CTkCheckBox:
        box = ctk.CTkCheckBox(self.body, text=label)
        if value:
            box.select()
        box.grid(row=self.row, column=0, columnspan=2, sticky="w", pady=5)
        self.row += 1
        return box

    def _entry(self, label: str, value: str, browse: bool = False) -> ctk.CTkEntry:
        ctk.CTkLabel(self.body, text=label, anchor="w").grid(row=self.row, column=0, sticky="w", pady=6)
        frame = ctk.CTkFrame(self.body, fg_color="transparent")
        frame.grid(row=self.row, column=1, sticky="ew", pady=6)
        frame.grid_columnconfigure(0, weight=1)
        entry = ctk.CTkEntry(frame)
        entry.insert(0, value)
        entry.grid(row=0, column=0, sticky="ew")
        if browse:

            def pick():
                path = filedialog.askopenfilename(parent=self)
                if path:
                    entry.delete(0, "end")
                    entry.insert(0, path)

            ctk.CTkButton(frame, text="찾기", width=50, command=pick).grid(row=0, column=1, padx=(6, 0))
        self.row += 1
        return entry

    def _focus(self) -> None:
        try:
            self.grab_set()
            self.lift()
            self.focus_force()
        except Exception:
            pass

    # ---- 동작 ------------------------------------------------------------------
    def _update_ytdlp(self) -> None:
        self.update_btn.configure(state="disabled", text="업데이트 중...")
        self._update_result.clear()

        def work():
            self._update_result.append(updater.update_ytdlp())

        threading.Thread(target=work, daemon=True).start()
        self.after(500, self._poll_update)

    def _poll_update(self) -> None:
        if not self._update_result:
            self.after(500, self._poll_update)
            return
        ok, out = self._update_result[0]
        self.update_btn.configure(state="normal", text="yt-dlp 업데이트")
        if ok:
            messagebox.showinfo("업데이트 완료", "yt-dlp 를 업데이트했습니다.\n프로그램을 다시 시작하면 적용됩니다.", parent=self)
        else:
            messagebox.showerror("업데이트 실패", out or "알 수 없는 오류", parent=self)

    def _save(self) -> None:
        s = self.settings
        s.format = self.v_format.get()
        s.mp3_bitrate = int(self.v_bitrate.get())
        s.wav_sample_rate = int(self.v_sr.get())
        s.wav_bit_depth = int(self.v_depth.get())
        s.filename_style = _inv(FILENAME_STYLES)[self.v_name.get()]
        s.on_exists = _inv(ON_EXISTS)[self.v_exists.get()]
        s.max_concurrent = int(self.v_conc.get())
        s.retries = int(self.v_retries.get())
        s.strip_emoji = bool(self.c_emoji.get())
        s.crop_cover_square = bool(self.c_crop.get())
        s.use_musicbrainz = bool(self.c_mb.get())
        s.fetch_lyrics = bool(self.c_lyr.get())
        s.wav_embed_cover = bool(self.c_wavcover.get())
        s.check_updates = bool(self.c_updates.get())
        cookie = self.v_cookie.get()
        s.cookies_browser = "" if cookie == "사용 안 함" else cookie
        s.cookies_file = self.e_cookiefile.get().strip()
        s.ffmpeg_path = self.e_ffmpeg.get().strip()
        s.save()
        self.on_save()
        self.destroy()
