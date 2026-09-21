"""GTK application lifecycle for the PDF ebook reader."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from .window import ReaderWindow


APPLICATION_ID = "io.github.local.PdfEbookReader"


class EbookReaderApplication(Gtk.Application):
    """Single GTK application instance for the reader."""

    def __init__(self) -> None:
        super().__init__(
            application_id=APPLICATION_ID,
        )
        self._window: ReaderWindow | None = None

    def do_activate(self) -> None:
        """Create the main window on first activation and present it."""

        if self._window is None:
            self._window = ReaderWindow(application=self)
        self._window.present()
