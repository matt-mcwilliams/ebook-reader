# PDF Ebook Reader - Annotation Feature Plan

## 1. Goal

Add lightweight, local annotations to the existing GTK PDF reader.

A reader should be able to:

- Press `A` or click an annotation button to enter placement mode.
- Click a location on the visible PDF page.
- See a red numbered circle at that location.
- Optionally enter or edit notes associated with the marker.
- Reopen, edit, or delete an existing annotation.
- Return to the same PDF later and see its annotations in the same places.

Annotations are application-owned overlays. They do not alter, embed data in, or create a new copy of the PDF.

## 2. Product Decisions and Boundaries

### Planned behavior

- Annotation numbers are scoped to one document and increase across all of its pages.
- A new document starts at annotation `1`.
- Existing numbers remain stable. Deleting annotation `3` does not renumber later annotations, and the next marker uses the next unused sequence number rather than filling the gap.
- Notes are optional. Dismissing the note editor with an empty note keeps the numbered marker.
- Annotation positions are stored relative to the PDF page, so they remain aligned when the page is zoomed, fitted, resized, or reopened.
- Clicking an existing numbered circle opens its note editor.
- Deletion is available from the note editor and requires an explicit action.
- Annotations are stored locally and atomically under the user's XDG state directory.
- Annotation mode is a one-marker action: after a successful placement, the app returns to normal reading mode.

### Not included in this feature

- Writing PDF annotation objects into the source file.
- Exporting, printing, importing, or sharing annotations.
- Text highlighting, freehand drawing, shapes, or selecting text ranges.
- Cloud sync, accounts, collaboration, or cross-device state.
- Searching, filtering, or displaying a document-wide annotation sidebar.
- Reordering or manually changing annotation numbers.
- Undo/redo history beyond deleting or editing an annotation directly.

## 3. User Experience

### Entering placement mode

Add an `Annotate` toggle button to the reader controls. Its accessible label and tooltip should mention the `A` shortcut.

When the user presses `A` or clicks the button:

1. The button becomes active.
2. The page cursor changes to a crosshair when practical in GTK.
3. A short, unobtrusive prompt appears over the reading area: `Click the page to place annotation N · Esc to cancel`.
4. Navigation and zoom remain visible, but clicking outside the rendered PDF does not create an annotation.

Pressing `A` again or `Esc` cancels placement mode. The `A` shortcut must not trigger while focus is in the page-number entry, the annotation note editor, or another text input.

If no document is open, the annotation action is disabled and `A` has no effect.

### Placing an annotation

The click location is converted from rendered-widget coordinates to normalized page coordinates in the inclusive range `0.0` to `1.0` for both axes. This makes the marker independent of current zoom and output resolution.

After a valid page click:

1. Create and persist the annotation immediately.
2. Show a red circular marker centered on the clicked location with the next document-wide number in white.
3. Leave placement mode.
4. Open a small editor anchored to the new marker.
5. Focus the notes field so the user may type immediately.

The editor should provide:

- A clear heading such as `Annotation 4`.
- A multiline notes field.
- A `Done` action that saves and closes.
- A `Delete` action for removing the annotation.

Closing the editor by clicking elsewhere saves the current note. `Esc` closes the editor before it is treated as a request to leave fullscreen. Notes should preserve line breaks and ordinary Unicode text.

### Viewing annotations

- Only annotations belonging to the current PDF page are visible.
- Markers move and resize with the rendered page while retaining a readable minimum visual size.
- Marker placement is clamped so the complete circle remains visually reachable near a page edge.
- Clicking a marker opens the same editor used after creation.
- Marker tooltips should expose the annotation number and, when present, a short single-line preview of its note.
- Page navigation closes any open annotation editor and renders the new page's markers.
- Annotations remain interactive in both fit and manual zoom modes and while the page is inside the scrolled viewport.

### Deleting annotations

Deleting removes the marker and its note from local storage immediately. Because deletion is permanent and there is no undo in this feature, the editor should ask for confirmation before removal.

