"""가수/앨범/제목 등을 수정하는 창 (여러 곡을 선택했으면 입력한 칸만 전체에 적용)."""
from __future__ import annotations

from typing import Callable

import customtkinter as ctk

from core.models import Track

FIELDS = [
    ("title", "제목"),
    ("artist", "가수"),
    ("album", "앨범"),
    ("album_artist", "앨범 아티스트"),
    ("track_no", "트랙 번호"),
    ("year", "연도"),
    ("genre", "장르"),
]


class EditDialog(ctk.CTkToplevel):
    def __init__(self, master, tracks: list[Track], on_save: Callable[[dict, bool], None]):
        super().__init__(master)
        self.tracks = tracks
        self.on_save = on_save
        self.multi = len(tracks) > 1
        self.title("태그 편집" if not self.multi else f"태그 편집 ({len(tracks)}곡 일괄)")
        self.geometry("460x470")
        self.resizable(False, False)
        self.transient(master)

        if self.multi:
            ctk.CTkLabel(
                self, text="입력한 칸만 선택한 모든 곡에 적용됩니다. (비워 두면 그대로 유지)",
                text_color="gray40", wraplength=420,
            ).pack(padx=20, pady=(16, 4), anchor="w")

        form = ctk.CTkFrame(self, fg_color="transparent")
        form.pack(fill="x", padx=20, pady=(12, 0))
        form.grid_columnconfigure(1, weight=1)
        self.entries: dict[str, ctk.CTkEntry] = {}
        first = tracks[0]
        for row, (key, label) in enumerate(FIELDS):
            ctk.CTkLabel(form, text=label, width=100, anchor="w").grid(row=row, column=0, pady=6, sticky="w")
            entry = ctk.CTkEntry(form)
            entry.grid(row=row, column=1, pady=6, sticky="ew")
            if self.multi:
                if key == "title" or key == "track_no":
                    entry.configure(state="disabled", placeholder_text="(일괄 편집 불가)")
                else:
                    # 모든 곡이 같은 값이면 미리 채워 준다
                    values = {str(getattr(t, key) or "") for t in tracks}
                    if len(values) == 1:
                        entry.insert(0, values.pop())
            else:
                value = getattr(first, key)
                entry.insert(0, "" if value in (None, "") else str(value))
            self.entries[key] = entry

        self.remember = ctk.CTkCheckBox(self, text="이 채널은 앞으로 이 가수 이름 사용 (제목에서 가수를 못 찾을 때)")
        self.remember.select()
        self.remember.pack(padx=20, pady=(14, 0), anchor="w")

        buttons = ctk.CTkFrame(self, fg_color="transparent")
        buttons.pack(side="bottom", fill="x", padx=20, pady=16)
        ctk.CTkButton(buttons, text="저장", width=100, command=self._save).pack(side="right")
        ctk.CTkButton(
            buttons, text="취소", width=100, fg_color="gray60", hover_color="gray50", command=self.destroy
        ).pack(side="right", padx=8)

        self.bind("<Return>", lambda _e: self._save())
        self.bind("<Escape>", lambda _e: self.destroy())
        self.after(150, self._focus)

    def _focus(self) -> None:
        try:
            self.grab_set()
            self.lift()
            self.focus_force()
        except Exception:
            pass

    def _save(self) -> None:
        values: dict[str, str] = {}
        for key, entry in self.entries.items():
            if str(entry.cget("state")) == "disabled":
                continue
            text = entry.get().strip()
            if self.multi and not text:
                continue  # 비운 칸은 건드리지 않음
            values[key] = text
        self.on_save(values, bool(self.remember.get()))
        self.destroy()
