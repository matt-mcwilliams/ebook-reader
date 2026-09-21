# PDF Ebook Reader

A small native GTK 4 PDF reader for Linux. Phase 4 provides local PDF opening,
page navigation, fit/manual zoom, scrolling, fullscreen, drag-and-drop opening,
recent-book persistence, reading-position restoration, and bounded background
rendering.

## Requirements

The application uses the system versions of these libraries:

- Python 3.13 or newer
- PyGObject with GTK 4 introspection data
- Poppler GLib introspection data
- Pycairo

On Arch Linux, install the runtime packages with:

```bash
sudo pacman -S python python-gobject python-cairo gtk4 poppler-glib
```

## Run from a checkout

From the repository root:

```bash
PYTHONPATH=src python -m ebook_reader
```

Open a PDF directly from the terminal:

```bash
PYTHONPATH=src python -m ebook_reader /path/to/book.pdf
```

You can also use the `Open PDF` button inside the application. The chooser
defaults to PDF files, and invalid, unreadable, and protected documents show a
recoverable error state.

The startup dependency check can be run without opening a window:

```bash
PYTHONPATH=src python -m ebook_reader --check-dependencies
```

While reading, use `Previous`/`Next` or the page field to navigate. Keyboard
shortcuts include `Left`/`Right`, `Page Up`/`Page Down`, `Space`, `Home`, and
`End`; `Ctrl+0` fits the page, `Ctrl++`/`Ctrl+-` adjust zoom, `F11` toggles
fullscreen, and `Ctrl+O` opens another PDF. Hold `Ctrl` while using the mouse
wheel to change zoom. A PDF can also be dropped onto the window.

The reader stores a small amount of per-user state in
`~/.local/state/pdf-ebook-reader/state.json` (or `$XDG_STATE_HOME`): recent
absolute paths, matching file metadata, the last page and zoom mode, and the
last non-fullscreen window size. PDFs remain in their original locations and
are never copied or uploaded.

## Install as a Python project

The package metadata supports an editable install in an environment that can access the system GTK libraries:

```bash
python -m venv --system-site-packages .venv
.venv/bin/python -m pip install --editable .
.venv/bin/ebook-reader
```

## Install as a desktop application

Install the current checkout for the current user without root access:

```bash
./scripts/install-local.sh
```

This installs the launcher in `~/.local/bin`, the desktop entry in
`~/.local/share/applications`, the icon in the user hicolor icon theme, and
the application files in `~/.local/share/pdf-ebook-reader`. It registers
`application/pdf` for the normal desktop “Open With” flow. The installer does
not use `sudo` or modify Hyprland/Omarchy configuration.

Remove only the installed application files with:

```bash
./scripts/uninstall-local.sh
```

The uninstall script deliberately leaves reading state in
`~/.local/state/pdf-ebook-reader` untouched.
