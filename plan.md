# PDF Ebook Reader - Cursor Text Selection Plan

## 1. Goal

Add native-feeling mouse text selection to the currently displayed PDF page.

A reader should be able to:

- Move the pointer over selectable page content and see an I-beam cursor.
- Press and drag from a start point to an end point to select PDF text.
- See a translucent highlight that follows the drag and matches Poppler's text layout.
- Copy the selected text with `Ctrl+C` or a context-menu `Copy` action.
- Click elsewhere, press `Esc`, navigate, or open another document to clear the selection.

Selection is transient UI state. It does not modify the PDF and is not stored between pages or application sessions.

## 2. Product Decisions and Boundaries

### Planned behavior

- Selection is enabled by default whenever a PDF page is displayed and annotation placement mode is off.
- A primary-button drag selects text on the current page using Poppler's glyph-level selection semantics.
- Drag direction is preserved. The pointer-down position is the selection start and the current/release position is the selection end.
- Drag points are clamped to the rendered page and converted to PDF page coordinates before calling Poppler.
- The highlight updates while dragging, then remains visible after release if Poppler returns non-empty text.
- `Ctrl+C` copies the selected text to the standard desktop clipboard.
- Right-clicking a non-empty selection opens a small menu with `Copy` and `Clear Selection`.
- Starting a new drag replaces the old selection. A normal primary click outside an annotation marker clears it.
- Selection is limited to one page because the reader displays one page at a time.
- Existing numbered annotation markers remain clickable above the selection highlight.
- Entering annotation placement mode clears the selection and temporarily gives the page click/drag gesture to annotation placement.

### Not included in this feature

- OCR for scanned or image-only PDFs.
- Cross-page selection.
- Keyboard caret navigation, Shift+Arrow selection, or a persistent selectable text widget.
- Double-click word selection or triple-click line/paragraph selection in the first version.
- Search, highlighting saved as an annotation, comments attached to selected text, or selection persistence.
- Rich-text, HTML, image, or layout-preserving clipboard formats.
- Changing Poppler's reading order or repairing malformed/missing PDF text maps.

## 3. User Experience

### Starting and extending a selection

When a rendered page is ready and annotation mode is inactive, the page uses an I-beam cursor. On primary-button press:

1. Close an open annotation editor only if its current contents can be saved.
2. Ignore the gesture if it began on an annotation marker.
3. Clear the previous selection and record the clamped pointer position as the anchor.
4. As the pointer moves, convert the anchor and current point into a directional Poppler selection rectangle.
5. Ask Poppler for the selected region and repaint the translucent highlight.
6. Auto-scroll the existing `Gtk.ScrolledWindow` when the pointer is near a viewport edge, while keeping coordinates relative to the page.

Do not commit a selection until the drag exceeds GTK's drag threshold. This keeps a simple click available for clearing the current selection and prevents tiny accidental highlights.

On release, ask Poppler for the selected text using the same rectangle and `Poppler.SelectionStyle.GLYPH`. If the result is empty or whitespace-only, clear the selection. Otherwise, retain both the rectangle and exact returned text.

### Visual feedback

- Draw selection regions with the theme accent color at roughly 30% opacity so black text stays legible.
- Draw the highlight above the rasterized PDF and below annotation marker buttons.
- Use Poppler's returned selected region rather than painting the raw drag rectangle. This makes multi-line selection follow glyph/line geometry and avoids highlighting empty margins.
- Recompute the region after each completed page render so the same in-progress or completed selection stays aligned if zoom changes.
- Expose a short non-modal message after copying, such as `Copied selected text`, using the existing feedback overlay generalized beyond annotation errors.

### Copying and clearing

- `Ctrl+C` copies only when a non-empty PDF selection exists and focus is not inside `Gtk.Entry` or `Gtk.TextView`. Text widgets keep their normal copy behavior.
- Use a UTF-8 string `Gdk.ContentProvider` with the display clipboard; do not create a custom clipboard format.
- Right-click inside the selected region opens `Copy` and `Clear Selection`. Right-click outside it may clear the selection but should not show a disabled menu.
- `Esc` clears a PDF selection after handling an annotation editor or annotation placement mode, and before leaving fullscreen.
- A primary click without a drag clears the selection unless it activates an annotation marker.
- Page navigation, document changes, errors, and window close clear the selection.
- Zoom and window resize preserve the selection rectangle and redraw it at the new render dimensions.