Deletion does not change any surviving annotation number. For example, after deleting `2` from annotations `1`, `2`, and `3`, the next annotation is `4`.

## 4. Data Model and Persistence

Create a dedicated `annotations.py` module rather than adding durable annotation content to `SettingsStore`. The existing settings file is a bounded recent-books list; annotations must not disappear when a document is no longer recent.

### Annotation record

Represent an annotation with a small immutable value object containing:

- Stable annotation ID, generated locally (UUID string).
- Positive display number.
- Zero-based page index.
- Normalized `x` and `y` page coordinates.
- Note text, which may be empty.
- Creation timestamp in UTC.
- Last-updated timestamp in UTC.

Represent a document's annotation collection with:

- Resolved absolute PDF path.
- PDF file size and modification time, matching `DocumentIdentity`.
- `next_number`, always greater than every assigned display number.
- Annotation records sorted by display number for deterministic storage and display.

### Storage

Use a separate versioned JSON file:

```text
~/.local/state/pdf-ebook-reader/annotations.json
```

Respect `$XDG_STATE_HOME` in the same way as the existing reader-state store. Save through a temporary sibling file, flush it, and replace the destination atomically.

The store should:

- Load a missing or corrupt file as an empty collection without crashing.
- Validate types, finite coordinates, page indices, IDs, timestamps, and note strings while decoding.
- Clamp normalized coordinates into the valid range.
- Ignore malformed individual records while retaining valid records.
- Enforce unique IDs and unique positive display numbers within a document.
- Return annotations by current `DocumentIdentity` and page index.
- Create, update, and delete annotations through narrow methods rather than exposing mutable storage internals.
- Keep annotation documents independently of the 10-book recent list.
- Avoid removing records merely because the PDF is temporarily missing.

The initial implementation should require the stored path, file size, and modification time to match the opened PDF. This avoids placing old markers onto changed page content. If a PDF is replaced at the same path, its previous annotations remain in storage but are not shown for the new identity.

### Suggested JSON shape

```json
{
  "version": 1,
  "documents": [
    {
      "path": "/absolute/path/book.pdf",
      "size": 123456,
      "mtime_ns": 1700000000000000000,
      "next_number": 3,
      "annotations": [
        {
          "id": "8c7d6279-794d-4667-867f-5bb4508fef8c",
          "number": 1,
          "page": 0,
          "x": 0.42,
          "y": 0.31,
          "note": "Important definition",
          "created_at": "2026-09-21T18:30:00Z",
          "updated_at": "2026-09-21T18:35:00Z"
        }
      ]
    }
  ]
}
```

## 5. Page Overlay and Coordinate Handling

The current `Gtk.Picture` is centered inside a framed page. Introduce a page-sized annotation surface that shares the rendered picture's coordinate system.

Recommended structure:

```text
Gtk.Frame (.reader-page)
└── Gtk.Fixed (exact rendered page size)
    ├── Gtk.Picture (at 0, 0)
    └── annotation marker buttons (positioned over the picture)
```

Attach a `Gtk.GestureClick` to the page-sized container for placement. Marker button clicks must be handled by the marker itself and must not bubble into creation of another annotation.

When a page render is applied:

1. Set both the picture and fixed container to the rendered page dimensions.
2. Fetch annotations for the current document identity and page.
3. Convert normalized coordinates to pixels using the actual rendered width and height.
4. Center marker widgets at those positions and clamp their widget bounds to the page.
5. Rebuild or reposition markers without changing their stored coordinates.

Store normalized coordinates from the center of the marker, not its top-left corner. Marker size is presentation state and must never affect the persisted location.

Render safety limits can make the texture's actual dimensions differ slightly from `source_size * requested_scale`; all display placement calculations must therefore use the `RenderedPage.width` and `RenderedPage.height` actually applied to the picture.

## 6. Window State and Interactions

Add explicit annotation UI state to `ReaderWindow`:

