# Reader State Machine Refactor Plan

## 1. Goal

Refactor the reader so an explicit state machine owns its interaction mode. The
application has two states:

- `VIEW` is the default and preserves the current reading, page navigation,
  zoom, text-selection, and annotation-marker behavior.
- `ANNOTATE` makes annotation review and placement the primary interaction.

Pressing unmodified `V` enters `VIEW`; pressing unmodified `A` enters
`ANNOTATE`. These keys select a state rather than toggling it. After a new
annotation is successfully placed, the reader returns to `VIEW`.

The bottom strip is state-dependent. In `VIEW` it shows the current page and
page count as it does today. In `ANNOTATE` it shows the active annotation's
ordinal and the document's annotation count, for example `Annotation 5 of 15`,
instead of page numbers.

## 2. Product Decisions and Boundaries

### State behavior

- A newly opened document starts in `VIEW`. Reader mode is transient and is
  not restored between launches or documents.
- Re-entering the current state is idempotent: `V` in `VIEW` and `A` in
  `ANNOTATE` do not toggle or reset the state.
- The existing `Annotate` button becomes a mode selector that enters
  `ANNOTATE`; add a corresponding `View` selector so the mouse UI exposes the
  same two transitions as `V` and `A`.
- The two mode controls are mutually exclusive and always reflect the state
  machine. They must not maintain a second source of truth through independent
  toggle callbacks.
- `Esc` returns from `ANNOTATE` to `VIEW`, after preserving the existing higher
  priority for closing an annotation editor and clearing a text selection.
- Opening another document, entering an error/empty state, and closing the
  window transition to `VIEW` through the same state-machine API.

### Annotation sequence

- Annotation position in the bottom counter is a one-based ordinal in the
  document's current annotations sorted by their stable display `number`.
  It is not the annotation's display number: after deletions, annotation 7 may
  be shown as `Annotation 5 of 15`.
- Entering `ANNOTATE` selects the first annotation on the current page. If that
  page has none, select the first annotation on a later page; wrap to the first
  document annotation when necessary.
- Re-entering `ANNOTATE` without leaving it preserves the active annotation.
- If the document has no annotations, the strip reads `No annotations`; the
  user can still click the page to create the first one.
- Previous/next controls in `ANNOTATE` move through the document-wide
  annotation sequence, changing pages when needed and visually identifying
  the active marker. They do not open the note editor automatically.
- Clicking a marker makes it the active annotation before opening its existing
  editor. Deleting the active annotation selects its next neighbor, or the
  previous neighbor when the deleted item was last.
- Adding, deleting, loading, or otherwise refreshing annotations rebuilds the
  sequence by ID so stale indices never point at a different annotation.

### Existing behavior retained

- In `VIEW`, Previous/Next, the editable page field, page counts, keyboard page
  navigation, zoom, fullscreen, text selection, copying, and marker editing
  continue to work as they do now.
- Annotation markers remain visible and editable in both states.
- `ANNOTATE` keeps the existing crosshair, placement prompt, marker hit
  protection, normalized coordinate storage, save rollback, and note editor.
- Text selection is unavailable in `ANNOTATE`; entering it clears any current
  selection and closes its context menu.
- Placing an annotation still opens the note editor, but only after the mode
  transition to `VIEW` succeeds. If creation or persistence fails, remain in
  `ANNOTATE` so the placement can be retried.
- Zooming and rerendering do not change the reader state or active annotation.

### Not included

- More than the two top-level states, nested/history states, or persistence of
  the current state.
- Changing the annotation JSON schema, numbering policy, or source-PDF data.
- Reordering annotations manually, filtering annotations by page, or adding a
  separate annotations sidebar/list.
- Automatically opening every annotation while traversing the sequence.
- Undo/redo for annotation creation, editing, or deletion.

## 3. State Machine Design

Add a small UI-independent module, for example `reader_state.py`, containing:

- `ReaderMode(Enum)` with `VIEW` and `ANNOTATE`.
- A transition/event representation for `ENTER_VIEW`, `ENTER_ANNOTATE`,
  `DOCUMENT_CLEARED`, and `ANNOTATION_PLACED` (or equivalently named typed
  methods on a controller).
- A pure transition function that receives the current mode and event and
  returns the next mode. Unsupported events should fail in tests rather than
  silently create a third behavior path.
- Pure annotation-sequence helpers that order annotations, resolve an active
  annotation ID, calculate its ordinal/count, and choose a neighbor after
  deletion.

`ReaderWindow` remains responsible for GTK side effects, but it must have only
one mode field: `self._reader_mode`. Replace direct writes to
`self._annotation_mode` and scattered calls to `_set_annotation_mode()` with a
single transition gateway such as `_transition_reader(event)`.

For each accepted transition, the gateway applies all state-related effects in
one place:

1. Save or close an open annotation editor when the initiating action requires
   it; abort the transition without changing controls if saving fails.
2. Clear transient text-selection/placement data that is invalid in the target
   state.
