"""Small, atomic, per-user persistence for the PDF reader."""

from __future__ import annotations

import json
import math
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


STATE_VERSION = 1
MAX_RECENT_BOOKS = 10
MIN_MANUAL_ZOOM = 0.5
MAX_MANUAL_ZOOM = 3.0
DEFAULT_MANUAL_ZOOM = 1.0


@dataclass(frozen=True)
class DocumentIdentity:
    """The inexpensive identity used to avoid restoring stale document state."""

    path: str
    size: int
    mtime_ns: int

    @classmethod
    def from_path(cls, path: str | os.PathLike[str]) -> "DocumentIdentity":
        """Create an identity from a resolved, existing local file."""

        resolved = Path(path).expanduser().resolve(strict=True)
        metadata = resolved.stat()
        return cls(str(resolved), metadata.st_size, metadata.st_mtime_ns)

    @property
    def key(self) -> str:
        """Return a stable in-memory key for render-cache entries."""

        return f"{self.path}\0{self.size}\0{self.mtime_ns}"


@dataclass(frozen=True)
class RecentBook:
    """One recently opened document and its last reader position."""

    identity: DocumentIdentity
    page: int = 0
    zoom_mode: str = "fit"
    manual_zoom: float = DEFAULT_MANUAL_ZOOM


@dataclass
class ReaderState:
    """Decoded application state held in memory between atomic writes."""

    recent_books: list[RecentBook] = field(default_factory=list)
    window_width: int | None = None
    window_height: int | None = None


def default_state_path() -> Path:
    """Return the state path under the user's XDG state directory."""

    state_home = os.environ.get("XDG_STATE_HOME")
    base = Path(state_home).expanduser() if state_home else Path.home() / ".local" / "state"
    return base / "pdf-ebook-reader" / "state.json"


def _clamp_zoom(value: float) -> float:
    if not math.isfinite(value):
        return DEFAULT_MANUAL_ZOOM
    return min(MAX_MANUAL_ZOOM, max(MIN_MANUAL_ZOOM, value))