## 4. Selection Model and Poppler Boundary

Add a focused `text_selection.py` module so coordinate math and selection state are testable without constructing the GTK window.

### Value objects

Represent a selection with immutable values:

- Zero-based page index.
- Anchor point in PDF page coordinates.
- Current/end point in PDF page coordinates.
- Extracted text, empty while a drag is still in progress.

Represent highlight rectangles in rendered-page pixel coordinates. They are derived display state and are never persisted.

### Coordinate conversion

Keep three coordinate spaces explicit:

1. Widget/render coordinates from GTK pointer events.
2. PDF page coordinates in points, as expected by `Poppler.Rectangle`.
3. Highlight-region coordinates returned by Poppler at a requested pixels-per-point scale.

Conversion helpers should:

- Reject non-finite or non-positive page dimensions.
- Clamp pointer input to the inclusive rendered page bounds.
- Calculate independent X and Y ratios from the actual `RenderedPage.width`/`height` and `Poppler.Page.get_size()` results.
- Preserve anchor/end ordering when creating the Poppler rectangle instead of sorting its corners; Poppler documents the rectangle as the selection's start and end points.
- Use a representative pixels-per-point scale for `get_selected_region()` and compensate for any tiny X/Y difference introduced by integer render dimensions.
- Round only when producing Cairo drawing rectangles, not when retaining the PDF-space selection.

### Poppler adapter

Wrap page selection calls in narrow helpers:

- `selection_region(page, rectangle, rendered_size)` calls `page.get_selected_region(scale, Poppler.SelectionStyle.GLYPH, rectangle)`, enumerates the returned Cairo region, and converts its rectangles into rendered coordinates.
- `selected_text(page, rectangle)` calls `page.get_selected_text(Poppler.SelectionStyle.GLYPH, rectangle)` and returns a safe string.

The adapter should catch `GLib.Error`, unexpected `None`, and malformed region data. A failure clears or leaves the prior valid selection as appropriate, logs a diagnostic, and never crashes the reader.

Do not use the deprecated `get_selection_region()` list API. Do not use the newer `render_transparent_selection()` API so the implementation continues to work with older Poppler releases that support `get_selected_region()`.

## 5. Page Overlay and Gesture Integration

Extend the current page stack without replacing its raster renderer:

```text
Gtk.Frame (.reader-page)
└── Gtk.Fixed (exact rendered page size; owns page gestures)
    ├── Gtk.Picture (PDF raster at 0, 0)
    ├── Gtk.DrawingArea (non-targetable selection highlight at 0, 0)
    └── annotation marker buttons (interactive and visually on top)
```

The drawing area should match the actual rendered width and height, use `set_can_target(False)`, and paint the current list of highlight rectangles in its draw callback.

Add a `Gtk.GestureDrag` for primary-button selection and a secondary-button `Gtk.GestureClick` for the context menu. Keep the existing primary click gesture for annotation placement and selection clearing. Define explicit gesture arbitration:

- In annotation mode, annotation placement owns a valid primary click and text selection does not start.
- Outside annotation mode, a drag starting inside a marker's tracked bounds is denied so the marker button receives the event.
- Once movement passes the drag threshold, the selection drag claims the event sequence and annotation placement/click clearing cannot run.
- A released click that was not claimed as a drag clears the selection.
- Context-menu activation must not start or alter a drag selection.

Do not infer marker hits only from event propagation. Continue using the existing marker-bound map as a defensive check because marker widgets and the fixed container both have controllers.

## 6. Window State and Lifecycle

Add explicit selection state to `ReaderWindow`:

- Drag anchor and current widget coordinates while a gesture is active.
- Current immutable PDF-space selection, or `None`.
- Current selected text.
- Current rendered highlight rectangles.
- Whether the active pointer sequence crossed the drag threshold.
- Pending throttled highlight update source ID, if live updates are coalesced.
- Current selection context popover, if any.

Centralize cleanup in `_clear_text_selection()` so it resets state, closes the context menu, cancels any pending update, queues one highlight redraw, and refreshes copy-action sensitivity.

Lifecycle rules:

