"""GTK application lifecycle for the PDF ebook reader."""

from __future__ import annotations

import gi

gi.require_version("Gio", "2.0")
gi.require_version("Gtk", "4.0")
from gi.repository import Gio, Gtk  # noqa: E402

from .window import ReaderWindow


APPLICATION_ID = "io.github.local.PdfEbookReader"


class EbookReaderApplication(Gtk.Application):
    """Single GTK application instance for the reader."""

    def __init__(self) -> None:
        super().__init__(
            application_id=APPLICATION_ID,
            flags=Gio.ApplicationFlags.HANDLES_OPEN,
        )
        self._window: ReaderWindow | None = None

    def do_activate(self) -> None:
        """Create the main window on first activation and present it."""

        self._ensure_window()
        self._window.present()

    def do_open(self, files: list[Gio.File], n_files: int, _hint: str) -> None:
        """Open the first file supplied by the shell or a file manager."""

        self._ensure_window()
        self._window.present()
        if n_files > 0:
            self._window.open_file(files[0])

    def _ensure_window(self) -> None:
        if self._window is None:
            self._window = ReaderWindow(application=self)
