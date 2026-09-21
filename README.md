# PDF Ebook Reader

A small native GTK 4 PDF reader for Linux. Phase 2 provides local PDF opening and a centered first-page reading view.

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

Page navigation, zoom, scrolling controls, and drag-and-drop opening are planned
for Phase 3.

## Install as a Python project

The package metadata supports an editable install in an environment that can access the system GTK libraries:

```bash
python -m venv --system-site-packages .venv
.venv/bin/python -m pip install --editable .
.venv/bin/ebook-reader
```

The desktop launcher and per-user installation scripts are planned for Phase 5.