- Opening a document, changing pages, entering an error state, or closing the window clears selection.
- Entering annotation mode clears selection before changing the cursor to a crosshair.
- Opening an annotation editor clears selection; saving failures still leave the editor and its text intact.
- Zooming, fit-mode resize, and cached renders retain a selection only when the document and page are unchanged; `_apply_rendered_page()` recalculates its highlight geometry.
- A render-generation mismatch must never apply stale highlight geometry to a newer page.
- The page cursor is `crosshair` in annotation mode and `text` during normal selection mode; empty/loading/error states use the default cursor.
- Disable PDF selection behavior while a text input owns focus.

## 7. Clipboard and Actions

Add one window-level copy action or equivalent centralized handler and route `Ctrl+C` through it only when the PDF selection owns the command.

Clipboard behavior:

- Preserve Poppler's returned Unicode text and line breaks.
- Copy plain text via `Gdk.ContentProvider.new_for_value(...)` and `Gdk.Clipboard.set_content(...)` using the current display clipboard.
- Keep the content provider alive for as long as GTK requires; store it on the window if the binding does not retain it reliably.
- Treat clipboard rejection as a recoverable error and show feedback rather than clearing the selection.
- Do not intercept `Ctrl+C` when focus is in the page-number entry or annotation note editor.

The context popover should be anchored near the pointer release/right-click point, remain inside the page/window, be keyboard navigable, and expose accessible labels for both actions.

## 8. Accessibility and Input Details

The raster page plus drawn overlay is not itself a full accessible text surface. Within the scope of this feature:

- Give the page container an accessible description explaining that text can be selected by dragging and copied with `Ctrl+C`.
- Announce successful copy through the visible feedback label and, where GTK permits, an accessible status role/live update.
- Ensure `Copy` and `Clear Selection` in the context menu are keyboard focusable.
- Maintain visible high-contrast focus indication on annotation markers above the selection.
- Do not communicate selection solely through cursor shape; the persistent highlight is the redundant visual state.

Full screen-reader traversal of PDF text requires a semantic text accessibility layer and is outside this iteration.

## 9. Performance and Failure Handling

- Coalesce drag-motion highlight calculations to at most once per GTK frame/idle cycle. Always process the final release coordinates synchronously before extracting text.
- Never rerender the PDF texture to update a selection; repaint only the lightweight drawing area.
- Cache the current page object/source dimensions for the displayed render, scoped by document, page, and render generation.
- Skip duplicate highlight work when the clamped endpoint has not changed.
- Put a reasonable upper bound on the number of highlight rectangles accepted from Poppler; if exceeded, fall back to a single update on release rather than freezing the UI.
- If Poppler reports no text or no region, clear the transient drag without showing an error.
- If text extraction succeeds but region generation fails, keep the text copyable and show no misleading highlight only if the failure is logged; retry region generation after the next render.
- If the PDF has an image-only page or a broken text map, selection simply produces no result. The UI must not imply that OCR is occurring.
- Cancel pending drag/auto-scroll work on page change, document change, error, and shutdown.

## 10. Implementation Phases

### Phase 1 - Pure selection model and Poppler adapter

- Add immutable selection/point/rectangle values and coordinate conversion helpers.
- Add wrappers for `get_selected_region()` and `get_selected_text()`.
- Add tests for forward/reverse drags, clamping, render/source scaling, invalid dimensions, Unicode, line breaks, empty text, and Poppler failures.

Deliverable: a directional PDF selection can produce safe text and render-aligned highlight rectangles without GTK window state.

### Phase 2 - Highlight overlay and pointer drag

- Add the non-targetable `Gtk.DrawingArea` between the page picture and annotation markers.
- Add primary drag handling, threshold behavior, live coalesced region updates, and final extraction.
- Set the normal page cursor to an I-beam and draw a theme-aware translucent selection.
- Add edge auto-scroll and ensure page-relative coordinates remain correct while scrolling.

Deliverable: dragging across text visibly selects the intended content on the current page.

### Phase 3 - Copy, clear, and context menu

- Add `Ctrl+C` clipboard support without stealing copy from existing text inputs.
- Add the right-click `Copy`/`Clear Selection` popover.
- Implement click and `Esc` clearing plus copy/error feedback.
- Add accessible labels and focus behavior for selection actions.

Deliverable: selected PDF text can be copied as plain Unicode text and dismissed predictably.

### Phase 4 - Annotation and render lifecycle integration