- `annotation_mode`: whether the next valid page click creates a marker.
- Current page render width and height.
- Current annotation popover/editor, if any.
- Mapping from annotation IDs to visible marker widgets.
- One `AnnotationStore` instance.

Centralize mode changes in a method that updates the button, prompt, cursor, and input behavior together.

Interaction rules:

- Opening another document, showing an error, or closing the window cancels annotation mode and closes the editor.
- Navigating pages closes the editor and refreshes markers.
- Re-rendering at a new zoom rebuilds marker positions after the new texture is applied.
- Starting annotation mode closes an existing editor.
- Opening an existing marker editor cancels placement mode.
- While the notes field has focus, ordinary reader navigation shortcuts and `A` must not fire.
- `Ctrl+O`, zoom shortcuts, fullscreen, and existing page navigation retain their current behavior when no annotation editor is active.
- Saving an annotation error should be logged and presented as a concise, recoverable UI error; it must not crash the reader or silently claim success.

## 7. Styling and Accessibility

Add minimal application CSS for:

- A red circular marker with white, high-contrast number text.
- Hover and keyboard-focus states that remain distinguishable.
- An active annotation-mode button.
- The placement prompt.

Use a fixed logical marker diameter large enough to click comfortably, with enough room for at least three digits. If annotation numbers become too wide, expand the pill/circle enough to keep the number legible rather than truncating it.

Each marker must be a keyboard-focusable GTK button with an accessible label such as `Annotation 4: Important definition`. An empty annotation should be announced as `Annotation 4, no notes`.

The editor controls need accessible labels, a predictable focus order, and full keyboard operation. Do not rely on red color alone to communicate selection or mode; the prompt and active button state provide redundant cues.

## 8. Error Cases

Handle these cases without losing unrelated annotations or destabilizing reading:

- Click outside the rendered PDF while placement mode is active: ignore it and remain in placement mode.
- Page changes before placement: keep placement mode active and update the prompt's next number for the same document.
- PDF changed on disk: do not apply annotations from the previous document identity.
- Annotation file missing or corrupt: start with no annotations and preserve a diagnostic on standard error.
- One malformed record: skip only that record.
- Atomic save failure during creation: do not show a marker that only exists in memory; report that it could not be saved.
- Save failure during note editing: keep the editor open with the user's current text so it can be retried or copied.
- Save failure during deletion: retain the marker and note.
- Very large annotation number: keep the marker readable and avoid layout overflow.
- Rapid zoom or page navigation: show markers only for the render generation and page currently displayed.
- Annotation editor open during app close: commit the current note before shutdown; if saving fails, log the failure because the window can no longer provide a recovery UI.

## 9. Implementation Phases

### Phase 1 - Annotation model and store

- Add annotation and document-collection value objects.
- Add versioned JSON decoding and encoding.
- Implement identity/page queries plus create, update, and delete operations.
- Implement atomic writes under the XDG state directory.
- Add unit tests for validation, numbering, CRUD, identity isolation, and failure-safe loading.

Deliverable: annotation records can be managed and safely persisted without GTK.

### Phase 2 - Page overlay and marker display

- Replace the page frame's direct picture child with a page-sized fixed overlay.
- Render existing page annotations as numbered marker buttons.
- Reposition markers from normalized coordinates after navigation, resize, fit, and zoom renders.
- Add marker styling, tooltips, and accessible names.

Deliverable: persisted annotations appear in the correct places at every zoom level.

### Phase 3 - Placement interaction

- Add the `Annotate` control and `A` shortcut.
- Add one-shot placement mode, prompt, crosshair cursor, and `Esc` cancellation.
- Convert valid page clicks to normalized coordinates and persist a new marker.
- Prevent marker/editor interactions from accidentally placing markers.

Deliverable: a user can place correctly numbered markers using mouse and keyboard controls.

### Phase 4 - Note editor and deletion

- Add a marker-anchored popover with multiline notes, `Done`, and `Delete`.
- Open and focus it after marker creation or marker activation.
- Save edits on `Done` or popover dismissal.
- Confirm deletion and update the page overlay only after persistence succeeds.
- Resolve keyboard shortcut and focus interactions.

