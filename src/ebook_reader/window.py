"""Main window for the Phase 1 application skeleton."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402


class ReaderWindow(Gtk.ApplicationWindow):
    """A quiet empty reader window ready for the Phase 2 document view."""

    def __init__(self, *, application: Gtk.Application) -> None:
        super().__init__(application=application)
        self.set_title("PDF Ebook Reader")
        self.set_default_size(1000, 750)

        header = Gtk.HeaderBar()
        header.set_title_widget(Gtk.Label(label="PDF Ebook Reader"))
        self.set_titlebar(header)

        content = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=12,
        )
        content.set_halign(Gtk.Align.CENTER)
        content.set_valign(Gtk.Align.CENTER)
        content.set_margin_start(32)
        content.set_margin_end(32)
        content.set_margin_top(32)
        content.set_margin_bottom(32)

        title = Gtk.Label(label="PDF Ebook Reader")
        title.add_css_class("title-1")
        subtitle = Gtk.Label(label="PDF opening and reading controls are coming in Phase 2.")
        subtitle.set_wrap(True)
        subtitle.set_justify(Gtk.Justification.CENTER)

        content.append(title)
        content.append(subtitle)
        self.set_child(content)
