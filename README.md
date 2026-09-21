# PDF Ebook Reader

A small native GTK 4 PDF reader for Linux. Phase 3 provides local PDF opening,
page navigation, fit/manual zoom, scrolling, fullscreen, and drag-and-drop opening.

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

## Install as a Python project

The package metadata supports an editable install in an environment that can access the system GTK libraries:

```bash
python -m venv --system-site-packages .venv
.venv/bin/python -m pip install --editable .
.venv/bin/ebook-reader
```

The desktop launcher and per-user installation scripts are planned for Phase 5.