3. Update `self._reader_mode` and repair the active annotation ID.
4. Synchronize the View/Annotate controls without recursively dispatching a
   second transition.
5. Update the bottom-strip model, placement prompt, cursor, marker emphasis,
   sensitivity, and accessibility text.

Mode queries in gestures and shortcut dispatch should compare against
`ReaderMode`; no compatibility boolean should remain after migration.

## 4. State-Dependent Bottom Strip

Build the navigation area as one stable container with two child presentations
or a `Gtk.Stack`:

```text
bottom controls
├── Previous
├── state-dependent position
│   ├── VIEW: [editable page] of [page count]
│   └── ANNOTATE: Annotation [ordinal] of [count]
├── Next
├── zoom controls
└── View / Annotate mode controls
```

- The annotation position is display-only. Do not reuse the page-entry widget
  as an editable annotation field.
- Previous/Next labels may remain stable, but their tooltips and accessible
  labels must describe pages in `VIEW` and annotations in `ANNOTATE`.
- In `VIEW`, sensitivity continues to follow page boundaries.
- In `ANNOTATE`, sensitivity follows annotation-sequence boundaries. Both are
  disabled for `No annotations`; annotation placement itself remains enabled.
- Switching states swaps the position presentation immediately, without
  triggering a render when the page does not change.
- Keep zoom controls available in either state. Mode controls are disabled when
  no document is loaded.

Centralize this in a state-aware control refresh rather than spreading mode
conditionals across button callbacks. The callbacks should dispatch semantic
commands (`previous`, `next`) whose meaning is resolved by the current state.

## 5. Annotation Navigation and Active Marker

Track the active item by annotation ID, never by list index. Derive its ordinal
from a fresh ordered tuple whenever annotations change.

When annotation traversal targets an item on another page:

1. Save/close an open annotation editor using the existing failure behavior.
2. Change `_current_page`, persist the reading position, and queue the normal
   page render.
3. Keep the target annotation ID while rendering.
4. After markers are rebuilt, give the target marker a distinct active style
   and scroll it into view if needed.

On the same page, update marker emphasis and controls without rerendering the
PDF. Active styling must coexist with hover, focus, and the existing resting
marker class, and cannot be the only indication of position; the bottom text
and accessible description provide redundant feedback.

Keyboard commands are state-dependent:

- `V` and `A` always request their named states when focus is not in a text
  input and no Ctrl/Alt/Meta/Super modifier is held.
- In `VIEW`, existing page-navigation keys retain their current behavior.
- In `ANNOTATE`, Left/Page Up/Shift+Space and Right/Page Down/Space traverse
  annotations; Home/End select the first/last annotation.
- `Enter` on the focused/active annotation may continue through the marker's
  normal activation path; annotation traversal itself does not open editors.

## 6. Placement, Editing, and Lifecycle Transitions

### Placement

- Page clicks place annotations only in `ANNOTATE`.
- Marker clicks never place a new annotation.
- On a successful create/save, set the new annotation as active long enough to
  refresh the sequence, dispatch `ANNOTATION_PLACED`, enter `VIEW`, rebuild the
  markers, and open the new annotation's editor.
- On save failure, restore the store snapshot, show the existing feedback, and
  stay in `ANNOTATE` with the prompt and crosshair intact.

### Editing and deletion

- Opening an existing marker editor does not itself change state.
- State transitions that close an editor must preserve its current retry-safe
  behavior when saving fails.
- After deletion, repair the active ID deterministically and refresh both the
  counter and markers. If no annotations remain, show `No annotations` while
  preserving the current mode.

### Document and rendering lifecycle

- Document open begins from `VIEW`; completing an asynchronous load must not
  restore a stale mode or annotation ID from the previous document.
- Page renders, zoom changes, fit resizes, dark-mode rerenders, and cache hits
  preserve mode and active annotation.
- Error, empty, and shutdown paths use a state-machine reset event before
  clearing document-owned data.
- Guard asynchronous render completion with the existing document/page/render
  generations so an old render cannot highlight an annotation in the new
  document or overwrite its controls.

## 7. Refactor Phases

### Phase 1 - Pure state and annotation-sequence model

- Add `ReaderMode`, transition events, and the pure transition function.
- Add helpers for sorted annotation IDs, entry selection, ordinal/count display,
  traversal, and deletion fallback.
- Unit-test all transition pairs, idempotent `V`/`A`, empty sequences, numbering
  gaps, current-page preference, wrapping on entry, and deleted active IDs.

Deliverable: mode and annotation-position decisions are deterministic without
constructing a GTK window.

### Phase 2 - Migrate `ReaderWindow` to the state machine

- Replace `_annotation_mode` with `_reader_mode` and a single transition
  gateway.
- Route button, keyboard, placement, error, document, and close paths through
  typed events.
- Make cursor, prompt, text-selection eligibility, and placement gestures read
  the new state.
- Preserve editor-save failure semantics across attempted transitions.