Deliverable: notes can be created, reopened, edited, and deleted without disrupting reader navigation.

### Phase 5 - Integration polish

- Cover document switching, page switching, save errors, fullscreen, and shutdown.
- Update `README.md` with annotation behavior, shortcut, storage location, and local-only scope.
- Run automated tests and complete the manual verification matrix.
- Re-run the existing reader tests to prevent navigation, rendering, and settings regressions.

Deliverable: annotations behave reliably as a native part of the existing reader.

## 10. Automated Verification

Add focused tests for logic that does not require a display server:

- First annotation number is `1`; subsequent numbers increase document-wide.
- Deleting a marker does not renumber survivors or reuse its number.
- Page queries return only the requested page, sorted by number.
- Notes round-trip Unicode and multiline text.
- Create, update, and delete survive a reload from disk.
- Writes use replacement and do not leave a partial destination on failure.
- Missing, invalid, and partially malformed JSON fail safely.
- Duplicate IDs or display numbers are rejected or skipped deterministically.
- Non-finite and out-of-range coordinates cannot escape the valid normalized range.
- A replaced PDF at the same path does not receive annotations from the old identity.
- Annotation documents are not pruned when the reader's recent list is trimmed.
- Coordinate helpers correctly convert between normalized and rendered positions.
- Edge markers are visually clamped without changing their persisted coordinates.
- Existing renderer and settings tests continue to pass.

Keep coordinate conversion and validation in small pure functions so these behaviors can be tested without constructing GTK widgets.

## 11. Manual Verification

Use a multipage local PDF and verify:

- `A` and the button both enter and cancel placement mode.
- `Esc` cancels placement, then closes an editor, then leaves fullscreen in the appropriate context.
- Clicking page center and all four edges places reachable markers at the intended positions.
- Clicking the gray area outside the page does not place a marker.
- A new marker appears immediately and its note field receives focus.
- Empty, multiline, and Unicode notes save and reopen correctly.
- Existing marker clicks never create extra annotations.
- Markers remain aligned through fit mode, several manual zoom levels, scrolling, resize, and fullscreen.
- Annotations are correct across multiple pages and multiple PDFs.
- Closing and reopening the app restores all markers and notes.
- Deletion asks for confirmation, removes only the chosen marker, and preserves numbering gaps.
- Text entry does not trigger `A`, page navigation, or other single-key reader shortcuts.
- Mouse, keyboard focus traversal, accessible labels, light theme, and dark theme remain usable.
- A simulated unwritable annotation store reports failure without showing unsaved state.
- The source PDF's contents and modification time do not change.

## 12. Definition of Done

The feature is complete when:

- A reader can place a numbered red marker with `A` or the annotation button followed by a page click.
- A reader can optionally add, reopen, edit, and delete notes.
- Numbers are stable, document-scoped, and monotonically increasing.
- Marker positions remain correct across navigation, zoom, resize, scrolling, restart, and fullscreen.
- Annotations persist locally and independently of the bounded recent-book list.
- Malformed storage and write failures are handled without crashing or falsely displaying unsaved changes.
- Annotation controls and markers are keyboard accessible and screen-reader labeled.
- Existing reader behavior and tests still pass.
- The PDF is never modified and no annotation data leaves the computer.

## 13. Assumptions to Confirm Before Implementation

This plan uses the following defaults, which can be changed before implementation:

1. Annotation numbering is per document, spans all pages, never renumbers, and does not reuse deleted numbers.
2. Clicking a marker opens an anchored popover rather than a permanent sidebar.
3. An empty note still leaves a valid numbered annotation.
4. Annotations persist across restarts in local application state but are not embedded in the PDF or exported.
5. A PDF whose size or modification time changes is treated as a new document, so old annotations are retained in storage but hidden from the changed file.
6. Placement mode creates one annotation and then turns itself off.
