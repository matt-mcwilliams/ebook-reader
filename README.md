# PDF Ebook Reader

A small native GTK 4 PDF reader for Linux. The project is being built in phases; Phase 1 provides the project skeleton, dependency check, and an empty application window.

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

The startup dependency check can be run without opening a window:

```bash
PYTHONPATH=src python -m ebook_reader --check-dependencies
```

The optional PDF argument and reader controls will be added in Phase 2 and later.

## Install as a Python project

The package metadata supports an editable install in an environment that can access the system GTK libraries:

```bash
python -m venv --system-site-packages .venv
.venv/bin/python -m pip install --editable .
.venv/bin/ebook-reader
```

The desktop launcher and per-user installation scripts are planned for Phase 5.