- Define gesture ownership with annotation placement and marker buttons.
- Clear selection when opening an annotation editor or entering annotation mode.
- Preserve and redraw selection through same-page zoom, fit resize, and render-cache hits.
- Clear stale state on navigation, new documents, errors, and close.

Deliverable: text selection coexists with every existing reader and annotation interaction.

### Phase 5 - Documentation and full verification

- Update `README.md` with drag-to-select, `Ctrl+C`, context menu behavior, and the no-OCR/single-page limits.
- Run all automated tests and the manual matrix below.
- Verify dependency checking still reflects the minimum Poppler API actually used.
- Reinstall the local desktop copy if needed for realistic GTK testing, without committing generated installation artifacts.

Deliverable: the feature is documented, regression-tested, and behaves consistently from a checkout and desktop launch.

## 11. Automated Verification

Add unit and integration-focused tests for:

- Widget-to-PDF conversion at the center and every boundary.
- Independent X/Y conversion when actual integer render dimensions differ slightly from the ideal scale.
- Forward, backward, upward, and downward drags preserve start/end semantics.
- Pointer positions outside the page clamp safely.
- Zero-sized, negative, non-finite, and extreme dimensions are rejected.
- Poppler region rectangles convert back to rendered coordinates correctly.
- Empty/whitespace extraction does not create a completed selection.
- Unicode, ligatures where Poppler exposes them, and multiline text are returned without application-side rewriting.
- A generated multiline PDF fixture selects expected text and produces a non-empty region at multiple scales.
- Region/extraction exceptions are contained.
- Selection cleanup cancels pending updates and removes all derived highlight rectangles.
- `Ctrl+C` dispatch chooses a focused GTK text input before the PDF selection.
- Annotation marker hit detection prevents selection from claiming the sequence.
- Existing annotation, renderer, settings, navigation, and dependency tests continue to pass.

Keep geometry, dispatch decisions, and Poppler result normalization in small functions so most coverage remains display-server independent.

## 12. Manual Verification

Use PDFs containing single-column text, multiple columns, mixed font sizes, rotated text, Unicode, and at least one scanned/image-only page. Verify:

- The pointer is an I-beam over a normal rendered page and a crosshair only in annotation mode.
- Dragging left-to-right, right-to-left, top-to-bottom, and bottom-to-top selects the intended text.
- Selection highlights only Poppler-selected glyph/line areas, not the entire drag box.
- Live highlighting remains responsive during a long drag and while edge auto-scrolling.
- `Ctrl+C` pastes the expected Unicode text and line breaks into another application.
- Copy from the page-number entry and annotation note editor still copies their own text.
- Right-click actions work with mouse and keyboard and appear near the selected area.
- A simple click and `Esc` clear selection according to the documented priority order.
- Zoom in/out, fit resize, scrolling, fullscreen, and render-cache hits keep the highlight aligned.
- Page changes and opening another PDF clear selection; returning to the page does not restore it.
- Starting annotation mode clears selection, marker clicks open their editors, and dragging from a marker does not create a PDF selection.
- Selection drag does not accidentally place an annotation, and annotation placement does not begin text selection.
- Multi-column and rotated text follow Poppler's reading/selection behavior without application crashes.
- Image-only pages simply yield no selection and no misleading error.
- Very large pages and long selections do not freeze page navigation or shutdown.
- Light and dark themes keep text legible under the highlight.

## 13. Definition of Done

The feature is complete when:

- A reader can drag across selectable PDF text and see an accurate persistent highlight.
- `Ctrl+C` and the context menu copy Poppler's selected plain text to the desktop clipboard.
- Forward and reverse, single-line and multi-line selections work on the current page.
- Selection remains aligned through scrolling, zoom, fit resize, fullscreen, and cached rerenders.
- Selection and annotation gestures coexist without accidental markers, selections, or lost annotation notes.
- Navigation, document changes, errors, and `Esc` clear selection predictably.
- Scanned/image-only pages fail quietly without suggesting OCR support.
- Poppler and clipboard failures do not crash the reader or corrupt existing state.
- Automated tests cover coordinate conversion, Poppler adaptation, cleanup, and interaction dispatch.
- The README and manual verification matrix describe the final behavior.
- The PDF is never modified and selection state is never persisted or uploaded.
