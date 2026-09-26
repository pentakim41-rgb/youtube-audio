"""실행 진입점: python main.py"""
import customtkinter as ctk

from config import Settings
from core.history import ArtistMemory, History
from ui.main_window import MainWindow
from utils.logger import setup_logging
from utils.paths import cleanup_old_work_dirs


def main() -> None:
    setup_logging()
    cleanup_old_work_dirs()
    ctk.set_appearance_mode("light")
    ctk.set_default_color_theme("blue")
    settings = Settings.load()
    app = MainWindow(settings, History(), ArtistMemory())
    app.mainloop()


if __name__ == "__main__":
    main()
