"""Main GTK window, PDF loading states, and reading controls."""

from __future__ import annotations

import sys
from pathlib import Path

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Gio", "2.0")
gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

from .document import DocumentError, PdfDocument
from .renderer import (
    DEFAULT_SCALE,
    ZOOM_STEP,
    clamp_manual_scale,
    fit_scale_for_viewport,
    render_page,
)


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
        self._render_generation = 0
        self._render_idle_id: int | None = None
        self._fit_timeout_id: int | None = None
        self._current_page = 0
        self._zoom_mode = "fit"
        self._manual_zoom = DEFAULT_SCALE
        self._last_render_scale = DEFAULT_SCALE
        self._reset_scroll_on_render = True
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
        self._install_input_controllers()
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

        hint = Gtk.Label(label="You can also pass a PDF path when launching or drop a PDF here.")
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

        self._page_area = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self._page_area.add_css_class("reader-page-area")
        self._page_area.set_halign(Gtk.Align.FILL)
        self._page_area.set_valign(Gtk.Align.FILL)
        self._page_area.set_hexpand(True)
        self._page_area.set_vexpand(True)
        self._page_area.set_margin_start(24)
        self._page_area.set_margin_end(24)
        self._page_area.set_margin_top(24)
        self._page_area.set_margin_bottom(24)
        self._page_area.append(self._page_frame)

        reader = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        reader.set_hexpand(True)
        reader.set_vexpand(True)

        self._reader_scrolled = Gtk.ScrolledWindow()
        self._reader_scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        self._reader_scrolled.set_hexpand(True)
        self._reader_scrolled.set_vexpand(True)
        self._reader_scrolled.set_child(self._page_area)
        reader.append(self._reader_scrolled)
        reader.append(self._build_reader_controls())

        self._reader_scrolled.connect("notify::width", self._on_reader_size_changed)
        self._reader_scrolled.connect("notify::height", self._on_reader_size_changed)
        return reader

    def _build_reader_controls(self) -> Gtk.Widget:
        """Build the compact navigation and zoom control strip."""

        controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        controls.set_halign(Gtk.Align.CENTER)
        controls.set_margin_start(12)
        controls.set_margin_end(12)
        controls.set_margin_top(6)
        controls.set_margin_bottom(10)

        self._previous_button = Gtk.Button(label="Previous")
        self._previous_button.set_tooltip_text("Previous page (Left or Page Up)")
        self._previous_button.connect("clicked", self._on_previous_clicked)

        self._page_entry = Gtk.Entry()
        self._page_entry.set_width_chars(5)
        self._page_entry.set_max_length(7)
        self._page_entry.set_input_purpose(Gtk.InputPurpose.DIGITS)
        self._page_entry.set_tooltip_text("Go to page number")
        self._page_entry.connect("activate", self._on_page_entry_activate)

        self._page_total_label = Gtk.Label(label="of 0")
        self._page_total_label.add_css_class("dim-label")

        self._next_button = Gtk.Button(label="Next")
        self._next_button.set_tooltip_text("Next page (Right, Page Down, or Space)")
        self._next_button.connect("clicked", self._on_next_clicked)

        separator = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
        separator.set_margin_start(6)
        separator.set_margin_end(6)

        self._zoom_out_button = Gtk.Button(label="−")
        self._zoom_out_button.set_tooltip_text("Zoom out (Ctrl+- or Ctrl+mouse wheel)")
        self._zoom_out_button.connect("clicked", self._on_zoom_out_clicked)

        self._zoom_label = Gtk.Label(label="Fit")
        self._zoom_label.set_width_chars(6)
        self._zoom_label.set_xalign(0.5)

        self._zoom_in_button = Gtk.Button(label="+")
        self._zoom_in_button.set_tooltip_text("Zoom in (Ctrl++ or Ctrl+mouse wheel)")
        self._zoom_in_button.connect("clicked", self._on_zoom_in_clicked)

        self._fit_button = Gtk.ToggleButton(label="Fit")
        self._fit_button.set_tooltip_text("Fit the page to the window (Ctrl+0)")
        self._fit_button.connect("toggled", self._on_fit_toggled)

        for child in (
            self._previous_button,
            self._page_entry,
            self._page_total_label,
            self._next_button,
            separator,
            self._zoom_out_button,
            self._zoom_label,
            self._zoom_in_button,
            self._fit_button,
        ):
            controls.append(child)

        return controls

    def _install_input_controllers(self) -> None:
        """Install keyboard, wheel-zoom, and file-drop input handling."""

        key_controller = Gtk.EventControllerKey()
        key_controller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        key_controller.connect("key-pressed", self._on_key_pressed)
        self.add_controller(key_controller)

        scroll_controller = Gtk.EventControllerScroll.new(Gtk.EventControllerScrollFlags.VERTICAL)
        scroll_controller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        scroll_controller.connect("scroll", self._on_scroll)
        self._reader_scrolled.add_controller(scroll_controller)

        file_drop = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        file_drop.connect("drop", self._on_file_list_drop)
        self.add_controller(file_drop)

        single_file_drop = Gtk.DropTarget.new(Gio.File, Gdk.DragAction.COPY)
        single_file_drop.connect("drop", self._on_single_file_drop)
        self.add_controller(single_file_drop)

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

    def _on_open_clicked(self, _button: Gtk.Button | None = None) -> None:
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
        self._render_generation += 1
        generation = self._load_generation
        self._document = None
        self._current_page = 0
        self._reset_scroll_on_render = True
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
        except DocumentError as error:
            if generation == self._load_generation:
                self._show_error(error.user_message)
            return GLib.SOURCE_REMOVE
        except Exception as error:  # pragma: no cover - defensive UI boundary
            print(f"Unable to render PDF {path!r}: {error}", file=sys.stderr)
            if generation == self._load_generation:
                self._show_error("The PDF could not be opened.")
            return GLib.SOURCE_REMOVE

        if generation != self._load_generation:
            return GLib.SOURCE_REMOVE

        self._document = document
        self._current_page = 0
        self._zoom_mode = "fit"
        self._manual_zoom = DEFAULT_SCALE
        self._reset_scroll_on_render = True
        self._update_reader_controls()
        self._title_label.set_text(document.title)
        self.set_title(document.title)
        self._show_state("reader")
        self._queue_render()
        return GLib.SOURCE_REMOVE

    def _show_error(self, message: str) -> None:
        self._document = None
        self._render_generation += 1
        self._error_label.set_text(message)
        self._show_state("error")

    def _on_previous_clicked(self, _button: Gtk.Button) -> None:
        self._go_to_page(self._current_page - 1)

    def _on_next_clicked(self, _button: Gtk.Button) -> None:
        self._go_to_page(self._current_page + 1)

    def _go_to_page(self, index: int) -> None:
        """Move to a clamped zero-based page index and request a render."""

        if self._document is None:
            return

        clamped = max(0, min(index, self._document.page_count - 1))
        if clamped == self._current_page:
            self._update_reader_controls()
            return

        self._current_page = clamped
        self._reset_scroll_on_render = True
        self._update_reader_controls()
        self._queue_render()

    def _on_page_entry_activate(self, entry: Gtk.Entry) -> None:
        """Navigate to the entered one-based page number."""

        if self._document is None:
            return

        try:
            requested = int(entry.get_text().strip())
        except ValueError:
            self._update_reader_controls()
            return

        self._go_to_page(requested - 1)
        entry.select_region(0, -1)

    def _on_zoom_out_clicked(self, _button: Gtk.Button) -> None:
        self._adjust_zoom(1 / ZOOM_STEP)

    def _on_zoom_in_clicked(self, _button: Gtk.Button) -> None:
        self._adjust_zoom(ZOOM_STEP)

    def _adjust_zoom(self, factor: float) -> None:
        """Enter manual zoom mode and adjust it by one bounded step."""

        if self._document is None:
            return

        if self._zoom_mode == "fit":
            self._manual_zoom = clamp_manual_scale(self._last_render_scale)
        self._manual_zoom = clamp_manual_scale(self._manual_zoom * factor)
        self._zoom_mode = "manual"
        self._reset_scroll_on_render = False
        self._fit_button.set_active(False)
        self._update_reader_controls()
        self._queue_render()

    def _on_fit_toggled(self, button: Gtk.ToggleButton) -> None:
        if self._document is None:
            return

        if button.get_active():
            self._zoom_mode = "fit"
        else:
            if self._zoom_mode == "fit":
                self._manual_zoom = clamp_manual_scale(self._last_render_scale)
            self._zoom_mode = "manual"
        self._reset_scroll_on_render = False
        self._update_reader_controls()
        self._queue_render()

    def _on_reader_size_changed(self, _widget: Gtk.ScrolledWindow, _param: object) -> None:
        if self._document is not None and self._zoom_mode == "fit":
            self._schedule_fit_render()

    def _schedule_fit_render(self) -> None:
        """Debounce fit-mode redraws while the window is being resized."""

        if self._fit_timeout_id is not None:
            GLib.source_remove(self._fit_timeout_id)
        self._fit_timeout_id = GLib.timeout_add(100, self._on_fit_timeout)

    def _on_fit_timeout(self) -> bool:
        self._fit_timeout_id = None
        if self._document is not None and self._zoom_mode == "fit":
            self._queue_render()
        return GLib.SOURCE_REMOVE

    def _queue_render(self) -> None:
        """Coalesce render requests so only the newest page/zoom wins."""

        self._render_generation += 1
        if self._render_idle_id is None:
            self._render_idle_id = GLib.idle_add(self._render_current_page)

    def _render_current_page(self) -> bool:
        self._render_idle_id = None
        document = self._document
        if document is None:
            return GLib.SOURCE_REMOVE

        generation = self._render_generation
        try:
            page = document.page(self._current_page)
            source_width, source_height = page.get_size()
            scale = self._requested_render_scale(source_width, source_height)
            rendered = render_page(page, scale=scale)
        except DocumentError as error:
            if generation == self._render_generation:
                self._show_error(error.user_message)
            return GLib.SOURCE_REMOVE
        except Exception as error:  # pragma: no cover - defensive UI boundary
            print(
                f"Unable to render page {self._current_page + 1} of {document.path!s}: {error}",
                file=sys.stderr,
            )
            if generation == self._render_generation:
                self._show_error("This page could not be rendered.")
            return GLib.SOURCE_REMOVE

        if document is not self._document or generation != self._render_generation:
            return GLib.SOURCE_REMOVE

        self._last_render_scale = scale
        self._page_picture.set_paintable(rendered.texture)
        self._page_picture.set_size_request(rendered.width, rendered.height)
        self._update_reader_controls()

        if self._reset_scroll_on_render:
            self._reader_scrolled.get_vadjustment().set_value(0)
            self._reset_scroll_on_render = False
        return GLib.SOURCE_REMOVE

    def _requested_render_scale(self, page_width: float, page_height: float) -> float:
        if self._zoom_mode == "manual":
            return self._manual_zoom

        viewport_width = max(1, self._reader_scrolled.get_width() - 48)
        viewport_height = max(1, self._reader_scrolled.get_height() - 48)
        try:
            return fit_scale_for_viewport(
                page_width,
                page_height,
                viewport_width,
                viewport_height,
            )
        except ValueError:
            return DEFAULT_SCALE

    def _update_reader_controls(self) -> None:
        document = self._document
        has_document = document is not None
        page_count = document.page_count if document is not None else 0

        self._previous_button.set_sensitive(has_document and self._current_page > 0)
        self._next_button.set_sensitive(has_document and self._current_page + 1 < page_count)
        self._page_entry.set_sensitive(has_document)
        self._page_total_label.set_text(f"of {page_count}")
        self._zoom_out_button.set_sensitive(has_document)
        self._zoom_in_button.set_sensitive(has_document)
        self._fit_button.set_sensitive(has_document)

        if has_document:
            self._page_entry.set_text(str(self._current_page + 1))

        if self._zoom_mode == "fit":
            self._zoom_label.set_text("Fit")
            if not self._fit_button.get_active():
                self._fit_button.set_active(True)
        else:
            self._zoom_label.set_text(f"{round(self._manual_zoom * 100)}%")
            if self._fit_button.get_active():
                self._fit_button.set_active(False)

    def _on_key_pressed(
        self,
        _controller: Gtk.EventControllerKey,
        keyval: int,
        _keycode: int,
        state: Gdk.ModifierType,
    ) -> bool:
        """Handle reader shortcuts while leaving page-entry typing alone."""

        if self._page_entry.has_focus():
            return False

        control = bool(state & Gdk.ModifierType.CONTROL_MASK)
        if control and keyval in (Gdk.KEY_o, Gdk.KEY_O):
            self._on_open_clicked()
            return True
        if control and keyval in (Gdk.KEY_plus, Gdk.KEY_equal, Gdk.KEY_KP_Add):
            self._adjust_zoom(ZOOM_STEP)
            return True
        if control and keyval in (Gdk.KEY_minus, Gdk.KEY_KP_Subtract):
            self._adjust_zoom(1 / ZOOM_STEP)
            return True
        if control and keyval in (Gdk.KEY_0, Gdk.KEY_KP_0):
            if self._document is not None:
                self._zoom_mode = "fit"
                self._fit_button.set_active(True)
                self._update_reader_controls()
                self._queue_render()
            return True

        if keyval == Gdk.KEY_F11:
            self._toggle_fullscreen()
            return True
        if keyval == Gdk.KEY_Escape and self.is_fullscreen():
            self.unfullscreen()
            return True
        if keyval == Gdk.KEY_space and state & Gdk.ModifierType.SHIFT_MASK:
            self._go_to_page(self._current_page - 1)
            return True
        if keyval in (Gdk.KEY_Right, Gdk.KEY_Page_Down, Gdk.KEY_space):
            self._go_to_page(self._current_page + 1)
            return True
        if keyval in (Gdk.KEY_Left, Gdk.KEY_Page_Up):
            self._go_to_page(self._current_page - 1)
            return True
        if keyval == Gdk.KEY_Home:
            self._go_to_page(0)
            return True
        if keyval == Gdk.KEY_End and self._document is not None:
            self._go_to_page(self._document.page_count - 1)
            return True
        return False

    def _on_scroll(
        self,
        controller: Gtk.EventControllerScroll,
        _delta_x: float,
        delta_y: float,
    ) -> bool:
        state = controller.get_current_event_state()
        if not state & Gdk.ModifierType.CONTROL_MASK or self._document is None:
            return False
        if delta_y < 0:
            self._adjust_zoom(ZOOM_STEP)
        elif delta_y > 0:
            self._adjust_zoom(1 / ZOOM_STEP)
        return True

    def _toggle_fullscreen(self) -> None:
        if self.is_fullscreen():
            self.unfullscreen()
        else:
            self.fullscreen()

    def _on_file_list_drop(
        self,
        _target: Gtk.DropTarget,
        value: Gdk.FileList,
        _x: float,
        _y: float,
    ) -> bool:
        files = value.get_files()
        if not files:
            return False
        self.open_file(files[0])
        return True

    def _on_single_file_drop(
        self,
        _target: Gtk.DropTarget,
        value: Gio.File,
        _x: float,
        _y: float,
    ) -> bool:
        self.open_file(value)
        return True
