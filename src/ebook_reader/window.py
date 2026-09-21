"""Main GTK window and the Phase 2 PDF loading states."""

from __future__ import annotations

import sys
from pathlib import Path

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Gio", "2.0")
gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

from .document import DocumentError, PdfDocument
from .renderer import render_page


APPLICATION_TITLE = "PDF Ebook Reader"


def _centered_box(*, spacing: int = 12) -> Gtk.Box:
    """Create a box suitable for the centered empty/loading/error states."""

    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=spacing)
    box.set_halign(Gtk.Align.CENTER)
    box.set_valign(Gtk.Align.CENTER)
    box.set_margin_start(32)
    box.set_margin_end(32)
    box.set_margin_top(32)
    box.set_margin_bottom(32)
    return box


class ReaderWindow(Gtk.ApplicationWindow):
    """A single-document PDF reader window."""

    def __init__(self, *, application: Gtk.Application) -> None:
        super().__init__(application=application)
        self.set_title(APPLICATION_TITLE)
        self.set_default_size(1000, 750)

        self._document: PdfDocument | None = None
        self._load_generation = 0
        self._file_dialog: Gtk.FileDialog | None = None

        self._title_label = Gtk.Label(label=APPLICATION_TITLE)
        self._title_label.set_max_width_chars(48)

        header = Gtk.HeaderBar()
        header.set_title_widget(self._title_label)
        self._header_open_button = Gtk.Button(label="Open")
        self._header_open_button.set_tooltip_text("Open a PDF")
        self._header_open_button.connect("clicked", self._on_open_clicked)
        header.pack_start(self._header_open_button)
        self.set_titlebar(header)

        self._stack = Gtk.Stack()
        self._stack.set_hexpand(True)
        self._stack.set_vexpand(True)
        self._stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)

        self._stack.add_named(self._build_empty_state(), "empty")
        self._stack.add_named(self._build_loading_state(), "loading")
        self._stack.add_named(self._build_reader_state(), "reader")
        self._stack.add_named(self._build_error_state(), "error")
        self.set_child(self._stack)

        self._install_css()
        self._show_state("empty")

    def _install_css(self) -> None:
        """Add only the small amount of styling needed to distinguish paper."""

        display = Gdk.Display.get_default()
        if display is None:
            return

        provider = Gtk.CssProvider()
        provider.load_from_data(
            b"""
            .reader-page-area {
                background: alpha(@theme_fg_color, 0.04);
            }
            .reader-page {
                background: white;
                border: 1px solid alpha(black, 0.18);
                box-shadow: 0 3px 14px alpha(black, 0.20);
            }
            .reader-error {
                color: @error_color;
            }
            """
        )
        Gtk.StyleContext.add_provider_for_display(
            display,
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

    def _build_empty_state(self) -> Gtk.Widget:
        content = _centered_box()

        title = Gtk.Label(label=APPLICATION_TITLE)
        title.add_css_class("title-1")
        subtitle = Gtk.Label(label="Open a local PDF to start reading.")
        subtitle.set_wrap(True)
        subtitle.set_justify(Gtk.Justification.CENTER)

        open_button = Gtk.Button(label="Open PDF")
        open_button.add_css_class("suggested-action")
        open_button.set_tooltip_text("Choose a local PDF")
        open_button.connect("clicked", self._on_open_clicked)

        hint = Gtk.Label(label="You can also pass a PDF path when launching the application.")
        hint.add_css_class("dim-label")
        hint.set_wrap(True)
        hint.set_justify(Gtk.Justification.CENTER)

        content.append(title)
        content.append(subtitle)
        content.append(open_button)
        content.append(hint)
        return content

    def _build_loading_state(self) -> Gtk.Widget:
        content = _centered_box()
        spinner = Gtk.Spinner()
        spinner.set_halign(Gtk.Align.CENTER)
        spinner.start()
        label = Gtk.Label(label="Opening PDF…")
        label.add_css_class("dim-label")
        content.append(spinner)
        content.append(label)
        return content

    def _build_reader_state(self) -> Gtk.Widget:
        self._page_picture = Gtk.Picture()
        self._page_picture.set_keep_aspect_ratio(True)
        self._page_picture.set_can_shrink(False)
        self._page_picture.set_halign(Gtk.Align.CENTER)
        self._page_picture.set_valign(Gtk.Align.CENTER)

        self._page_frame = Gtk.Frame()
        self._page_frame.add_css_class("reader-page")
        self._page_frame.set_child(self._page_picture)
        self._page_frame.set_halign(Gtk.Align.CENTER)
        self._page_frame.set_valign(Gtk.Align.CENTER)

        page_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        page_box.add_css_class("reader-page-area")
        page_box.set_halign(Gtk.Align.FILL)
        page_box.set_valign(Gtk.Align.FILL)
        page_box.set_hexpand(True)
        page_box.set_vexpand(True)
        page_box.set_margin_start(24)
        page_box.set_margin_end(24)
        page_box.set_margin_top(24)
        page_box.set_margin_bottom(24)
        page_box.append(self._page_frame)

        self._page_status = Gtk.Label()
        self._page_status.add_css_class("dim-label")
        self._page_status.set_margin_bottom(10)
        self._page_status.set_halign(Gtk.Align.CENTER)

        reader = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        reader.set_hexpand(True)
        reader.set_vexpand(True)
        reader.append(page_box)
        reader.append(self._page_status)

        self._reader_scrolled = Gtk.ScrolledWindow()
        self._reader_scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        self._reader_scrolled.set_hexpand(True)
        self._reader_scrolled.set_vexpand(True)
        self._reader_scrolled.set_child(reader)
        return self._reader_scrolled

    def _build_error_state(self) -> Gtk.Widget:
        content = _centered_box()
        title = Gtk.Label(label="Could not open PDF")
        title.add_css_class("title-2")
        self._error_label = Gtk.Label()
        self._error_label.add_css_class("reader-error")
        self._error_label.set_wrap(True)
        self._error_label.set_max_width_chars(70)
        self._error_label.set_justify(Gtk.Justification.CENTER)

        retry_button = Gtk.Button(label="Choose another PDF")
        retry_button.add_css_class("suggested-action")
        retry_button.connect("clicked", self._on_open_clicked)

        content.append(title)
        content.append(self._error_label)
        content.append(retry_button)
        return content

    def _show_state(self, name: str) -> None:
        self._stack.set_visible_child_name(name)

    def _on_open_clicked(self, _button: Gtk.Button) -> None:
        self._file_dialog = Gtk.FileDialog()
        self._file_dialog.set_title("Open PDF")

        pdf_filter = Gtk.FileFilter()
        pdf_filter.set_name("PDF documents")
        pdf_filter.add_mime_type("application/pdf")
        pdf_filter.add_pattern("*.pdf")
        pdf_filter.add_pattern("*.PDF")

        all_filter = Gtk.FileFilter()
        all_filter.set_name("All files")
        all_filter.add_pattern("*")

        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(pdf_filter)
        filters.append(all_filter)
        self._file_dialog.set_filters(filters)
        self._file_dialog.set_default_filter(pdf_filter)
        self._file_dialog.open(self, None, self._on_file_dialog_finished)

    def _on_file_dialog_finished(self, dialog: Gtk.FileDialog, result: Gio.AsyncResult) -> None:
        try:
            selected = dialog.open_finish(result)
        except GLib.Error:
            # Cancellation is the normal result of dismissing the chooser.
            return
        finally:
            self._file_dialog = None

        if selected is not None:
            self.open_file(selected)

    def open_file(self, file: Gio.File | str | Path) -> None:
        """Begin opening a local file from the chooser or GApplication."""

        if isinstance(file, Gio.File):
            path = file.get_path()
            if path is None:
                self._show_error("Only local PDF files can be opened.")
                return
        else:
            path = str(file)

        self._load_generation += 1
        generation = self._load_generation
        self._document = None
        self._title_label.set_text(APPLICATION_TITLE)
        self.set_title(APPLICATION_TITLE)
        self._show_state("loading")

        # Let GTK paint the loading state before Poppler does synchronous file
        # parsing and page rendering. Threading is intentionally deferred until
        # it is needed by a later performance-focused phase.
        GLib.idle_add(self._load_file, path, generation)

    def _load_file(self, path: str, generation: int) -> bool:
        if generation != self._load_generation:
            return GLib.SOURCE_REMOVE

        try:
            document = PdfDocument.open(path)
            rendered = render_page(document.page(0))
        except DocumentError as error:
            if generation == self._load_generation:
                self._show_error(error.user_message)
            return GLib.SOURCE_REMOVE
        except Exception as error:  # pragma: no cover - defensive UI boundary
            print(f"Unable to render PDF {path!r}: {error}", file=sys.stderr)
            if generation == self._load_generation:
                self._show_error("The first page could not be rendered.")
            return GLib.SOURCE_REMOVE

        if generation != self._load_generation:
            return GLib.SOURCE_REMOVE

        self._document = document
        self._page_picture.set_paintable(rendered.texture)
        self._page_status.set_text(f"Page 1 of {document.page_count}")
        self._title_label.set_text(document.title)
        self.set_title(document.title)
        self._show_state("reader")
        return GLib.SOURCE_REMOVE

    def _show_error(self, message: str) -> None:
        self._document = None
        self._error_label.set_text(message)
        self._show_state("error")
