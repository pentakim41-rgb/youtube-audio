"""메인 화면: 링크 입력 → 리스트에 추가 → 목록에서 선택/편집/삭제 → 전체 다운로드."""
from __future__ import annotations

import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import customtkinter as ctk

from config import Settings
from core.errors import AppError, classify
from core.fetcher import FetchResult, UrlInfo, analyze_url, extract_urls, fetch_tracks
from core.history import ArtistMemory, History
from core.models import EDITABLE_FIELDS, Status, Track
from core.queue_manager import QueueManager
from ui.edit_dialog import EditDialog
from ui.settings_dialog import SettingsDialog
from utils import updater
from utils.logger import get_logger
from utils.paths import open_folder

log = get_logger("ui")

COLUMNS = (
    ("no", "#", 40, "center"),
    ("artist", "가수", 170, "w"),
    ("title", "제목", 280, "w"),
    ("album", "앨범", 170, "w"),
    ("length", "길이", 60, "center"),
    ("formats", "다운로드", 80, "center"),
    ("status", "상태", 190, "w"),
)


def fmt_duration(sec: int | None) -> str:
    if not sec:
        return ""
    m, s = divmod(int(sec), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


class MainWindow(ctk.CTk):
    def __init__(self, settings: Settings, history: History, memory: ArtistMemory):
        super().__init__()
        self.settings = settings
        self.history = history
        self.memory = memory

        self.title("유튜브 → MP3/WAV 변환기")
        self.geometry("1000x680")
        self.minsize(820, 520)

        self.tracks: dict[int, Track] = {}  # uid → Track
        self.order: list[int] = []  # 목록에 보이는 순서
        self.ui_queue: "queue.Queue[tuple]" = queue.Queue()
        self.pending_fetches = 0
        self._fetch_lock = threading.Lock()

        self.qm = QueueManager(settings, history, memory, on_event=lambda t: self.ui_queue.put(("track", t)))

        self._build_widgets()
        self._bind_keys()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(100, self._poll)
        self.after(600, self._startup_checks)

    # ================================================================ 화면 구성
    def _build_widgets(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        # --- 1행: 링크 입력 + 리스트에 추가 --------------------------------------
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", padx=16, pady=(16, 6))
        top.grid_columnconfigure(0, weight=1)
        self.url_entry = ctk.CTkEntry(
            top, height=38, placeholder_text="유튜브 링크를 붙여넣고 Enter (여러 개를 한꺼번에 붙여넣어도 됩니다)"
        )
        self.url_entry.grid(row=0, column=0, sticky="ew")
        self.add_btn = ctk.CTkButton(top, text="리스트에 추가", width=130, height=38, command=self._add_from_entry)
        self.add_btn.grid(row=0, column=1, padx=(8, 0))

        # --- 2행: 옵션 ----------------------------------------------------------------
        opt = ctk.CTkFrame(self, fg_color="transparent")
        opt.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 6))
        ctk.CTkLabel(opt, text="포맷").pack(side="left")
        self.format_seg = ctk.CTkSegmentedButton(opt, values=["mp3", "wav"], command=self._on_format, width=120)
        self.format_seg.set(self.settings.format)
        self.format_seg.pack(side="left", padx=(8, 16))
        ctk.CTkLabel(opt, text="저장 폴더").pack(side="left")
        self.folder_label = ctk.CTkLabel(opt, text=self.settings.output_dir, text_color="gray30", anchor="w")
        self.folder_label.pack(side="left", padx=8)
        ctk.CTkButton(opt, text="변경", width=54, command=self._choose_folder).pack(side="left")
        ctk.CTkButton(opt, text="열기", width=54, fg_color="gray55", hover_color="gray45",
                      command=lambda: open_folder(self.settings.output_dir)).pack(side="left", padx=(6, 0))
        ctk.CTkButton(opt, text="설정", width=70, fg_color="gray55", hover_color="gray45",
                      command=self._open_settings).pack(side="right")
        self.auto_var = ctk.BooleanVar(value=self.settings.auto_start)
        ctk.CTkCheckBox(opt, text="추가하면 바로 다운로드", variable=self.auto_var,
                        command=self._on_auto).pack(side="right", padx=16)

        # --- 3행: 목록 창 ----------------------------------------------------------------
        box = ctk.CTkFrame(self)
        box.grid(row=2, column=0, sticky="nsew", padx=16, pady=6)
        box.grid_columnconfigure(0, weight=1)
        box.grid_rowconfigure(0, weight=1)

        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("Treeview", rowheight=30, font=("Malgun Gothic", 10), background="white",
                        fieldbackground="white", borderwidth=0)
        style.configure("Treeview.Heading", font=("Malgun Gothic", 10, "bold"))
        style.map("Treeview", background=[("selected", "#3b8ed0")], foreground=[("selected", "white")])

        self.tree = ttk.Treeview(box, columns=[c[0] for c in COLUMNS], show="headings", selectmode="extended")
        for key, label, width, anchor in COLUMNS:
            self.tree.heading(key, text=label)
            self.tree.column(key, width=width, anchor=anchor, stretch=key in ("artist", "title", "album", "status"))
        self.tree.tag_configure("done", foreground="#1a7f37")
        self.tree.tag_configure("failed", foreground="#c62828")
        self.tree.tag_configure("active", foreground="#1565c0")
        self.tree.tag_configure("muted", foreground="#888888")
        scroll = ttk.Scrollbar(box, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.grid(row=0, column=0, sticky="nsew", padx=(2, 0), pady=2)
        scroll.grid(row=0, column=1, sticky="ns", pady=2)
        self.tree.bind("<Double-1>", lambda _e: self._edit_selected())
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self._on_select())

        self.empty_label = ctk.CTkLabel(
            box, text="위 입력칸에 유튜브 링크를 붙여넣고 '리스트에 추가'를 누르세요.\n"
                      "추가된 곡은 클릭해서 선택하고, 더블클릭으로 가수/앨범을 수정할 수 있습니다.",
            text_color="gray50", justify="center")
        self.empty_label.place(relx=0.5, rely=0.5, anchor="center")

        # --- 4행: 목록 조작 버튼 ---------------------------------------------------------------
        actions = ctk.CTkFrame(self, fg_color="transparent")
        actions.grid(row=3, column=0, sticky="ew", padx=16, pady=(4, 2))
        ctk.CTkButton(actions, text="선택 편집", width=90, command=self._edit_selected).pack(side="left")
        ctk.CTkButton(actions, text="선택 삭제", width=90, fg_color="#c62828", hover_color="#a51f1f",
                      command=self._delete_selected).pack(side="left", padx=6)
        ctk.CTkButton(actions, text="전체 선택", width=80, fg_color="gray55", hover_color="gray45",
                      command=self._select_all).pack(side="left")
        ctk.CTkButton(actions, text="완료 항목 지우기", width=120, fg_color="gray55", hover_color="gray45",
                      command=self._clear_finished).pack(side="left", padx=6)
        ctk.CTkButton(actions, text="선택 다운로드", width=100, fg_color="gray55", hover_color="gray45",
                      command=self._retry_selected).pack(side="left")
        self.download_btn = ctk.CTkButton(actions, text="전체 다운로드", width=140, height=36,
                                          command=self._download_all)
        self.download_btn.pack(side="right")
        ctk.CTkButton(actions, text="전체 취소", width=90, height=36, fg_color="gray55", hover_color="gray45",
                      command=self._cancel_all).pack(side="right", padx=8)

        # --- 5행: 전체 진행률 / 상태 -------------------------------------------------------------
        bottom = ctk.CTkFrame(self, fg_color="transparent")
        bottom.grid(row=4, column=0, sticky="ew", padx=16, pady=(6, 12))
        bottom.grid_columnconfigure(0, weight=1)
        self.progress = ctk.CTkProgressBar(bottom)
        self.progress.set(0)
        self.progress.grid(row=0, column=0, sticky="ew")
        self.summary_label = ctk.CTkLabel(bottom, text="목록이 비어 있습니다", anchor="e", width=260)
        self.summary_label.grid(row=0, column=1, padx=(10, 0))
        self.detail_label = ctk.CTkLabel(bottom, text="", anchor="w", text_color="gray30", justify="left")
        self.detail_label.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        self.bind("<Configure>", self._on_resize)

    def _bind_keys(self) -> None:
        self.url_entry.bind("<Return>", lambda _e: self._add_from_entry())
        self.tree.bind("<Delete>", lambda _e: self._delete_selected())
        self.tree.bind("<Control-a>", lambda _e: (self._select_all(), "break")[1])
        self.tree.bind("<Return>", lambda _e: self._edit_selected())

    def _on_resize(self, event) -> None:
        if event.widget is self:
            self.detail_label.configure(wraplength=max(self.winfo_width() - 40, 200))

    # ================================================================ 링크 추가/조회
    def _add_from_entry(self) -> None:
        text = self.url_entry.get().strip()
        if not text:
            return
        jobs: list[tuple[UrlInfo, bool]] = []
        errors: list[str] = []
        for raw in extract_urls(text):
            info = analyze_url(raw)
            if info.kind == "invalid":
                errors.append(f"{raw[:60]} → {info.reason}")
                continue
            playlist = info.kind == "playlist"
            if info.kind == "video_in_playlist":
                answer = messagebox.askyesnocancel(
                    "재생목록 링크",
                    "이 링크는 재생목록 안의 영상입니다.\n\n"
                    "예 : 재생목록 전체를 추가\n아니오 : 이 영상만 추가\n취소 : 추가 안 함",
                    parent=self,
                )
                if answer is None:
                    continue
                playlist = bool(answer)
            jobs.append((info, playlist))
        if errors:
            messagebox.showwarning("추가할 수 없는 링크", "\n".join(errors[:10]), parent=self)
        if not jobs:
            return
        self.url_entry.delete(0, "end")
        with self._fetch_lock:
            self.pending_fetches += len(jobs)
        self._refresh_summary()
        threading.Thread(target=self._fetch_worker, args=(jobs,), daemon=True).start()
        self.url_entry.focus_set()

    def _fetch_worker(self, jobs: list[tuple[UrlInfo, bool]]) -> None:
        for info, playlist in jobs:
            label = info.video_id or info.playlist_id
            try:
                result = fetch_tracks(info, self.settings, playlist, self.memory)
                self.ui_queue.put(("fetched", result))
            except AppError as exc:
                self.ui_queue.put(("fetch_error", label, exc.message))
            except Exception as exc:
                err = classify(exc)
                self.ui_queue.put(("fetch_error", label, err.message))

    def _on_fetched(self, result: FetchResult) -> None:
        with self._fetch_lock:
            self.pending_fetches = max(self.pending_fetches - 1, 0)
        existing = {t.video_id for t in self.tracks.values()}
        fresh = [t for t in result.tracks if t.video_id not in existing]
        in_list = len(result.tracks) - len(fresh)

        # 이미 받은 곡 확인 (기록에 있고 파일이 아직 남아 있는 경우)
        for t in fresh:
            t.formats |= self.history.formats(t.video_id)
        dupes = [t for t in fresh if self.settings.format in t.formats]
        if dupes:
            names = "\n".join(f"· {t.display_name()}" for t in dupes[:8])
            more = f"\n… 외 {len(dupes) - 8}곡" if len(dupes) > 8 else ""
            if messagebox.askyesno(
                "이미 받은 곡",
                f"이미 받은 적이 있는 곡이 {len(dupes)}개 있습니다.\n{names}{more}\n\n이 곡들은 목록에서 제외할까요?",
                parent=self,
            ):
                skip = {t.video_id for t in dupes}
                fresh = [t for t in fresh if t.video_id not in skip]

        for t in fresh:
            self.tracks[t.uid] = t
            self.order.append(t.uid)
            self.tree.insert("", "end", iid=str(t.uid), values=self._row_values(t), tags=self._row_tags(t))
        notes = []
        if in_list:
            notes.append(f"이미 목록에 있는 {in_list}곡은 건너뜀")
        if result.skipped:
            notes.append(f"비공개/삭제된 항목 {result.skipped}개 제외")
        if notes:
            self.detail_label.configure(text=" · ".join(notes))
        self._renumber()
        self._refresh_summary()
        if self.settings.auto_start:
            for t in fresh:
                if t.needs(self.settings.format):
                    self.qm.enqueue(t)

    # ================================================================ 목록 조작
    def _selected_tracks(self) -> list[Track]:
        return [self.tracks[int(i)] for i in self.tree.selection() if int(i) in self.tracks]

    def _select_all(self) -> None:
        self.tree.selection_set(self.tree.get_children())

    def _delete_selected(self) -> None:
        selected = self._selected_tracks()
        if not selected:
            return
        busy = [t for t in selected if t.status.active]
        if busy and not messagebox.askyesno(
            "삭제", f"진행 중인 곡 {len(busy)}개는 취소하고 삭제합니다. 계속할까요?", parent=self
        ):
            return
        for t in selected:
            if t.status.active:
                self.qm.cancel(t)
            self.tree.delete(str(t.uid))
            self.tracks.pop(t.uid, None)
            if t.uid in self.order:
                self.order.remove(t.uid)
        self._renumber()
        self._refresh_summary()

    def _clear_finished(self) -> None:
        for uid in [u for u in self.order if self.tracks[u].status in (Status.DONE, Status.SKIPPED)]:
            self.tree.delete(str(uid))
            self.tracks.pop(uid, None)
            self.order.remove(uid)
        self._renumber()
        self._refresh_summary()

    def _edit_selected(self) -> None:
        selected = self._selected_tracks()
        if not selected:
            messagebox.showinfo("편집", "편집할 곡을 목록에서 선택하세요.", parent=self)
            return
        if any(t.status.active for t in selected):
            messagebox.showinfo("편집", "진행 중인 곡은 편집할 수 없습니다.", parent=self)
            return
        EditDialog(self, selected, lambda values, remember: self._apply_edit(selected, values, remember))

    def _apply_edit(self, tracks: list[Track], values: dict, remember: bool) -> None:
        for t in tracks:
            for key, text in values.items():
                if key not in EDITABLE_FIELDS:
                    continue
                if key == "track_no":
                    try:
                        setattr(t, key, int(text) if text else None)
                    except ValueError:
                        continue
                else:
                    setattr(t, key, text)
                t.edited.add(key)
            if "artist" in values:
                t.artist_source = "user"
            t.remember_artist = remember
            self._update_row(t)

    def _retry_selected(self) -> None:
        fmt = self.settings.format
        for t in self._selected_tracks():
            if t.status in (Status.FAILED, Status.CANCELLED) or t.needs(fmt):
                self.qm.enqueue(t)

    # ================================================================ 다운로드
    def _download_all(self) -> None:
        fmt = self.settings.format
        targets = [t for uid in self.order if (t := self.tracks[uid]).needs(fmt)]
        if not targets:
            if self.order:
                messagebox.showinfo("다운로드", f"목록의 곡을 모두 {fmt} 로 이미 받았습니다.", parent=self)
            else:
                messagebox.showinfo("다운로드", "다운로드할 곡이 없습니다. 링크를 먼저 추가하세요.", parent=self)
            return
        already = sum(1 for t in self.tracks.values() if fmt in t.formats)
        if already:
            self.detail_label.configure(text=f"{fmt} 로 이미 받은 {already}곡은 건너뜀")
        self.qm.set_concurrency(self.settings.max_concurrent)
        for t in targets:
            self.qm.enqueue(t)

    def _cancel_all(self) -> None:
        for t in self.tracks.values():
            if t.status.active:
                self.qm.cancel(t)

    # ================================================================ 설정
    def _on_format(self, value: str) -> None:
        self.settings.format = value
        self.settings.save()

    def _on_auto(self) -> None:
        self.settings.auto_start = bool(self.auto_var.get())
        self.settings.save()

    def _choose_folder(self) -> None:
        path = filedialog.askdirectory(initialdir=self.settings.output_dir, parent=self)
        if path:
            self.settings.output_dir = path
            self.settings.save()
            self.folder_label.configure(text=path)

    def _open_settings(self) -> None:
        SettingsDialog(self, self.settings, self._settings_saved)

    def _settings_saved(self) -> None:
        self.format_seg.set(self.settings.format)
        self.folder_label.configure(text=self.settings.output_dir)
        self.auto_var.set(self.settings.auto_start)
        self.qm.set_concurrency(self.settings.max_concurrent)

    # ================================================================ 표시 갱신
    @staticmethod
    def _row_tags(t: Track) -> tuple:
        if t.status == Status.DONE:
            return ("done",)
        if t.status == Status.FAILED:
            return ("failed",)
        if t.status.active:
            return ("active",)
        if t.status in (Status.CANCELLED, Status.SKIPPED):
            return ("muted",)
        return ()

    def _status_text(self, t: Track) -> str:
        if t.status == Status.FAILED:
            return f"실패 - {t.message}"[:60]
        if t.status in (Status.DOWNLOADING, Status.CONVERTING, Status.TAGGING):
            return f"{t.status.value} {t.progress:.0f}%"
        if t.status == Status.RETRYING:
            return "재시도 대기 중"
        return t.status.value

    def _row_values(self, t: Track) -> tuple:
        try:
            no = self.order.index(t.uid) + 1
        except ValueError:
            no = len(self.order) + 1
        return (no, t.artist, t.title, t.album, fmt_duration(t.duration), t.formats_text(), self._status_text(t))

    def _update_row(self, t: Track) -> None:
        iid = str(t.uid)
        if t.uid in self.tracks and self.tree.exists(iid):
            self.tree.item(iid, values=self._row_values(t), tags=self._row_tags(t))

    def _renumber(self) -> None:
        for uid in self.order:
            self._update_row(self.tracks[uid])
        if self.order:
            self.empty_label.place_forget()
        else:
            self.empty_label.place(relx=0.5, rely=0.5, anchor="center")

    def _on_select(self) -> None:
        sel = self._selected_tracks()
        if len(sel) == 1:
            t = sel[0]
            text = t.message or ""
            if t.status == Status.FAILED and text:
                text = f"실패 사유: {text}"
            elif t.output_path:
                text = f"저장 위치: {t.output_path}"
            self.detail_label.configure(text=text)

    def _refresh_summary(self) -> None:
        total = len(self.order)
        parts = [f"총 {total}곡"]
        counts = {"done": 0, "failed": 0, "active": 0}
        for t in self.tracks.values():
            if t.status == Status.DONE:
                counts["done"] += 1
            elif t.status == Status.FAILED:
                counts["failed"] += 1
            elif t.status.active:
                counts["active"] += 1
        if counts["done"]:
            parts.append(f"완료 {counts['done']}")
        if counts["failed"]:
            parts.append(f"실패 {counts['failed']}")
        if counts["active"]:
            parts.append(f"진행 {counts['active']}")
        if self.pending_fetches:
            parts.append(f"조회 중 {self.pending_fetches}")
        self.summary_label.configure(text=" · ".join(parts) if total or self.pending_fetches else "목록이 비어 있습니다")

        started = [t for t in self.tracks.values() if t.status != Status.READY]
        if started:
            value = sum(100.0 if t.status.finished else t.progress for t in started) / (100.0 * len(started))
        else:
            value = 0.0
        self.progress.set(min(max(value, 0.0), 1.0))

    # ================================================================ 이벤트 처리 (UI 스레드)
    def _poll(self) -> None:
        changed = False
        try:
            for _ in range(200):  # 한 번에 너무 많이 처리해 화면이 멈추지 않도록 제한
                event = self.ui_queue.get_nowait()
                kind = event[0]
                if kind == "track":
                    track = event[1]
                    if track.uid in self.tracks:
                        self._update_row(track)
                        changed = True
                elif kind == "fetched":
                    self._on_fetched(event[1])
                elif kind == "fetch_error":
                    with self._fetch_lock:
                        self.pending_fetches = max(self.pending_fetches - 1, 0)
                    self._refresh_summary()
                    messagebox.showerror("링크 조회 실패", f"{event[1]}\n\n{event[2]}", parent=self)
                elif kind == "outdated":
                    self._ask_update(event[1], event[2])
                elif kind == "warnings":
                    messagebox.showwarning("환경 점검", "\n\n".join(event[1]), parent=self)
        except queue.Empty:
            pass
        if changed:
            self._refresh_summary()
        self.after(100, self._poll)

    # ================================================================ 시작 점검 / 종료
    def _startup_checks(self) -> None:
        warnings = updater.environment_warnings(self.settings.ffmpeg_path)
        if warnings:
            self.ui_queue.put(("warnings", warnings))
        if self.settings.check_updates:

            def work():
                current, latest = updater.installed_version(), updater.latest_version()
                if updater.is_outdated(current, latest):
                    self.ui_queue.put(("outdated", current, latest))

            threading.Thread(target=work, daemon=True).start()

    def _ask_update(self, current: str, latest: str) -> None:
        if not updater.can_self_update():
            messagebox.showinfo("yt-dlp 업데이트", f"새 버전({latest})이 있습니다. 새로 빌드해 주세요.", parent=self)
            return
        if messagebox.askyesno(
            "yt-dlp 업데이트",
            f"yt-dlp 새 버전이 있습니다. ({current} → {latest})\n\n유튜브 변경 대응을 위해 업데이트를 권장합니다. 지금 업데이트할까요?",
            parent=self,
        ):
            self.detail_label.configure(text="yt-dlp 업데이트 중...")

            def work():
                ok, out = updater.update_ytdlp()
                text = "yt-dlp 업데이트 완료 - 프로그램을 다시 시작하면 적용됩니다." if ok else f"업데이트 실패: {out[-200:]}"
                self.after(0, lambda: self.detail_label.configure(text=text))

            threading.Thread(target=work, daemon=True).start()

    def _on_close(self) -> None:
        if any(t.status.active for t in self.tracks.values()):
            if not messagebox.askyesno("종료", "진행 중인 다운로드가 있습니다. 취소하고 종료할까요?", parent=self):
                return
        self.qm.shutdown()
        self.settings.save()
        self.destroy()