Deliverable: current view/annotation behavior operates from one source of
truth, with `V` and `A` selecting states rather than toggling.

### Phase 3 - State-dependent controls and annotation traversal

- Add mutually exclusive View/Annotate controls.
- Add the bottom `Gtk.Stack` (or equivalent) for page versus annotation
  position.
- Dispatch Previous/Next and navigation keys according to state.
- Track active annotation IDs across page renders and annotation mutations.
- Add active-marker styling, scrolling, tooltips, and accessible descriptions.

Deliverable: `VIEW` shows page position; `ANNOTATE` shows and navigates
`Annotation N of M`.

### Phase 4 - Placement transition and lifecycle hardening

- Return to `VIEW` only after successful annotation persistence.
- Refresh/order annotations before opening the new note editor.
- Repair active state after deletion and reset it across document/error/close
  boundaries.
- Verify text selection, zoom, rendering, fullscreen, and editor interactions
  in both states.

Deliverable: all success, failure, and asynchronous paths preserve valid state
and synchronized UI.

### Phase 5 - Documentation and verification

- Update `README.md` with `V`/`A`, state-specific bottom controls, annotation
  traversal, placement return behavior, and `Esc` semantics.
- Run all automated tests plus the manual matrix below.
- Reinstall the local desktop copy if needed for realistic GTK verification,
  without committing generated installation artifacts.

Deliverable: behavior is documented and regression-tested from both checkout
and desktop launches.

## 8. Automated Verification

Add tests for:

- Initial/default mode and every defined transition.
- Idempotent `V` in `VIEW` and `A` in `ANNOTATE`.
- Modified shortcuts and focused text inputs do not change mode.
- State controls cannot become simultaneously active or desynchronize from the
  state machine.
- `Esc` priority with an editor, annotation mode, selection, and fullscreen.
- Annotation ordering by display number when numbers have gaps.
- Entry selection on the current page, later-page fallback, wrap, and the empty
  sequence.
- Previous/next and Home/End boundary behavior across pages.
- Counter text such as `Annotation 5 of 15` uses ordinal/count, not display
  number.
- Same-page traversal avoids rendering; cross-page traversal queues the normal
  generation-guarded render.
- Successful placement transitions to `VIEW`; failed persistence remains in
  `ANNOTATE` and restores annotation data.
- Active-annotation repair after deleting first, middle, last, and only items.
- Opening a new document and error/close paths clear stale annotation IDs and
  return to `VIEW`.
- Existing annotation storage, text selection, rendering, settings, and
  navigation tests continue to pass.

Keep transition and sequence logic pure so most new coverage remains
display-server independent. Add narrow GTK integration tests only for control
synchronization and command routing where practical.

## 9. Manual Verification

With a PDF containing annotations on multiple pages, including gaps caused by
deletion, verify:

- A fresh document starts in `VIEW` with page position shown at the bottom.
- `A` enters `ANNOTATE`; `V` returns to `VIEW`; repeated presses do not toggle.
- View/Annotate controls, prompt, cursor, and bottom presentation always agree.
- Entering `ANNOTATE` selects the documented current-page/fallback annotation
  and shows the correct ordinal/count despite display-number gaps.
- Previous/Next and all state-dependent keyboard commands traverse annotations,
  change pages when needed, emphasize the correct marker, and stop at bounds.
- A document with no annotations shows `No annotations` and still permits
  placement.
- Clicking blank page space in `ANNOTATE` creates exactly one annotation,
  returns to `VIEW`, restores page controls, and opens the new note editor.
- A simulated annotation save failure leaves the app in `ANNOTATE` and allows a
  retry without consuming a number or creating a marker.
- Clicking/editing/deleting markers works in both states; deleting the active
  marker selects the intended neighbor and updates the count.
- Text selection works in `VIEW`, is cleared/disabled in `ANNOTATE`, and works
  again after returning to `VIEW`.
- Page navigation in `VIEW`, annotation navigation in `ANNOTATE`, zoom, fit,
  dark mode, scrolling, fullscreen, and rerenders do not desynchronize state.
- Opening another PDF, load errors, and closing reset mode-owned state without
  stale markers, counters, prompts, or asynchronous render effects.
- Screen-reader labels and keyboard focus communicate the active mode and
  annotation position without relying on color alone.

## 10. Definition of Done

- The explicit two-state machine is the sole source of truth for reader mode.
- `VIEW` is the default; unmodified `V` and `A` select their respective states.
- Page position is shown in `VIEW`; annotation ordinal/count is shown in
  `ANNOTATE`.
- Annotation traversal is deterministic across pages, numbering gaps,
  mutations, and rerenders.
- A successfully placed annotation returns the reader to `VIEW`; failure keeps
  the user safely in `ANNOTATE`.
- Existing reading, text-selection, rendering, persistence, and annotation
  editing behavior remains intact.
- Controls, cursor, prompt, markers, keyboard routing, and accessibility state
  cannot drift apart.
- Automated tests and the manual verification matrix pass, and the README
  describes the final behavior.
