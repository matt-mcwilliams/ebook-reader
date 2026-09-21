# Minimal PDF EBook Reader - Implementation Plan

## 1. Goal

Build a small desktop application for this Omarchy/Arch Linux computer that opens a local PDF and presents it as a calm, book-like reading experience.

The first version should:

- Run as a normal desktop window, not as a website or local web server.
- Open PDFs from the file picker or from the command line/file manager.
- Display one page at a time in a centered, distraction-free reading area.
- Provide only the controls needed to read comfortably.
- Keep documents on the computer; nothing is uploaded or sent over the network.
- Remember the last page for recently opened books.
- Integrate with the application launcher and follow the current system theme.

## 2. Deliberate MVP Boundaries

### Included

- Open one local PDF at a time.
- Drag and drop a PDF onto the window.
- Single-page reading view.
- Previous/next-page navigation.
- Direct page-number entry.
- Keyboard and mouse navigation.
- Zoom in, zoom out, and fit-to-window behavior.
- Current page and total page count.
- Lightweight recent-book and reading-position persistence.
- Friendly empty, loading, and error states.
- Per-user desktop installation and launcher entry.

### Not included in the first version

- PDF editing, annotations, highlights, or bookmarks.
- Search, table-of-contents extraction, thumbnails, or two-page spreads.
- EPUB/MOBI support.
- Accounts, cloud sync, networking, or telemetry.
- A library/database of imported files.
- Copying PDFs into an application-managed folder.
- Digital-rights-management support.
- Hyprland window rules, global keybindings, or edits to Omarchy configuration.

These can be considered only after the basic reader is stable and pleasant to use.

## 3. Technical Direction

Use a native GTK 4 application written in Python, with Poppler GLib for PDF loading and page rendering.

This choice fits the current machine because the required runtime components are already installed:

- Python 3.14
- GTK 4
- PyGObject
- Poppler GLib

It also avoids bundling Chromium/Electron, running a local server, or adding a large JavaScript dependency tree.

### Core libraries

- `Gtk`/`Gdk`/`Gio` via PyGObject: window, controls, dialogs, keyboard input, drag and drop, and application lifecycle.
- `Poppler` via GObject Introspection: PDF parsing, metadata, page count, page dimensions, and Cairo rendering.
- `cairo`: render a Poppler page to an image surface shown by GTK.
- Python standard library: paths, JSON persistence, URI handling, and command-line arguments.

### Compatibility assumptions

- Primary target: this x86_64 Omarchy/Arch Linux installation under Wayland/Hyprland.
- The application should use GTK's normal Wayland support and must not force XWayland.
- Packaging for other Linux distributions, macOS, or Windows is outside the MVP.
- Password-protected PDFs should produce a clear unsupported/protected-document message in the MVP rather than a broken view.

## 4. User Experience

### Empty state

On launch, show a restrained empty screen with:

- Application name.
- A single prominent `Open PDF` button.
- A short hint that a PDF can also be dropped onto the window.
- A compact recent-books list only when prior books exist.

### Reading state

The main window should contain:

1. A slim GTK header bar.
2. The document title, falling back to the file name.
3. An `Open` button.
4. A centered page canvas on a neutral background.
5. A small bottom control bar with previous, page position, next, zoom, and fit controls.

The PDF page should look like a physical page: centered, with modest spacing and a subtle shadow or border. Controls should stay visually quiet so the document remains the focus.

### Minimal controls

- Previous page.
- Page field formatted conceptually as `12 / 240`.
- Next page.
- Zoom out.
- Zoom percentage or fit indicator.
- Zoom in.
- Fit-to-window toggle/action.

Buttons must have accessible labels and tooltips even if they use icons.

### Keyboard and mouse behavior

| Input | Action |
| --- | --- |
| `Ctrl+O` | Open a PDF |
| `Right`, `Page Down`, or `Space` | Next page |
| `Left`, `Page Up`, or `Shift+Space` | Previous page |
| `Home` | First page |
| `End` | Last page |
| `Ctrl++` | Zoom in |
| `Ctrl+-` | Zoom out |
| `Ctrl+0` | Fit page to the available window |
| `F11` | Toggle fullscreen |
| `Esc` | Leave fullscreen or dismiss a transient UI element |
| Mouse wheel | Scroll a zoomed page vertically |
| `Ctrl` + mouse wheel | Adjust zoom |

Navigation actions should clamp safely at the first and last pages. When focus is inside the page-number field, typing must take priority over page shortcuts.

### Window behavior

- Start at a comfortable default size, approximately 1000 x 750 logical pixels.
- Respect manual resize and fullscreen.
- Refit the page after a resize when fit mode is enabled.
- Allow scrolling when the rendered page is larger than the viewport.
- Use the active GTK light/dark appearance automatically.
- Do not add custom Hyprland window rules for the MVP.

## 5. Application Architecture

Keep the project small and separate only the concerns that benefit from being independently testable.

Proposed structure:

```text
ebook-reader/
├── plan.md
├── README.md
├── pyproject.toml
├── src/
│   └── ebook_reader/
│       ├── __init__.py
│       ├── __main__.py
│       ├── application.py
│       ├── window.py
│       ├── document.py
│       ├── renderer.py
│       └── settings.py
├── data/
│   ├── io.github.local.PdfEbookReader.desktop
│   ├── io.github.local.PdfEbookReader.metainfo.xml
│   └── icons/
├── scripts/
│   ├── install-local.sh
│   └── uninstall-local.sh
└── tests/
    ├── test_document.py
    ├── test_settings.py
    └── fixtures/
```

### Responsibilities

- `application.py`: `Gtk.Application`, lifecycle, command-line/open-file handling, actions, and single-instance behavior.
- `window.py`: window layout, empty/reader/error states, signals, input shortcuts, and presentation state.
- `document.py`: safe Poppler document wrapper, metadata, page count, page lookup, and document identity.
- `renderer.py`: page-size calculation, zoom/fit math, Cairo rendering, and the small render cache.
- `settings.py`: recent files, last page, zoom preference, and window-state persistence.
- `__main__.py`: a minimal executable entry point.

Avoid a framework or dependency-injection layer. GTK signals and a few clearly owned objects are sufficient for this application.

## 6. State Model

Keep a small explicit state object in the window/controller:

- Current document path and identity.
- Poppler document handle.
- Current zero-based page index.
- Total page count.
- Zoom mode: `fit` or `manual`.
- Manual zoom value.
- Loading/rendering status.
- Last render request generation number.

UI controls derive their sensitivity and labels from this state. For example, `Previous` is disabled on page 1, and both navigation buttons are disabled when no document is open.

### Persisted data

Use a small JSON file under the XDG state directory, such as:

```text
~/.local/state/pdf-ebook-reader/state.json
```

Store only:

- A bounded list of up to 10 recent absolute file paths.
- Last page for each recent file.
- Last manual zoom or fit mode.
- Last non-fullscreen window size.

Write state atomically through a temporary sibling file followed by replacement. Missing, moved, or unreadable recent files should be ignored gracefully and removed from the list when encountered.

For the MVP, identify a document by its resolved path plus simple file metadata such as size and modification time. This is inexpensive and prevents restoring an obviously stale page position after a file is replaced.

## 7. PDF Loading and Rendering

### Open flow

1. Receive a path from the file chooser, drag and drop, command line, or desktop `Open With` action.
2. Validate that it exists, is a regular readable file, and has a PDF content type or `.pdf` extension.
3. Convert the local path to a properly escaped file URI for Poppler.
4. Open the document and read page count/title metadata.
5. Restore the saved page and zoom mode when available.
6. Render the requested page and switch to the reading state.
7. Add the file to recents only after it opens successfully.

### Render sizing

- Read the source page dimensions from Poppler.
- In fit mode, calculate the largest scale that fits both viewport width and height while preserving aspect ratio and leaving a small margin.
- In manual mode, use a bounded scale, initially supporting roughly 50% to 300%.
- Account for the GTK scale factor so output remains sharp on HiDPI displays.
- Render the page at the required pixel size into a Cairo image surface.
- Present the surface through a GTK drawing widget or texture without rescaling it a second time.

### Responsiveness

- Do not render repeatedly for every resize event; debounce fit-mode redraws briefly.
- Render on a worker thread if normal PDFs cause visible input blocking, then marshal only the completed texture/UI update back to GTK's main thread.
- Use a monotonically increasing render generation ID so a slow old render cannot replace a newer requested page.
- Cache at most the current page and adjacent pages at the active zoom level.
- Set a strict cache limit based on approximate surface memory, and discard old surfaces first.
- Show a subtle spinner for renders that are not effectively immediate.

Threading should be introduced only around isolated rendering work. GTK widgets must be accessed only from the main thread. If Poppler object thread-safety is uncertain in practice, create the page/render work in a serialized worker rather than rendering concurrently.

## 8. Errors and Edge Cases

Handle each case without crashing or leaving controls in an inconsistent state:

- Cancelled file chooser: do nothing.
- Missing or unreadable file: explain that the file cannot be accessed.
- Invalid or corrupt PDF: show a concise error with an option to choose another file.
- Password-protected PDF: explain that protected documents are not supported yet.
- Zero-page or malformed document: reject it with a useful message.
- Very large page: cap render scale/pixel count to avoid excessive memory use.
- File moved after appearing in recents: remove or disable that recent entry.
- Document deleted while open: keep the already-open document readable where possible, but report failures on later page access.
- Rapid page changes: display only the latest requested page.
- Application closed during rendering: cancel/ignore the outstanding result cleanly.

Do not display raw stack traces in the UI. Development logs can go to standard error with enough detail to diagnose failures.

## 9. Local Desktop Integration

The app should be runnable during development with a project command such as:

```bash
python -m ebook_reader [optional-file.pdf]
```

The local install script should install only into the current user's directories:

- Executable/launcher wrapper: `~/.local/bin/`
- Desktop entry: `~/.local/share/applications/`
- App icon: `~/.local/share/icons/hicolor/`
- Application files or virtual environment: an appropriate directory under `~/.local/`

