"""Local, application-owned PDF annotations.

Annotations deliberately live outside :mod:`ebook_reader.settings`.  The
reader's settings file is a bounded list of recent books, while annotation
data must remain available even after a book falls out of that list.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import sys
import tempfile
from typing import Any
from uuid import UUID, uuid4

from .settings import DocumentIdentity


ANNOTATIONS_VERSION = 1


def default_annotations_path() -> Path:
    """Return the annotation path under the user's XDG state directory."""

    state_home = os.environ.get("XDG_STATE_HOME")
    base = Path(state_home).expanduser() if state_home else Path.home() / ".local" / "state"
    return base / "pdf-ebook-reader" / "annotations.json"


@dataclass(frozen=True)
class Annotation:
    """One immutable, document-local marker and its optional note."""

    id: str
    number: int
    page: int
    x: float
    y: float
    note: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class AnnotationDocument:
    """All annotations belonging to one exact PDF file identity."""

    identity: DocumentIdentity
    next_number: int
    annotations: tuple[Annotation, ...] = ()

    def sorted(self) -> "AnnotationDocument":
        """Return a deterministic copy with annotations sorted by number."""

        return AnnotationDocument(
            identity=self.identity,
            next_number=self.next_number,
            annotations=tuple(sorted(self.annotations, key=lambda item: item.number)),
        )


def clamp_normalized(value: float) -> float:
    """Clamp a finite page coordinate to the inclusive normalized range."""

    if not math.isfinite(value):
        return 0.0
    return min(1.0, max(0.0, value))


def normalized_to_pixels(
    x: float,
    y: float,
    width: float,
    height: float,
) -> tuple[float, float]:
    """Convert normalized page coordinates to rendered-page pixels."""

    if width < 0 or height < 0 or not math.isfinite(width) or not math.isfinite(height):
        raise ValueError("Rendered page dimensions must be finite and non-negative")
    return clamp_normalized(x) * width, clamp_normalized(y) * height


def pixels_to_normalized(
    x: float,
    y: float,
    width: float,
    height: float,
) -> tuple[float, float]:
    """Convert rendered-page pixels to clamped normalized coordinates."""

    if width <= 0 or height <= 0 or not math.isfinite(width) or not math.isfinite(height):
        raise ValueError("Rendered page dimensions must be finite and positive")
    if not math.isfinite(x) or not math.isfinite(y):
        raise ValueError("Pixel coordinates must be finite")
    return clamp_normalized(x / width), clamp_normalized(y / height)


def marker_top_left(
    x: float,
    y: float,
    page_width: float,
    page_height: float,
    marker_width: float,
    marker_height: float,
) -> tuple[float, float]:
    """Return a marker's clamped top-left position for a stored center."""

    if (
        page_width < 0
        or page_height < 0
        or marker_width < 0
        or marker_height < 0
        or not all(
            math.isfinite(value)
            for value in (page_width, page_height, marker_width, marker_height)
        )
    ):
        raise ValueError("Page and marker dimensions must be finite and non-negative")

    center_x, center_y = normalized_to_pixels(x, y, page_width, page_height)
    max_left = max(0.0, page_width - marker_width)
    max_top = max(0.0, page_height - marker_height)
    return (
        min(max_left, max(0.0, center_x - marker_width / 2)),
        min(max_top, max(0.0, center_y - marker_height / 2)),
    )


