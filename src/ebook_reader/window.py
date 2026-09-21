"""Main GTK window, PDF loading states, and reading controls."""

from __future__ import annotations

from concurrent.futures import CancelledError, Future, ThreadPoolExecutor
from dataclasses import dataclass
import sys
from pathlib import Path

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Gio", "2.0")
gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

from .annotations import (
    Annotation,
    AnnotationStore,
    marker_top_left,
    pixels_to_normalized,
)
from .document import DocumentError, PdfDocument
from .renderer import (
    DEFAULT_SCALE,
    RenderCache,
    RenderCacheKey,
    RenderedPage,
    ZOOM_STEP,
    clamp_manual_scale,
    fit_scale_for_viewport,
    render_page,
)
from .settings import SettingsStore
from .text_selection import (
    HighlightRectangle,
    MAX_SELECTION_RECTANGLES,
    RenderPoint,
    TextSelection,
    clamp_render_point,
    selected_text,
    selection_from_render_points,
    selection_region,
)


APPLICATION_TITLE = "PDF Ebook Reader"


@dataclass(frozen=True)
class _RenderRequest:
    generation: int
    document: PdfDocument
    page_index: int
    scale: float
    cache_key: RenderCacheKey


def _render_page_job(page: object, scale: float) -> RenderedPage:
    """Render one page away from GTK's main loop."""

    return render_page(page, scale=scale)  # type: ignore[arg-type]


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
        self._settings = SettingsStore()
        if self._settings.prune_missing():
            self._save_settings()
        self._annotations = AnnotationStore()

        self.set_title(APPLICATION_TITLE)
        self.set_default_size(1000, 750)
        saved_window_size = self._settings.window_size
        if saved_window_size is not None:
            self.set_default_size(*saved_window_size)

        self._document: PdfDocument | None = None
        self._load_generation = 0
        self._render_generation = 0
        self._render_idle_id: int | None = None
        self._render_future: Future[RenderedPage] | None = None
        self._render_request: _RenderRequest | None = None
        self._render_executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="pdf-reader-render",
        )
        self._render_cache = RenderCache()
        self._fit_timeout_id: int | None = None
        self._current_page = 0
        self._zoom_mode = "fit"
        self._manual_zoom = DEFAULT_SCALE
        self._last_render_scale = DEFAULT_SCALE
        self._reset_scroll_on_render = True
        self._file_dialog: Gtk.FileDialog | None = None
        self._closing = False
        self._annotation_mode = False
        self._page_width = 0
        self._page_height = 0
        self._annotation_marker_buttons: dict[str, Gtk.Button] = {}
        self._annotation_marker_bounds: dict[str, tuple[float, float, float, float]] = {}
        self._annotation_editor: Gtk.Popover | None = None
        self._annotation_editor_annotation_id: str | None = None
        self._annotation_note_view: Gtk.TextView | None = None
        self._annotation_confirmation: Gtk.Popover | None = None
        self._annotation_closing_editor = False
        self._annotation_feedback_timeout_id: int | None = None
        self._selection_drag_anchor: RenderPoint | None = None
        self._selection_drag_current: RenderPoint | None = None
        self._selection_drag_active = False
        self._selection_drag_claimed = False
        self._selection_pointer_viewport: tuple[float, float] | None = None
        self._selection_autoscroll_timeout_id: int | None = None
        self._text_selection: TextSelection | None = None
        self._selected_text = ""
        self._selection_highlights: tuple[HighlightRectangle, ...] = ()
        self._selection_update_idle_id: int | None = None
        self._selection_region_deferred = False
        self._selection_context_popover: Gtk.Popover | None = None
        self._selection_clipboard_provider: Gdk.ContentProvider | None = None
        self._displayed_page: object | None = None
        self._displayed_page_document: PdfDocument | None = None
        self._displayed_page_index: int | None = None
        self._displayed_render_generation: int | None = None
        self._displayed_source_width = 0.0
        self._displayed_source_height = 0.0

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
        self.connect("close-request", self._on_close_request)
        self._refresh_recent_books()
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
            .annotation-marker {
                background: @error_color;
                color: white;
                border: 2px solid white;
                border-radius: 9999px;
                font-weight: 700;
                padding: 0;
            }
            .annotation-marker:hover {
                background: shade(@error_color, 0.82);
            }
            .annotation-marker:focus {
                outline: 3px solid @theme_fg_color;
                outline-offset: 2px;
            }
            .annotation-mode-button:checked {
                background: alpha(@accent_bg_color, 0.35);
            }
            .selection-highlight {
                color: @accent_bg_color;
            }
            .annotation-prompt,
            .annotation-feedback,
            .reader-feedback {
                background: alpha(@theme_bg_color, 0.94);
                border-radius: 8px;
                padding: 8px 12px;
                margin: 12px;
            }
            .annotation-feedback {
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

        self._recent_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self._recent_box.set_size_request(420, -1)
        self._recent_box.set_margin_top(12)

        content.append(title)
        content.append(subtitle)
        content.append(open_button)
        content.append(hint)
        content.append(self._recent_box)
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

        self._page_fixed = Gtk.Fixed()
        self._page_fixed.set_halign(Gtk.Align.CENTER)
        self._page_fixed.set_valign(Gtk.Align.CENTER)
        self._page_fixed.set_can_target(True)
        self._page_fixed.put(self._page_picture, 0, 0)

        self._selection_overlay = Gtk.DrawingArea()
        self._selection_overlay.add_css_class("selection-highlight")
        self._selection_overlay.set_can_target(False)
        self._selection_overlay.set_draw_func(self._draw_selection_highlight)
        self._page_fixed.put(self._selection_overlay, 0, 0)

        page_click = Gtk.GestureClick()
        page_click.set_button(Gdk.BUTTON_PRIMARY)
        page_click.connect("pressed", self._on_page_click_pressed)
        page_click.connect("released", self._on_page_click_released)
        self._page_fixed.add_controller(page_click)

        page_drag = Gtk.GestureDrag()
        page_drag.set_button(Gdk.BUTTON_PRIMARY)
        page_drag.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        page_drag.connect("drag-begin", self._on_selection_drag_begin)
        page_drag.connect("drag-update", self._on_selection_drag_update)
        page_drag.connect("drag-end", self._on_selection_drag_end)
        self._page_fixed.add_controller(page_drag)

        context_click = Gtk.GestureClick()
        context_click.set_button(Gdk.BUTTON_SECONDARY)
        context_click.connect("released", self._on_selection_context_released)
        self._page_fixed.add_controller(context_click)

        self._page_frame = Gtk.Frame()
        self._page_frame.add_css_class("reader-page")
        self._page_frame.update_property(
            [Gtk.AccessibleProperty.DESCRIPTION],
            ["PDF text can be selected by dragging and copied with Ctrl+C."],
        )
        self._page_frame.set_child(self._page_fixed)
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

        self._page_overlay = Gtk.Overlay()
        self._page_overlay.set_child(self._page_area)
        self._render_spinner = Gtk.Spinner()
        self._render_spinner.set_halign(Gtk.Align.END)
        self._render_spinner.set_valign(Gtk.Align.START)
        self._render_spinner.set_margin_top(12)
        self._render_spinner.set_margin_end(12)
        self._render_spinner.set_visible(False)
        self._page_overlay.add_overlay(self._render_spinner)

        self._annotation_prompt = Gtk.Label()
        self._annotation_prompt.add_css_class("annotation-prompt")
        self._annotation_prompt.set_halign(Gtk.Align.CENTER)
        self._annotation_prompt.set_valign(Gtk.Align.START)
        self._annotation_prompt.set_visible(False)
        self._annotation_prompt.set_can_target(False)
        self._page_overlay.add_overlay(self._annotation_prompt)

        self._annotation_feedback = Gtk.Label()
        self._annotation_feedback.add_css_class("annotation-feedback")
        self._annotation_feedback.set_halign(Gtk.Align.CENTER)
        self._annotation_feedback.set_valign(Gtk.Align.END)
        self._annotation_feedback.set_visible(False)
        self._annotation_feedback.set_can_target(False)
        self._page_overlay.add_overlay(self._annotation_feedback)

        reader = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        reader.set_hexpand(True)
        reader.set_vexpand(True)

        self._reader_scrolled = Gtk.ScrolledWindow()
        self._reader_scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        self._reader_scrolled.set_hexpand(True)
        self._reader_scrolled.set_vexpand(True)
        self._reader_scrolled.set_child(self._page_overlay)
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
        self._set_accessible_label(self._page_entry, "Page number")
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
        self._set_accessible_label(self._zoom_out_button, "Zoom out")
        self._zoom_out_button.set_tooltip_text("Zoom out (Ctrl+- or Ctrl+mouse wheel)")
        self._zoom_out_button.connect("clicked", self._on_zoom_out_clicked)

        self._zoom_label = Gtk.Label(label="Fit")
        self._zoom_label.set_width_chars(6)
        self._zoom_label.set_xalign(0.5)

        self._zoom_in_button = Gtk.Button(label="+")
        self._set_accessible_label(self._zoom_in_button, "Zoom in")
        self._zoom_in_button.set_tooltip_text("Zoom in (Ctrl++ or Ctrl+mouse wheel)")
        self._zoom_in_button.connect("clicked", self._on_zoom_in_clicked)

        self._fit_button = Gtk.ToggleButton(label="Fit")
        self._set_accessible_label(self._fit_button, "Fit page to window")
        self._fit_button.set_tooltip_text("Fit the page to the window (Ctrl+0)")
        self._fit_button.connect("toggled", self._on_fit_toggled)

        self._annotate_button = Gtk.ToggleButton(label="Annotate")
        self._annotate_button.add_css_class("annotation-mode-button")
        self._set_accessible_label(self._annotate_button, "Annotate (A)")
        self._annotate_button.set_tooltip_text("Place an annotation (A)")
        self._annotate_button.connect("toggled", self._on_annotate_toggled)

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
            self._annotate_button,
        ):
            controls.append(child)

        return controls

    @staticmethod
    def _set_accessible_label(widget: Gtk.Widget, label: str) -> None:
        widget.update_property([Gtk.AccessibleProperty.LABEL], [label])

    def _draw_selection_highlight(
        self,
        area: Gtk.DrawingArea,
        context: object,
        _width: int,
        _height: int,
    ) -> None:
        """Paint Poppler's glyph-aligned rectangles above the page raster."""

        if not self._selection_highlights:
            return
        color = area.get_color()
        context.save()  # type: ignore[attr-defined]
        context.set_source_rgba(color.red, color.green, color.blue, 0.30)  # type: ignore[attr-defined]
        for rectangle in self._selection_highlights:
            context.rectangle(
                round(rectangle.x),
                round(rectangle.y),
                round(rectangle.width),
                round(rectangle.height),
            )  # type: ignore[attr-defined]
        context.fill()  # type: ignore[attr-defined]
        context.restore()  # type: ignore[attr-defined]

    def _update_page_cursor(self) -> None:
        if not hasattr(self, "_page_fixed"):
            return
        cursor_name: str | None = None
        if self._document is not None and self._page_width > 0 and self._page_height > 0:
            cursor_name = "crosshair" if self._annotation_mode else "text"
        try:
            self._page_fixed.set_cursor(
                Gdk.Cursor.new_from_name(cursor_name, None) if cursor_name else None
            )
        except (TypeError, GLib.Error):  # pragma: no cover - GTK version dependent
            self._page_fixed.set_cursor(None)

    def _on_page_click_pressed(
        self,
        _gesture: Gtk.GestureClick,
        _n_press: int,
        _x: float,
        _y: float,
    ) -> None:
        # The drag controller marks the sequence as claimed once GTK's drag
        # threshold is crossed.  Reset the marker at the start of every new
        # primary sequence so a completed drag cannot affect the next click.
        self._selection_drag_claimed = False

    def _on_selection_drag_begin(
        self,
        gesture: Gtk.GestureDrag,
        x: float,
        y: float,
    ) -> None:
        if (
            self._document is None
            or self._annotation_mode
            or self._focus_is_text_input()
            or self._page_width <= 0
            or self._page_height <= 0
            or self._displayed_page is None
            or self._displayed_source_width <= 0
            or self._displayed_source_height <= 0
            or self._point_hits_marker(x, y)
        ):
            gesture.set_state(Gtk.EventSequenceState.DENIED)
            return

        try:
            anchor = clamp_render_point(x, y, self._page_width, self._page_height)
        except ValueError:
            gesture.set_state(Gtk.EventSequenceState.DENIED)
            return

        self._clear_text_selection()
        self._selection_drag_anchor = anchor
        self._selection_drag_current = anchor
        self._selection_drag_active = True
        self._selection_drag_claimed = True
        self._selection_region_deferred = False
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        self._update_selection_for_pointer(anchor)

    def _on_selection_drag_update(
        self,
        gesture: Gtk.GestureDrag,
        offset_x: float,
        offset_y: float,
    ) -> None:
        if not self._selection_drag_active or self._selection_drag_anchor is None:
            return
        try:
            current = clamp_render_point(
                self._selection_drag_anchor.x + offset_x,
                self._selection_drag_anchor.y + offset_y,
                self._page_width,
                self._page_height,
            )
        except ValueError:
            return
        self._update_selection_for_pointer(current)
        self._schedule_selection_autoscroll(current)
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)

    def _on_selection_drag_end(
        self,
        _gesture: Gtk.GestureDrag,
        offset_x: float,
        offset_y: float,
    ) -> None:
        if not self._selection_drag_active or self._selection_drag_anchor is None:
            return

        try:
            current = clamp_render_point(
                self._selection_drag_anchor.x + offset_x,
                self._selection_drag_anchor.y + offset_y,
                self._page_width,
                self._page_height,
            )
        except ValueError:
            current = self._selection_drag_current

        self._selection_drag_active = False
        self._cancel_pending_selection_update()
        self._cancel_selection_autoscroll()
        if current is None:
            self._clear_text_selection()
            return
        self._update_selection_for_pointer(current, queue_update=False)
        self._selection_region_deferred = False
        self._refresh_selection_highlight()

        selection = self._text_selection
        page = self._displayed_page
        if selection is None or page is None:
            self._clear_text_selection()
            return
        text = selected_text(page, selection.rectangle)
        if not text.strip():
            self._clear_text_selection()
            return
        self._selected_text = text
        self._text_selection = selection.with_text(text)

    def _update_selection_for_pointer(
        self,
        current: RenderPoint,
        *,
        queue_update: bool = True,
    ) -> None:
        anchor = self._selection_drag_anchor
        if anchor is None or self._displayed_source_width <= 0 or self._displayed_source_height <= 0:
            return
        if self._selection_drag_current == current and self._text_selection is not None:
            return
        try:
            selection = selection_from_render_points(
                self._current_page,
                anchor,
                current,
                self._page_width,
                self._page_height,
                self._displayed_source_width,
                self._displayed_source_height,
            )
        except ValueError:
            return
        self._selection_drag_current = current
        self._text_selection = selection
        self._selected_text = ""
        if queue_update:
            self._queue_selection_region_update()

    def _queue_selection_region_update(self) -> None:
        if self._selection_region_deferred or self._selection_update_idle_id is not None:
            return
        self._selection_update_idle_id = GLib.idle_add(self._run_selection_region_update)

    def _run_selection_region_update(self) -> bool:
        self._selection_update_idle_id = None
        if self._selection_drag_active:
            self._refresh_selection_highlight()
        return GLib.SOURCE_REMOVE

    def _refresh_selection_highlight(self) -> None:
        selection = self._text_selection
        if (
            selection is None
            or self._displayed_page is None
            or self._displayed_page_document is not self._document
            or self._displayed_page_index != self._current_page
            or self._displayed_render_generation != self._render_generation
        ):
            self._selection_highlights = ()
            self._selection_overlay.queue_draw()
            return
        self._selection_highlights = selection_region(
            self._displayed_page,
            selection.rectangle,
            (self._page_width, self._page_height),
            max_rectangles=MAX_SELECTION_RECTANGLES,
        )
        self._selection_overlay.queue_draw()

    def _schedule_selection_autoscroll(self, current: RenderPoint) -> None:
        adjustment_x = self._reader_scrolled.get_hadjustment()
        adjustment_y = self._reader_scrolled.get_vadjustment()
        self._selection_pointer_viewport = (
            current.x - adjustment_x.get_value(),
            current.y - adjustment_y.get_value(),
        )
        if self._selection_autoscroll_timeout_id is None:
            self._selection_autoscroll_timeout_id = GLib.timeout_add(
                30,
                self._on_selection_autoscroll,
            )

    def _on_selection_autoscroll(self) -> bool:
        if not self._selection_drag_active or self._selection_pointer_viewport is None:
            self._selection_autoscroll_timeout_id = None
            return GLib.SOURCE_REMOVE

        pointer_x, pointer_y = self._selection_pointer_viewport
        adjustment_x = self._reader_scrolled.get_hadjustment()
        adjustment_y = self._reader_scrolled.get_vadjustment()
        viewport_width = max(1, self._reader_scrolled.get_width())
        viewport_height = max(1, self._reader_scrolled.get_height())
        edge = 32.0
        step = 24.0
        old_x = adjustment_x.get_value()
        old_y = adjustment_y.get_value()
        new_x = old_x
        new_y = old_y
        if pointer_x < edge:
            new_x = max(adjustment_x.get_lower(), old_x - step)
        elif pointer_x > viewport_width - edge:
            new_x = min(adjustment_x.get_upper() - adjustment_x.get_page_size(), old_x + step)
        if pointer_y < edge:
            new_y = max(adjustment_y.get_lower(), old_y - step)
        elif pointer_y > viewport_height - edge:
            new_y = min(adjustment_y.get_upper() - adjustment_y.get_page_size(), old_y + step)
        delta_x = new_x - old_x
        delta_y = new_y - old_y
        if delta_x or delta_y:
            adjustment_x.set_value(new_x)
            adjustment_y.set_value(new_y)
            if self._selection_drag_current is not None:
                try:
                    current = clamp_render_point(
                        self._selection_drag_current.x + delta_x,
                        self._selection_drag_current.y + delta_y,
                        self._page_width,
                        self._page_height,
                    )
                except ValueError:
                    current = self._selection_drag_current
                self._update_selection_for_pointer(current)
        return GLib.SOURCE_CONTINUE

    def _cancel_pending_selection_update(self) -> None:
        if self._selection_update_idle_id is not None:
            GLib.source_remove(self._selection_update_idle_id)
            self._selection_update_idle_id = None

    def _cancel_selection_autoscroll(self) -> None:
        if self._selection_autoscroll_timeout_id is not None:
            GLib.source_remove(self._selection_autoscroll_timeout_id)
            self._selection_autoscroll_timeout_id = None
        self._selection_pointer_viewport = None

    def _clear_text_selection(self) -> None:
        """Clear all transient PDF selection state in one place."""

        self._cancel_pending_selection_update()
        self._cancel_selection_autoscroll()
        self._close_selection_context()
        self._selection_drag_anchor = None
        self._selection_drag_current = None
        self._selection_drag_active = False
        self._selection_drag_claimed = False
        self._text_selection = None
        self._selected_text = ""
        self._selection_highlights = ()
        self._selection_region_deferred = False
        if hasattr(self, "_selection_overlay"):
            self._selection_overlay.queue_draw()

    def _point_in_selection(self, x: float, y: float) -> bool:
        return any(
            rectangle.x <= x <= rectangle.x + rectangle.width
            and rectangle.y <= y <= rectangle.y + rectangle.height
            for rectangle in self._selection_highlights
        )

    def _on_selection_context_released(
        self,
        gesture: Gtk.GestureClick,
        _n_press: int,
        x: float,
        y: float,
    ) -> None:
        if self._annotation_mode or self._point_hits_marker(x, y):
            gesture.set_state(Gtk.EventSequenceState.DENIED)
            return
        if not self._selected_text or not self._point_in_selection(x, y):
            self._clear_text_selection()
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)
            return
        self._open_selection_context(x, y)
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)

    def _open_selection_context(self, x: float, y: float) -> None:
        self._close_selection_context()
        popover = Gtk.Popover()
        popover.set_autohide(True)
        popover.set_position(Gtk.PositionType.BOTTOM)
        popover.connect("closed", self._on_selection_context_closed)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        content.set_margin_start(4)
        content.set_margin_end(4)
        content.set_margin_top(4)
        content.set_margin_bottom(4)
        copy_button = Gtk.Button(label="Copy")
        self._set_accessible_label(copy_button, "Copy selected text")
        copy_button.connect("clicked", self._on_selection_context_copy)
        clear_button = Gtk.Button(label="Clear Selection")
        self._set_accessible_label(clear_button, "Clear PDF text selection")
        clear_button.connect("clicked", self._on_selection_context_clear)
        content.append(copy_button)
        content.append(clear_button)
        popover.set_child(content)
        popover.set_parent(self._page_fixed)
        pointing_to = Gdk.Rectangle()
        pointing_to.x = int(round(x))
        pointing_to.y = int(round(y))
        pointing_to.width = 1
        pointing_to.height = 1
        popover.set_pointing_to(pointing_to)
        self._selection_context_popover = popover
        popover.popup()
        GLib.idle_add(self._focus_selection_context_copy, copy_button)

    def _focus_selection_context_copy(self, button: Gtk.Button) -> bool:
        if self._selection_context_popover is not None:
            button.grab_focus()
        return GLib.SOURCE_REMOVE

    def _on_selection_context_copy(self, _button: Gtk.Button) -> None:
        self._copy_selected_text()
        self._close_selection_context()

    def _on_selection_context_clear(self, _button: Gtk.Button) -> None:
        self._clear_text_selection()

    def _close_selection_context(self) -> None:
        popover = self._selection_context_popover
        if popover is None:
            return
        self._selection_context_popover = None
        popover.popdown()
        popover.set_child(None)
        if popover.get_parent() is not None:
            popover.unparent()

    def _on_selection_context_closed(self, popover: Gtk.Popover) -> None:
        if self._selection_context_popover is not popover:
            return
        self._selection_context_popover = None
        popover.set_child(None)
        if popover.get_parent() is not None:
            popover.unparent()

    def _copy_selected_text(self) -> bool:
        if not self._selected_text or not self._selected_text.strip():
            return False
        display = Gdk.Display.get_default()
        if display is None:
            self._show_reader_feedback("Could not access the clipboard")
            return False
        try:
            provider = Gdk.ContentProvider.new_for_value(self._selected_text)
            if not display.get_clipboard().set_content(provider):
                raise RuntimeError("The clipboard rejected the selected text")
            self._selection_clipboard_provider = provider
        except (GLib.Error, TypeError, ValueError, RuntimeError) as error:
            print(f"Unable to copy selected PDF text: {error}", file=sys.stderr)
            self._show_reader_feedback("Could not copy selected text")
            return False
        self._show_reader_feedback("Copied selected text")
        return True

    def _show_reader_feedback(self, message: str) -> None:
        self._show_annotation_feedback(message)

    def _on_annotate_toggled(self, button: Gtk.ToggleButton) -> None:
        self._set_annotation_mode(button.get_active())

    def _set_annotation_mode(self, active: bool) -> bool:
        """Keep annotation mode's state, button, prompt, and cursor together."""

        if active and self._document is None:
            active = False
        if active and self._annotation_editor is not None:
            if not self._close_annotation_editor(save=True):
                active = False
        if active:
            self._clear_text_selection()

        self._annotation_mode = active
        if self._annotate_button.get_active() != active:
            self._annotate_button.set_active(active)

        if active:
            self._update_annotation_prompt()
            self._annotation_prompt.set_visible(True)
            try:
                self._page_fixed.set_cursor(Gdk.Cursor.new_from_name("crosshair", None))
            except (TypeError, GLib.Error):  # pragma: no cover - GTK version dependent
                self._page_fixed.set_cursor(None)
        else:
            self._annotation_prompt.set_visible(False)
        self._update_page_cursor()
        return active

    def _update_annotation_prompt(self) -> None:
        if self._document is None:
            return
        next_number = self._annotations.next_number(self._document.identity)
        self._annotation_prompt.set_text(
            f"Click the page to place annotation {next_number} · Esc to cancel"
        )

    def _on_page_click_released(
        self,
        gesture: Gtk.GestureClick,
        _n_press: int,
        x: float,
        y: float,
    ) -> None:
        """Create an annotation only for a click inside the rendered page."""

        if self._annotation_mode:
            if self._document is None:
                return
            if self._point_hits_marker(x, y):
                return
            if not 0 <= x <= self._page_width or not 0 <= y <= self._page_height:
                return
            try:
                normalized_x, normalized_y = pixels_to_normalized(
                    x,
                    y,
                    self._page_width,
                    self._page_height,
                )
            except ValueError:
                return
            self._place_annotation(normalized_x, normalized_y)
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)
            return
        if self._selection_drag_claimed:
            self._selection_drag_claimed = False
            return
        if self._point_hits_marker(x, y):
            return
        self._clear_text_selection()

    def _place_annotation(self, x: float, y: float) -> None:
        document = self._document
        if document is None:
            return
        snapshot = self._annotations.get_document(document.identity)
        try:
            annotation = self._annotations.create(
                document.identity,
                page=self._current_page,
                x=x,
                y=y,
            )
            self._annotations.save()
        except Exception as error:  # pragma: no cover - filesystem/UI boundary
            self._annotations.restore_document(snapshot)
            self._report_annotation_save_error("The annotation could not be saved", error)
            return

        self._set_annotation_mode(False)
        self._refresh_annotation_markers()
        self._open_annotation_editor(annotation)

    def _refresh_annotation_markers(self) -> None:
        """Rebuild visible markers from the current rendered page dimensions."""

        if not hasattr(self, "_page_fixed"):
            return
        for button in tuple(self._annotation_marker_buttons.values()):
            self._page_fixed.remove(button)
        self._annotation_marker_buttons.clear()
        self._annotation_marker_bounds.clear()

        document = self._document
        if document is None or self._page_width <= 0 or self._page_height <= 0:
            return

        for annotation in self._annotations.annotations_for(document.identity, self._current_page):
            width, height = self._marker_dimensions(annotation.number)
            left, top = marker_top_left(
                annotation.x,
                annotation.y,
                self._page_width,
                self._page_height,
                width,
                height,
            )
            button = Gtk.Button(label=str(annotation.number))
            button.add_css_class("annotation-marker")
            button.set_size_request(width, height)
            button.set_focusable(True)
            self._update_marker_metadata(button, annotation)
            button.connect(
                "clicked",
                lambda _button, annotation_id=annotation.id: self._on_marker_clicked(annotation_id),
            )
            self._page_fixed.put(button, left, top)
            self._annotation_marker_buttons[annotation.id] = button
            self._annotation_marker_bounds[annotation.id] = (
                left,
                top,
                float(width),
                float(height),
            )

    @staticmethod
    def _marker_dimensions(number: int) -> tuple[int, int]:
        """Size markers for at least three digits without truncating larger ones."""

        return max(38, 20 + len(str(number)) * 10), 38

    def _update_marker_metadata(self, button: Gtk.Button, annotation: Annotation) -> None:
        preview = self._annotation_preview(annotation.note)
        if preview:
            label = f"Annotation {annotation.number}: {preview}"
            tooltip = label
        else:
            label = f"Annotation {annotation.number}, no notes"
            tooltip = f"Annotation {annotation.number}"
        self._set_accessible_label(button, label)
        button.set_tooltip_text(tooltip)

    @staticmethod
    def _annotation_preview(note: str) -> str:
        return " ".join(note.split())[:120]

    def _point_hits_marker(self, x: float, y: float) -> bool:
        return any(
            left <= x <= left + width and top <= y <= top + height
            for left, top, width, height in self._annotation_marker_bounds.values()
        )

    def _on_marker_clicked(self, annotation_id: str) -> None:
        document = self._document
        if document is None:
            return
        annotation = self._annotations.get(document.identity, annotation_id)
        if annotation is not None:
            self._open_annotation_editor(annotation)

    def _open_annotation_editor(self, annotation: Annotation) -> None:
        document = self._document
        marker = self._annotation_marker_buttons.get(annotation.id)
        if document is None or marker is None:
            return
        self._clear_text_selection()
        if self._annotation_editor is not None:
            if self._annotation_editor_annotation_id == annotation.id:
                if self._annotation_note_view is not None:
                    self._annotation_note_view.grab_focus()
                return
            if not self._close_annotation_editor(save=True):
                return
        self._set_annotation_mode(False)

        popover = Gtk.Popover()
        popover.set_autohide(True)
        popover.set_position(Gtk.PositionType.BOTTOM)
        popover.connect("closed", self._on_annotation_popover_closed)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        content.set_margin_start(12)
        content.set_margin_end(12)
        content.set_margin_top(12)
        content.set_margin_bottom(12)
        heading = Gtk.Label(label=f"Annotation {annotation.number}")
        heading.add_css_class("heading")
        heading.set_halign(Gtk.Align.START)

        note_view = Gtk.TextView()
        note_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        note_view.set_vexpand(True)
        note_view.set_size_request(280, 120)
        note_view.set_hexpand(True)
        self._set_accessible_label(note_view, f"Notes for annotation {annotation.number}")
        note_view.get_buffer().set_text(annotation.note)
        note_key_controller = Gtk.EventControllerKey()
        note_key_controller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        note_key_controller.connect("key-pressed", self._on_annotation_note_key_pressed)
        note_view.add_controller(note_key_controller)
        note_scroll = Gtk.ScrolledWindow()
        note_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        note_scroll.set_child(note_view)
        note_scroll.set_min_content_height(120)
        note_scroll.set_min_content_width(280)

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        actions.set_halign(Gtk.Align.END)
        done_button = Gtk.Button(label="Done")
        self._set_accessible_label(done_button, "Done editing annotation")
        done_button.add_css_class("suggested-action")
        done_button.connect("clicked", self._on_annotation_done_clicked)
        delete_button = Gtk.Button(label="Delete")
        self._set_accessible_label(delete_button, f"Delete annotation {annotation.number}")
        delete_button.connect("clicked", self._on_annotation_delete_clicked)
        actions.append(delete_button)
        actions.append(done_button)

        content.append(heading)
        content.append(note_scroll)
        content.append(actions)
        popover.set_child(content)
        popover.set_parent(marker)
        self._annotation_editor = popover
        self._annotation_editor_annotation_id = annotation.id
        self._annotation_note_view = note_view
        popover.popup()
        GLib.idle_add(self._focus_annotation_notes, popover)

    def _focus_annotation_notes(self, popover: Gtk.Popover) -> bool:
        if self._annotation_editor is popover and self._annotation_note_view is not None:
            self._annotation_note_view.grab_focus()
        return GLib.SOURCE_REMOVE

    def _on_annotation_note_key_pressed(
        self,
        _controller: Gtk.EventControllerKey,
        keyval: int,
        _keycode: int,
        state: Gdk.ModifierType,
    ) -> bool:
        if not state & Gdk.ModifierType.CONTROL_MASK:
            return False
        if keyval not in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            return False
        self._close_annotation_editor(save=True)
        return True

    def _on_annotation_popover_closed(self, popover: Gtk.Popover) -> None:
        if self._annotation_closing_editor or self._annotation_editor is not popover:
            return
        if self._save_annotation_note():
            self._destroy_annotation_editor()
        else:
            popover.popup()
            if self._annotation_note_view is not None:
                self._annotation_note_view.grab_focus()

    def _on_annotation_done_clicked(self, _button: Gtk.Button) -> None:
        self._close_annotation_editor(save=True)

    def _close_annotation_editor(self, *, save: bool) -> bool:
        if self._annotation_editor is None:
            return True
        if save and not self._save_annotation_note():
            return False
        self._destroy_annotation_editor()
        return True

    def _save_annotation_note(self) -> bool:
        document = self._document
        annotation_id = self._annotation_editor_annotation_id
        note_view = self._annotation_note_view
        if document is None or annotation_id is None or note_view is None:
            return True
        start, end = note_view.get_buffer().get_bounds()
        note = note_view.get_buffer().get_text(start, end, True)
        snapshot = self._annotations.get_document(document.identity)
        try:
            updated = self._annotations.update(document.identity, annotation_id, note=note)
            self._annotations.save()
        except Exception as error:  # pragma: no cover - filesystem/UI boundary
            self._annotations.restore_document(snapshot)
            self._report_annotation_save_error("The annotation note could not be saved", error)
            return False

        marker = self._annotation_marker_buttons.get(annotation_id)
        if marker is not None:
            self._update_marker_metadata(marker, updated)
        return True

    def _destroy_annotation_editor(self) -> None:
        popover = self._annotation_editor
        if popover is None:
            return
        self._close_annotation_confirmation()
        self._annotation_closing_editor = True
        try:
            popover.popdown()
            popover.set_child(None)
            if popover.get_parent() is not None:
                popover.unparent()
        finally:
            self._annotation_closing_editor = False
        self._annotation_editor = None
        self._annotation_editor_annotation_id = None
        self._annotation_note_view = None

    def _on_annotation_delete_clicked(self, button: Gtk.Button) -> None:
        if self._annotation_editor is None:
            return
        self._close_annotation_confirmation()
        confirmation = Gtk.Popover()
        confirmation.set_autohide(True)
        confirmation.set_position(Gtk.PositionType.BOTTOM)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        content.set_margin_start(12)
        content.set_margin_end(12)
        content.set_margin_top(12)
        content.set_margin_bottom(12)
        label = Gtk.Label(label="Delete this annotation permanently?")
        label.set_wrap(True)
        label.set_max_width_chars(32)
        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        actions.set_halign(Gtk.Align.END)
        cancel = Gtk.Button(label="Cancel")
        self._set_accessible_label(cancel, "Cancel annotation deletion")
        cancel.connect("clicked", lambda _button: self._close_annotation_confirmation())
        confirm = Gtk.Button(label="Delete")
        self._set_accessible_label(confirm, "Confirm annotation deletion")
        confirm.add_css_class("destructive-action")
        confirm.connect("clicked", self._on_annotation_delete_confirmed)
        actions.append(cancel)
        actions.append(confirm)
        content.append(label)
        content.append(actions)
        confirmation.set_child(content)
        confirmation.set_parent(button)
        self._annotation_confirmation = confirmation
        confirmation.popup()

    def _on_annotation_delete_confirmed(self, _button: Gtk.Button) -> None:
        document = self._document
        annotation_id = self._annotation_editor_annotation_id
        if document is None or annotation_id is None:
            return
        snapshot = self._annotations.get_document(document.identity)
        try:
            self._annotations.delete(document.identity, annotation_id)
            self._annotations.save()
        except Exception as error:  # pragma: no cover - filesystem/UI boundary
            self._annotations.restore_document(snapshot)
            self._close_annotation_confirmation()
            self._report_annotation_save_error("The annotation could not be deleted", error)
            return

        self._close_annotation_confirmation()
        self._destroy_annotation_editor()
        self._refresh_annotation_markers()
        self._update_annotation_prompt()

    def _close_annotation_confirmation(self) -> None:
        confirmation = self._annotation_confirmation
        if confirmation is None:
            return
        confirmation.popdown()
        confirmation.set_child(None)
        if confirmation.get_parent() is not None:
            confirmation.unparent()
        self._annotation_confirmation = None

    def _report_annotation_save_error(self, message: str, error: BaseException) -> None:
        print(f"{message}: {error}", file=sys.stderr)
        self._show_annotation_feedback(f"{message}. Try again.")

    def _show_annotation_feedback(self, message: str) -> None:
        if self._annotation_feedback_timeout_id is not None:
            GLib.source_remove(self._annotation_feedback_timeout_id)
        self._annotation_feedback.set_text(message)
        self._annotation_feedback.set_visible(True)
        self._annotation_feedback_timeout_id = GLib.timeout_add(
            4500,
            self._hide_annotation_feedback,
        )

    def _hide_annotation_feedback(self) -> bool:
        self._annotation_feedback_timeout_id = None
        self._annotation_feedback.set_visible(False)
        return GLib.SOURCE_REMOVE

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

    def _refresh_recent_books(self) -> None:
        """Rebuild the empty-state recent list from currently valid entries."""

        if not hasattr(self, "_recent_box"):
            return

        child = self._recent_box.get_first_child()
        while child is not None:
            next_child = child.get_next_sibling()
            self._recent_box.remove(child)
            child = next_child

        books = self._settings.recent_books
        if not books:
            self._recent_box.set_visible(False)
            return

        self._recent_box.set_visible(True)
        heading = Gtk.Label(label="Recent books")
        heading.add_css_class("heading")
        heading.set_halign(Gtk.Align.START)
        self._recent_box.append(heading)
        for book in books:
            button = Gtk.Button(label=Path(book.identity.path).name)
            button.set_halign(Gtk.Align.FILL)
            button.set_tooltip_text(book.identity.path)
            button.connect(
                "clicked",
                lambda _button, path=book.identity.path: self.open_file(path),
            )
            self._recent_box.append(button)

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

        if not self._close_annotation_editor(save=True):
            return
        self._clear_text_selection()
        self._set_annotation_mode(False)

        if isinstance(file, Gio.File):
            path = file.get_path()
            if path is None:
                self._show_error("Only local PDF files can be opened.")
                return
        else:
            path = str(file)

        self._persist_current_document()
        self._load_generation += 1
        self._render_generation += 1
        self._cancel_pending_render()
        self._render_cache.clear()
        generation = self._load_generation
        self._document = None
        self._current_page = 0
        self._page_width = 0
        self._page_height = 0
        self._refresh_annotation_markers()
        self._update_reader_controls()
        self._reset_scroll_on_render = True
        self._title_label.set_text(APPLICATION_TITLE)
        self.set_title(APPLICATION_TITLE)
        self._show_state("loading")

        # Let GTK paint the loading state before Poppler does synchronous file
        # parsing. Page rendering itself is dispatched to the serialized worker
        # once the document has loaded.
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
        saved = self._settings.find(document.identity)
        if saved is None:
            self._current_page = 0
            self._zoom_mode = "fit"
            self._manual_zoom = DEFAULT_SCALE
        else:
            self._current_page = max(0, min(saved.page, document.page_count - 1))
            self._zoom_mode = saved.zoom_mode
            self._manual_zoom = clamp_manual_scale(saved.manual_zoom)
        self._last_render_scale = self._manual_zoom
        self._reset_scroll_on_render = True
        self._render_cache.clear()
        self._settings.remember(
            document.identity,
            page=self._current_page,
            zoom_mode=self._zoom_mode,
            manual_zoom=self._manual_zoom,
        )
        self._save_settings()
        self._refresh_recent_books()
        self._update_reader_controls()
        self._title_label.set_text(document.title)
        self.set_title(document.title)
        self._show_state("reader")
        self._queue_render()
        return GLib.SOURCE_REMOVE

    def _show_error(self, message: str) -> None:
        self._clear_text_selection()
        if not self._close_annotation_editor(save=True):
            # Keep the editor and its unsaved text available for retry rather
            # than replacing it with an error page that would lose the note.
            self._set_annotation_mode(False)
            self._show_annotation_feedback(message)
            return
        self._set_annotation_mode(False)
        self._document = None
        self._page_width = 0
        self._page_height = 0
        self._displayed_page = None
        self._displayed_page_document = None
        self._displayed_page_index = None
        self._displayed_render_generation = None
        self._displayed_source_width = 0.0
        self._displayed_source_height = 0.0
        self._refresh_annotation_markers()
        self._update_page_cursor()
        self._update_reader_controls()
        self._render_generation += 1
        self._cancel_pending_render()
        self._render_cache.clear()
        self._render_spinner.stop()
        self._render_spinner.set_visible(False)
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
        if not self._close_annotation_editor(save=True):
            return
        self._clear_text_selection()

        clamped = max(0, min(index, self._document.page_count - 1))
        if clamped == self._current_page:
            self._update_reader_controls()
            return

        self._current_page = clamped
        self._reset_scroll_on_render = True
        self._update_reader_controls()
        self._persist_current_document()
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
        if not self._close_annotation_editor(save=True):
            return

        if self._zoom_mode == "fit":
            self._manual_zoom = clamp_manual_scale(self._last_render_scale)
        self._manual_zoom = clamp_manual_scale(self._manual_zoom * factor)
        self._zoom_mode = "manual"
        self._reset_scroll_on_render = False
        self._fit_button.set_active(False)
        self._update_reader_controls()
        self._persist_current_document()
        self._queue_render()

    def _on_fit_toggled(self, button: Gtk.ToggleButton) -> None:
        if self._document is None:
            return
        if not self._close_annotation_editor(save=True):
            if button.get_active() != (self._zoom_mode == "fit"):
                button.set_active(self._zoom_mode == "fit")
            return

        if button.get_active():
            self._zoom_mode = "fit"
        else:
            if self._zoom_mode == "fit":
                self._manual_zoom = clamp_manual_scale(self._last_render_scale)
            self._zoom_mode = "manual"
        self._reset_scroll_on_render = False
        self._update_reader_controls()
        self._persist_current_document()
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
        self._schedule_render_start()

    def _schedule_render_start(self) -> None:
        if (
            self._closing
            or self._document is None
            or self._render_idle_id is not None
            or self._render_future is not None
        ):
            return
        self._render_idle_id = GLib.idle_add(self._start_render)

    def _start_render(self) -> bool:
        self._render_idle_id = None
        document = self._document
        if document is None:
            return GLib.SOURCE_REMOVE

        generation = self._render_generation
        try:
            page = document.page(self._current_page)
            source_width, source_height = page.get_size()
            scale = self._requested_render_scale(source_width, source_height)
            cache_key = RenderCacheKey(
                document_id=document.identity.key,
                page_index=self._current_page,
                zoom_mode=self._zoom_mode,
                scale=round(scale, 6),
            )
            self._render_cache.prune_around(
                document_id=document.identity.key,
                page_index=self._current_page,
                zoom_mode=self._zoom_mode,
                scale=cache_key.scale,
            )
            cached = self._render_cache.get(cache_key)
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

        if cached is not None:
            self._apply_rendered_page(cached, scale, generation, document)
            return GLib.SOURCE_REMOVE

        request = _RenderRequest(
            generation=generation,
            document=document,
            page_index=self._current_page,
            scale=scale,
            cache_key=cache_key,
        )
        self._render_request = request
        self._render_spinner.set_visible(True)
        self._render_spinner.start()
        try:
            future = self._render_executor.submit(_render_page_job, page, scale)
        except RuntimeError:
            self._render_spinner.stop()
            self._render_spinner.set_visible(False)
            if generation == self._render_generation:
                self._show_error("The page renderer could not be started.")
            return GLib.SOURCE_REMOVE
        self._render_future = future
        future.add_done_callback(
            lambda completed: GLib.idle_add(self._finish_render, request, completed)
        )
        return GLib.SOURCE_REMOVE

    def _finish_render(
        self,
        request: _RenderRequest,
        future: Future[RenderedPage],
    ) -> bool:
        if future is not self._render_future:
            return GLib.SOURCE_REMOVE

        self._render_future = None
        self._render_request = None
        self._render_spinner.stop()
        self._render_spinner.set_visible(False)

        if self._closing:
            return GLib.SOURCE_REMOVE

        try:
            rendered = future.result()
        except CancelledError:
            self._schedule_render_start()
            return GLib.SOURCE_REMOVE
        except DocumentError as error:
            if request.generation == self._render_generation:
                self._show_error(error.user_message)
            return GLib.SOURCE_REMOVE
        except Exception as error:  # pragma: no cover - defensive UI boundary
            print(
                f"Unable to render page {request.page_index + 1} of "
                f"{request.document.path!s}: {error}",
                file=sys.stderr,
            )
            if request.generation == self._render_generation:
                self._show_error("This page could not be rendered.")
            return GLib.SOURCE_REMOVE

        if request.document is not self._document or request.generation != self._render_generation:
            self._schedule_render_start()
            return GLib.SOURCE_REMOVE

        self._render_cache.put(request.cache_key, rendered)
        self._apply_rendered_page(rendered, request.scale, request.generation, request.document)
        return GLib.SOURCE_REMOVE

    def _apply_rendered_page(
        self,
        rendered: RenderedPage,
        scale: float,
        generation: int,
        document: PdfDocument,
    ) -> None:
        if document is not self._document or generation != self._render_generation:
            self._schedule_render_start()
            return

        self._last_render_scale = scale
        self._page_picture.set_paintable(rendered.texture)
        self._page_picture.set_size_request(rendered.width, rendered.height)
        self._selection_overlay.set_size_request(rendered.width, rendered.height)
        self._page_fixed.set_size_request(rendered.width, rendered.height)
        self._page_width = rendered.width
        self._page_height = rendered.height

        try:
            displayed_page = document.page(self._current_page)
            source_width, source_height = displayed_page.get_size()
            if source_width <= 0 or source_height <= 0:
                raise ValueError("PDF page has invalid dimensions")
        except (DocumentError, GLib.Error, TypeError, ValueError):
            displayed_page = None
            source_width = 0.0
            source_height = 0.0
        self._displayed_page = displayed_page
        self._displayed_page_document = document
        self._displayed_page_index = self._current_page
        self._displayed_render_generation = generation
        self._displayed_source_width = float(source_width)
        self._displayed_source_height = float(source_height)

        if (
            self._text_selection is not None
            and (
                self._text_selection.page_index != self._current_page
                or self._displayed_page_document is not self._document
            )
        ):
            self._clear_text_selection()
        else:
            self._refresh_selection_highlight()
        if self._annotation_editor is None or self._close_annotation_editor(save=True):
            self._refresh_annotation_markers()
        self._update_reader_controls()
        self._update_page_cursor()

        if self._reset_scroll_on_render:
            self._reader_scrolled.get_vadjustment().set_value(0)
            self._reset_scroll_on_render = False

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
        self._annotate_button.set_sensitive(has_document)

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

        if self._annotation_mode:
            self._update_annotation_prompt()

    def _focus_is_text_input(self) -> bool:
        focus = self.get_focus()
        return isinstance(focus, (Gtk.Editable, Gtk.TextView))

    def _on_key_pressed(
        self,
        _controller: Gtk.EventControllerKey,
        keyval: int,
        _keycode: int,
        state: Gdk.ModifierType,
    ) -> bool:
        """Handle reader shortcuts without stealing focus from text inputs."""

        if keyval == Gdk.KEY_Escape:
            if self._annotation_editor is not None:
                self._close_annotation_editor(save=True)
                return True
            if self._annotation_mode:
                self._set_annotation_mode(False)
                return True
            if self._selected_text:
                self._clear_text_selection()
                return True
            if self.is_fullscreen():
                self.unfullscreen()
                return True
            return False

        if self._annotation_editor is not None or self._focus_is_text_input():
            return False

        control = bool(state & Gdk.ModifierType.CONTROL_MASK)
        if control and keyval in (Gdk.KEY_c, Gdk.KEY_C) and self._selected_text:
            self._copy_selected_text()
            return True
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
                self._persist_current_document()
                self._queue_render()
            return True

        if keyval == Gdk.KEY_F11:
            self._toggle_fullscreen()
            return True
        shortcut_modifiers = (
            Gdk.ModifierType.CONTROL_MASK
            | Gdk.ModifierType.ALT_MASK
            | Gdk.ModifierType.META_MASK
            | Gdk.ModifierType.SUPER_MASK
        )
        if (
            keyval in (Gdk.KEY_a, Gdk.KEY_A)
            and not state & shortcut_modifiers
            and self._document is not None
        ):
            self._set_annotation_mode(not self._annotation_mode)
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
        if (
            not state & Gdk.ModifierType.CONTROL_MASK
            or self._document is None
            or self._annotation_editor is not None
        ):
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

    def _persist_current_document(self) -> None:
        document = self._document
        if document is None:
            return
        self._settings.remember(
            document.identity,
            page=self._current_page,
            zoom_mode=self._zoom_mode,
            manual_zoom=self._manual_zoom,
        )
        self._save_settings()

    def _save_settings(self) -> None:
        try:
            self._settings.save()
        except OSError as error:  # pragma: no cover - depends on local filesystem
            print(f"Unable to save reader state: {error}", file=sys.stderr)

    def _cancel_pending_render(self) -> None:
        if self._render_idle_id is not None:
            GLib.source_remove(self._render_idle_id)
            self._render_idle_id = None
        if self._render_future is not None:
            self._render_future.cancel()

    def _on_close_request(self, _window: Gtk.Window) -> bool:
        self._clear_text_selection()
        if not self._close_annotation_editor(save=True):
            print(
                "Unable to save the open annotation before closing; closing with the editor note unchanged.",
                file=sys.stderr,
            )
            self._destroy_annotation_editor()
        self._set_annotation_mode(False)
        self._persist_current_document()
        if not self.is_fullscreen():
            self._settings.set_window_size(self.get_width(), self.get_height())
        self._save_settings()
        self._closing = True
        self._cancel_pending_render()
        self._render_executor.shutdown(wait=False, cancel_futures=True)
        return False

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