class SettingsStore:
    """Load, edit, and atomically save the reader's small JSON state file."""

    def __init__(self, path: str | os.PathLike[str] | None = None) -> None:
        self.path = Path(path).expanduser() if path is not None else default_state_path()
        self.state = ReaderState()
        self.load()

    @property
    def recent_books(self) -> tuple[RecentBook, ...]:
        return tuple(self.state.recent_books)

    @property
    def window_size(self) -> tuple[int, int] | None:
        if self.state.window_width is None or self.state.window_height is None:
            return None
        return self.state.window_width, self.state.window_height

    def load(self) -> ReaderState:
        """Load valid state, treating missing or corrupt files as empty state."""

        try:
            with self.path.open("r", encoding="utf-8") as state_file:
                raw = json.load(state_file)
        except (OSError, ValueError, TypeError):
            self.state = ReaderState()
            return self.state

        self.state = self._decode(raw)
        return self.state

    def find(self, identity: DocumentIdentity) -> RecentBook | None:
        """Find saved state only when path, size, and modification time match."""

        for book in self.state.recent_books:
            if book.identity == identity:
                return book
        return None

    def remember(
        self,
        identity: DocumentIdentity,
        *,
        page: int,
        zoom_mode: str,
        manual_zoom: float,
    ) -> None:
        """Move a successfully opened book to the front of the bounded list."""

        normalized_mode = zoom_mode if zoom_mode in {"fit", "manual"} else "fit"
        book = RecentBook(
            identity=identity,
            page=max(0, int(page)),
            zoom_mode=normalized_mode,
            manual_zoom=_clamp_zoom(float(manual_zoom)),
        )
        self.state.recent_books = [
            existing
            for existing in self.state.recent_books
            if existing.identity.path != identity.path
        ]
        self.state.recent_books.insert(0, book)
        del self.state.recent_books[MAX_RECENT_BOOKS:]

    def remove_path(self, path: str | os.PathLike[str]) -> bool:
        """Remove all state for a path and report whether anything changed."""

        try:
            resolved = str(Path(path).expanduser().resolve(strict=False))
        except (OSError, RuntimeError, TypeError):
            resolved = str(path)

        original_count = len(self.state.recent_books)
        self.state.recent_books = [
            book for book in self.state.recent_books if book.identity.path != resolved
        ]
        return len(self.state.recent_books) != original_count

    def prune_missing(self) -> bool:
        """Drop recent entries that no longer name a readable regular file."""

        kept: list[RecentBook] = []
        changed = False
        for book in self.state.recent_books:
            path = Path(book.identity.path)
            try:
                available = path.is_file() and os.access(path, os.R_OK)
            except OSError:
                available = False
            if available:
                kept.append(book)
            else:
                changed = True
        if changed:
            self.state.recent_books = kept
        return changed

    def set_window_size(self, width: int, height: int) -> None:
        """Remember a positive non-fullscreen window size."""

        if width > 0 and height > 0:
            self.state.window_width = int(width)
            self.state.window_height = int(height)

    def save(self) -> None:
        """Write state through a same-directory temporary file and replace."""

        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        file_descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.",
            suffix=".tmp",
            dir=self.path.parent,
            text=True,
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(file_descriptor, "w", encoding="utf-8") as state_file:
                json.dump(self._encode(), state_file, indent=2, sort_keys=True)
                state_file.write("\n")
                state_file.flush()
                os.fsync(state_file.fileno())
            os.replace(temporary_path, self.path)
            temporary_path = None
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink()
                except FileNotFoundError:
                    pass

    def _encode(self) -> dict[str, Any]:
        return {
            "version": STATE_VERSION,
            "recent_books": [
                {
                    "path": book.identity.path,
                    "size": book.identity.size,
                    "mtime_ns": book.identity.mtime_ns,
                    "page": book.page,
                    "zoom_mode": book.zoom_mode,
                    "manual_zoom": book.manual_zoom,
                }
                for book in self.state.recent_books[:MAX_RECENT_BOOKS]
            ],
            "window": {
                "width": self.state.window_width,
                "height": self.state.window_height,
            },
        }

    @staticmethod
    def _decode(raw: Any) -> ReaderState:
        if not isinstance(raw, dict):
            return ReaderState()

        recent_books: list[RecentBook] = []
        raw_books = raw.get("recent_books", [])
        if isinstance(raw_books, list):
            for item in raw_books:
                book = SettingsStore._decode_book(item)
                if book is not None and all(
                    existing.identity.path != book.identity.path for existing in recent_books
                ):
                    recent_books.append(book)
                if len(recent_books) == MAX_RECENT_BOOKS:
                    break

        window = raw.get("window")
        width: int | None = None
        height: int | None = None
        if isinstance(window, dict):
            width = SettingsStore._positive_int(window.get("width"))
            height = SettingsStore._positive_int(window.get("height"))
            if width is None or height is None:
                width = height = None

        return ReaderState(
            recent_books=recent_books,
            window_width=width,
            window_height=height,
        )

    @staticmethod
    def _decode_book(item: Any) -> RecentBook | None:
        if not isinstance(item, dict):
            return None
        path = item.get("path")
        size = item.get("size")
        mtime_ns = item.get("mtime_ns")
        if not isinstance(path, str) or not path or not Path(path).is_absolute():
            return None
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            return None
        if not isinstance(mtime_ns, int) or isinstance(mtime_ns, bool) or mtime_ns < 0:
            return None

        page = item.get("page", 0)
        if not isinstance(page, int) or isinstance(page, bool):
            page = 0
        mode = item.get("zoom_mode", "fit")
        if mode not in {"fit", "manual"}:
            mode = "fit"
        zoom = item.get("manual_zoom", DEFAULT_MANUAL_ZOOM)
        if isinstance(zoom, bool) or not isinstance(zoom, (int, float)):
            zoom = DEFAULT_MANUAL_ZOOM

        return RecentBook(
            identity=DocumentIdentity(path=path, size=size, mtime_ns=mtime_ns),
            page=max(0, page),
            zoom_mode=mode,
            manual_zoom=_clamp_zoom(float(zoom)),
        )

    @staticmethod
    def _positive_int(value: Any) -> int | None:
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            return None
        return value