The `.desktop` entry should:

- Launch the application normally under Wayland.
- Declare `application/pdf` support.
- Accept a local PDF path/URI via `%U` or `%F`.
- Include a stable application ID matching `Gtk.Application`.
- Appear in Omarchy's normal application launcher through the standard desktop-entry mechanism.

The installer must not use `sudo`, edit `~/.config/hypr/`, edit `~/.config/omarchy/`, or write into `/usr/share/omarchy/`. The uninstall script should remove only files created by this project's installer and leave saved reading state alone unless explicitly asked to purge it.

## 10. Implementation Phases

### Phase 1 - Project skeleton and dependency check

- Add Python project metadata and the module entry point.
- Confirm the GObject namespaces for GTK 4 and Poppler load correctly.
- Add a minimal `Gtk.Application` window.
- Document the development run command.
- Add a startup dependency check with an actionable message for missing system packages.

Deliverable: an empty native window starts reliably from the terminal.

### Phase 2 - Open and display a PDF

- Build the file chooser with a PDF filter.
- Add command-line/open-file handling.
- Implement the Poppler document wrapper.
- Render the first page into the centered viewport.
- Add empty, loading, reader, and error states.

Deliverable: a normal local PDF can be selected and read on page 1.

### Phase 3 - Reading controls

- Add previous/next controls and page-number entry.
- Add keyboard shortcuts and boundary behavior.
- Implement manual zoom and fit-to-window.
- Add scrolling and fullscreen.
- Add drag-and-drop opening.

Deliverable: the entire PDF can be navigated comfortably using mouse or keyboard.

### Phase 4 - Persistence and resilience

- Save and restore last page and zoom mode.
- Add the bounded recent-books list.
- Add render generation/cancellation behavior and a small cache.
- Add pixel/memory safety limits.
- Polish error messages and malformed-file handling.

Deliverable: reopening a book returns to the prior reading position, and rapid/large-file interactions remain stable.

### Phase 5 - Desktop installation and polish

- Add the application icon, desktop entry, metadata, and per-user installer/uninstaller.
- Verify launcher discovery and `Open With` behavior.
- Check light and dark appearances under the current Omarchy theme.
- Review focus order, accessible labels, tooltips, and window resizing.
- Keep custom CSS minimal and limited to the page/background presentation.

Deliverable: the reader launches like a normal installed desktop application on this Omarchy machine.

## 11. Verification Plan

### Automated tests

- Page clamping and page-number conversion.
- Fit-scale calculations across portrait, landscape, and unusually shaped pages.
- Zoom minimum/maximum enforcement.
- State-file read/write, atomic replacement, corrupt JSON fallback, and recent-list trimming.
- Document identity changes when file size or modification time changes.
- URI/path handling for spaces and non-ASCII characters.
- Error mapping from Poppler/GLib errors to user-facing messages.

### Manual PDF fixtures

Test with locally generated or freely redistributable fixtures covering:

- A short portrait text document.
- A landscape document.
- Mixed page sizes and rotations.
- Image-heavy/high-resolution pages.
- A long document for position restoration.
- A Unicode file name and a path containing spaces.
- A corrupt/non-PDF file renamed to `.pdf`.
- A password-protected PDF.

Do not commit private sample books to the repository.

### Desktop checks on Omarchy

- Launch from the terminal with no file.
- Launch from the terminal with a PDF path.
- Launch from the application menu.
- Open a PDF through the file manager's `Open With` flow.
- Drag a PDF from the file manager onto the window.
- Confirm native Wayland operation.
- Confirm resize, fullscreen, focus, and keyboard behavior under Hyprland.
- Confirm the UI remains readable in both light and dark system appearances.
- Confirm no Omarchy or Hyprland configuration was changed.

### Performance checks

- Page navigation should feel immediate after a neighboring page is cached.
- The window must remain responsive while rendering a complex page.
- Repeated navigation should not cause unbounded memory growth.
- Fit-mode resize should settle on one sharp render rather than a cascade of stale renders.

## 12. Definition of Done

The MVP is complete when:

- A user can install it without root access and find it in the Omarchy application launcher.
- It opens ordinary local PDFs through the app, command line, drag and drop, and `Open With`.
- It presents a clean one-page reading view with working navigation, page entry, zoom, fit, and fullscreen.
- It restores the last page of recent documents after restarting.
- Invalid, missing, large, and protected files fail safely with understandable messages.
- It stays responsive during normal navigation and does not exhibit obvious memory growth.
- Core state and sizing logic passes automated tests.
- Manual checks pass on this Wayland/Hyprland desktop.
- No file leaves the computer, no network service is started, and no Omarchy/Hyprland configuration is modified.

## 13. Possible Follow-ups After the MVP

Only consider these after real use shows they are valuable:

- Text search.
- Outline/table-of-contents navigation.
- User bookmarks.
- Optional two-page spread.
- Sepia/background reading themes.
- Password entry for protected PDFs.
- Thumbnail sidebar.
- Packaging as an Arch package or Flatpak.
- EPUB support through a separate document backend.

