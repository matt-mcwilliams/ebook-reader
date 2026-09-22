"""Pure reader-mode and annotation-sequence decisions.

The GTK window owns side effects; this module keeps mode transitions and
annotation ordering deterministic and straightforward to test without a
display server.
"""

from __future__ import annotations

from enum import Enum
from typing import Iterable, Protocol, TypeVar


class ReaderMode(Enum):
    """The top-level interaction mode of the reader."""

    VIEW = "view"
    ANNOTATE = "annotate"


class ReaderEvent(Enum):
    """Events accepted by the reader mode state machine."""

    ENTER_VIEW = "enter-view"
    ENTER_ANNOTATE = "enter-annotate"
    DOCUMENT_CLEARED = "document-cleared"
    ANNOTATION_PLACED = "annotation-placed"


def transition_mode(mode: ReaderMode, event: ReaderEvent) -> ReaderMode:
    """Return the next mode for *event*.

    Enter events are intentionally idempotent. Document lifecycle events and a
    successful placement always leave the reader in the normal viewing state.
    """

    if event in (
        ReaderEvent.ENTER_VIEW,
        ReaderEvent.DOCUMENT_CLEARED,
        ReaderEvent.ANNOTATION_PLACED,
    ):
        return ReaderMode.VIEW
    if event is ReaderEvent.ENTER_ANNOTATE:
        return ReaderMode.ANNOTATE
    raise ValueError(f"Unsupported reader event: {event!r}")


class AnnotationLike(Protocol):
    """The fields needed by annotation sequence helpers."""

    id: str
    number: int
    page: int


AnnotationT = TypeVar("AnnotationT", bound=AnnotationLike)


def ordered_annotations(annotations: Iterable[AnnotationT]) -> tuple[AnnotationT, ...]:
    """Return annotations in stable display-number order."""

    return tuple(sorted(annotations, key=lambda item: (item.number, item.id)))


def annotation_index(
    annotations: Iterable[AnnotationT],
    annotation_id: str | None,
) -> int | None:
    """Return the zero-based sequence index for an ID, if it is present."""

    if annotation_id is None:
        return None
    for index, annotation in enumerate(ordered_annotations(annotations)):
        if annotation.id == annotation_id:
            return index
    return None


def annotation_position(
    annotations: Iterable[AnnotationT],
    annotation_id: str | None,
) -> tuple[int, int] | None:
    """Return ``(one_based_ordinal, count)`` for an active annotation."""

    ordered = ordered_annotations(annotations)
    index = annotation_index(ordered, annotation_id)
    if index is None:
        return None
    return index + 1, len(ordered)


def initial_annotation_id(
    annotations: Iterable[AnnotationT],
    current_page: int,
) -> str | None:
    """Choose the first current-page annotation, then a later one, then wrap."""

    ordered = ordered_annotations(annotations)
    if not ordered:
        return None
    for annotation in ordered:
        if annotation.page == current_page:
            return annotation.id
    for annotation in ordered:
        if annotation.page > current_page:
            return annotation.id
    return ordered[0].id


def neighboring_annotation_id(
    annotations: Iterable[AnnotationT],
    annotation_id: str | None,
    step: int,
) -> str | None:
    """Return a bounded neighboring ID, or the first item if none is active."""

    if step == 0:
        return annotation_id if annotation_index(annotations, annotation_id) is not None else None
    ordered = ordered_annotations(annotations)
    if not ordered:
        return None
    index = annotation_index(ordered, annotation_id)
    if index is None:
        return ordered[0].id
    target = index + (1 if step > 0 else -1)
    if not 0 <= target < len(ordered):
        return None
    return ordered[target].id


def annotation_after_delete(
    remaining: Iterable[AnnotationT],
    deleted_index: int,
) -> str | None:
    """Choose the next item after deletion, falling back to the previous item."""

    ordered = ordered_annotations(remaining)
    if not ordered:
        return None
    return ordered[min(max(deleted_index, 0), len(ordered) - 1)].id