class AnnotationStore:
    """Versioned JSON storage for annotations, with atomic replacement."""

    def __init__(self, path: str | os.PathLike[str] | None = None) -> None:
        self.path = Path(path).expanduser() if path is not None else default_annotations_path()
        self._documents: dict[DocumentIdentity, AnnotationDocument] = {}
        self.load()

    @property
    def documents(self) -> tuple[AnnotationDocument, ...]:
        """Return immutable document snapshots in deterministic path order."""

        return tuple(
            sorted(self._documents.values(), key=lambda document: document.identity.path)
        )

    def load(self) -> tuple[AnnotationDocument, ...]:
        """Load valid records, retaining a diagnostic when decoding fails."""

        try:
            with self.path.open("r", encoding="utf-8") as annotation_file:
                raw = json.load(annotation_file)
        except FileNotFoundError:
            self._documents = {}
            self._diagnose("Annotation file is missing; starting with no annotations", self.path)
            return self.documents
        except (OSError, ValueError, TypeError) as error:
            self._documents = {}
            self._diagnose("Unable to load annotations", error)
            return self.documents

        try:
            self._documents = self._decode_documents(raw)
        except (ValueError, TypeError) as error:
            self._documents = {}
            self._diagnose("Unable to decode annotations", error)
        return self.documents

    def get_document(self, identity: DocumentIdentity) -> AnnotationDocument:
        """Return an immutable snapshot, creating an empty collection if needed."""

        return self._documents.get(
            identity,
            AnnotationDocument(identity=identity, next_number=1),
        )

    def annotations_for(
        self,
        identity: DocumentIdentity,
        page: int,
    ) -> tuple[Annotation, ...]:
        """Return only the requested page's annotations, sorted by number."""

        if page < 0:
            return ()
        return tuple(
            annotation
            for annotation in self.get_document(identity).annotations
            if annotation.page == page
        )

    def get(self, identity: DocumentIdentity, annotation_id: str) -> Annotation | None:
        """Find one annotation by its stable ID."""

        return next(
            (
                annotation
                for annotation in self.get_document(identity).annotations
                if annotation.id == annotation_id
            ),
            None,
        )

    def next_number(self, identity: DocumentIdentity) -> int:
        """Return the next document-wide display number."""

        return self.get_document(identity).next_number

    def create(
        self,
        identity: DocumentIdentity,
        *,
        page: int,
        x: float,
        y: float,
        note: str = "",
    ) -> Annotation:
        """Create an annotation in memory; call :meth:`save` to persist it."""

        _validate_page(page)
        _validate_note(note)
        _validate_coordinate(x)
        _validate_coordinate(y)

        document = self.get_document(identity)
        now = _utc_now()
        annotation = Annotation(
            id=str(uuid4()),
            number=document.next_number,
            page=page,
            x=clamp_normalized(x),
            y=clamp_normalized(y),
            note=note,
            created_at=now,
            updated_at=now,
        )
        self._documents[identity] = AnnotationDocument(
            identity=identity,
            next_number=annotation.number + 1,
            annotations=tuple(sorted((*document.annotations, annotation), key=lambda item: item.number)),
        )
        return annotation

    def update(self, identity: DocumentIdentity, annotation_id: str, *, note: str) -> Annotation:
        """Replace one annotation's note while preserving its identity and number."""

        _validate_note(note)
        document = self.get_document(identity)
        existing = self.get(identity, annotation_id)
        if existing is None:
            raise KeyError(annotation_id)
        replacement = Annotation(
            id=existing.id,
            number=existing.number,
            page=existing.page,
            x=existing.x,
            y=existing.y,
            note=note,
            created_at=existing.created_at,
            updated_at=_utc_now(),
        )
        self._documents[identity] = AnnotationDocument(
            identity=identity,
            next_number=document.next_number,
            annotations=tuple(
                replacement if annotation.id == annotation_id else annotation
                for annotation in document.annotations
            ),
        )
        return replacement

    def delete(self, identity: DocumentIdentity, annotation_id: str) -> Annotation:
        """Remove one annotation without changing the document number sequence."""

        document = self.get_document(identity)
        existing = self.get(identity, annotation_id)
        if existing is None:
            raise KeyError(annotation_id)
        self._documents[identity] = AnnotationDocument(
            identity=identity,
            next_number=document.next_number,
            annotations=tuple(
                annotation for annotation in document.annotations if annotation.id != annotation_id
            ),
        )
        return existing

    def restore_document(self, snapshot: AnnotationDocument) -> None:
        """Restore a document snapshot after a failed atomic write."""

        self._documents[snapshot.identity] = snapshot.sorted()

    def save(self) -> None:
        """Write annotations through a flushed sibling file and atomic replace."""

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
            with os.fdopen(file_descriptor, "w", encoding="utf-8") as annotation_file:
                json.dump(self._encode(), annotation_file, indent=2, sort_keys=True)
                annotation_file.write("\n")
                annotation_file.flush()
                os.fsync(annotation_file.fileno())
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
            "version": ANNOTATIONS_VERSION,
            "documents": [
                {
                    "path": document.identity.path,
                    "size": document.identity.size,
                    "mtime_ns": document.identity.mtime_ns,
                    "next_number": document.next_number,
                    "annotations": [
                        {
                            "id": annotation.id,
                            "number": annotation.number,
                            "page": annotation.page,
                            "x": annotation.x,
                            "y": annotation.y,
                            "note": annotation.note,
                            "created_at": annotation.created_at,
                            "updated_at": annotation.updated_at,
                        }
                        for annotation in document.annotations
                    ],
                }
                for document in self.documents
            ],
        }

    @classmethod
    def _decode_documents(cls, raw: Any) -> dict[DocumentIdentity, AnnotationDocument]:
        if not isinstance(raw, dict) or raw.get("version") != ANNOTATIONS_VERSION:
            raise ValueError("unsupported annotation storage version")

        decoded: dict[DocumentIdentity, AnnotationDocument] = {}
        raw_documents = raw.get("documents")
        if not isinstance(raw_documents, list):
            return decoded

        for item in raw_documents:
            document = cls._decode_document(item)
            if document is None or document.identity in decoded:
                continue
            decoded[document.identity] = document
        return decoded

    @classmethod
    def _decode_document(cls, item: Any) -> AnnotationDocument | None:
        if not isinstance(item, dict):
            return None
        path = item.get("path")
        size = item.get("size")
        mtime_ns = item.get("mtime_ns")
        next_number = item.get("next_number")
        if (
            not isinstance(path, str)
            or not path
            or not Path(path).is_absolute()
            or not _nonnegative_int(size)
            or not _nonnegative_int(mtime_ns)
        ):
            return None
        identity = DocumentIdentity(path=path, size=size, mtime_ns=mtime_ns)
        annotations: list[Annotation] = []
        seen_ids: set[str] = set()
        seen_numbers: set[int] = set()
        raw_annotations = item.get("annotations", [])
        if isinstance(raw_annotations, list):
            for raw_annotation in raw_annotations:
                annotation = cls._decode_annotation(raw_annotation)
                if annotation is None:
                    continue
                if annotation.id in seen_ids or annotation.number in seen_numbers:
                    continue
                seen_ids.add(annotation.id)
                seen_numbers.add(annotation.number)
                annotations.append(annotation)

        highest = max((annotation.number for annotation in annotations), default=0)
        if not _positive_int(next_number):
            next_number = highest + 1
        else:
            next_number = max(next_number, highest + 1)
        return AnnotationDocument(
            identity=identity,
            next_number=next_number,
            annotations=tuple(sorted(annotations, key=lambda annotation: annotation.number)),
        )

    @staticmethod
    def _decode_annotation(item: Any) -> Annotation | None:
        if not isinstance(item, dict):
            return None
        annotation_id = item.get("id")
        number = item.get("number")
        page = item.get("page")
        x = item.get("x")
        y = item.get("y")
        note = item.get("note")
        created_at = item.get("created_at")
        updated_at = item.get("updated_at")
        if not isinstance(annotation_id, str) or not _valid_uuid(annotation_id):
            return None
        if not _positive_int(number) or not _nonnegative_int(page):
            return None
        if not _finite_number(x) or not _finite_number(y):
            return None
        if not isinstance(note, str):
            return None
        created = _normalize_timestamp(created_at)
        updated = _normalize_timestamp(updated_at)
        if created is None or updated is None:
            return None
        return Annotation(
            id=str(UUID(annotation_id)),
            number=number,
            page=page,
            x=clamp_normalized(float(x)),
            y=clamp_normalized(float(y)),
            note=note,
            created_at=created,
            updated_at=updated,
        )

    def _diagnose(self, message: str, error: BaseException) -> None:
        print(f"{message} at {self.path}: {error}", file=sys.stderr)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _normalize_timestamp(value: Any) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _valid_uuid(value: str) -> bool:
    try:
        UUID(value)
    except (ValueError, AttributeError):
        return False
    return True


def _finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _validate_coordinate(value: Any) -> None:
    if not _finite_number(value):
        raise ValueError("Annotation coordinates must be finite numbers")


def _validate_page(value: Any) -> None:
    if not _nonnegative_int(value):
        raise ValueError("Annotation page must be a non-negative integer")


def _validate_note(value: Any) -> None:
    if not isinstance(value, str):
        raise TypeError("Annotation note must be text")


def _positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _nonnegative_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0
