from __future__ import annotations

from dataclasses import dataclass
import unittest

from ebook_reader.reader_state import (
    ReaderEvent,
    ReaderMode,
    annotation_after_delete,
    annotation_index,
    annotation_position,
    initial_annotation_id,
    neighboring_annotation_id,
    ordered_annotations,
    transition_mode,
)


@dataclass(frozen=True)
class _Annotation:
    id: str
    number: int
    page: int


class ReaderStateTests(unittest.TestCase):
    def test_mode_events_are_explicit_and_idempotent(self) -> None:
        self.assertEqual(
            transition_mode(ReaderMode.VIEW, ReaderEvent.ENTER_VIEW),
            ReaderMode.VIEW,
        )
        self.assertEqual(
            transition_mode(ReaderMode.VIEW, ReaderEvent.ENTER_ANNOTATE),
            ReaderMode.ANNOTATE,
        )
        self.assertEqual(
            transition_mode(ReaderMode.ANNOTATE, ReaderEvent.ENTER_ANNOTATE),
            ReaderMode.ANNOTATE,
        )
        self.assertEqual(
            transition_mode(ReaderMode.ANNOTATE, ReaderEvent.ANNOTATION_PLACED),
            ReaderMode.VIEW,
        )
        self.assertEqual(
            transition_mode(ReaderMode.ANNOTATE, ReaderEvent.DOCUMENT_CLEARED),
            ReaderMode.VIEW,
        )

    def test_order_position_and_gaps_use_display_number_order(self) -> None:
        annotations = (
            _Annotation("third", 7, 2),
            _Annotation("first", 2, 0),
            _Annotation("second", 5, 1),
        )
        self.assertEqual([item.id for item in ordered_annotations(annotations)], ["first", "second", "third"])
        self.assertEqual(annotation_index(annotations, "third"), 2)
        self.assertEqual(annotation_position(annotations, "second"), (2, 3))
        self.assertIsNone(annotation_position(annotations, "missing"))

    def test_entry_prefers_current_page_then_later_then_wraps(self) -> None:
        annotations = (
            _Annotation("first", 1, 0),
            _Annotation("second", 2, 4),
            _Annotation("third", 3, 8),
        )
        self.assertEqual(initial_annotation_id(annotations, 4), "second")
        self.assertEqual(initial_annotation_id(annotations, 2), "second")
        self.assertEqual(initial_annotation_id(annotations, 9), "first")
        self.assertIsNone(initial_annotation_id((), 0))

    def test_navigation_is_bounded_and_deletion_falls_forward(self) -> None:
        annotations = (
            _Annotation("first", 1, 0),
            _Annotation("second", 2, 1),
            _Annotation("third", 3, 2),
        )
        self.assertEqual(neighboring_annotation_id(annotations, "second", -1), "first")
        self.assertEqual(neighboring_annotation_id(annotations, "second", 1), "third")
        self.assertIsNone(neighboring_annotation_id(annotations, "first", -1))
        self.assertIsNone(neighboring_annotation_id(annotations, "third", 1))
        remaining = (annotations[0], annotations[2])
        self.assertEqual(annotation_after_delete(remaining, 1), "third")
        self.assertEqual(annotation_after_delete((annotations[0],), 2), "first")
        self.assertIsNone(annotation_after_delete((), 0))


if __name__ == "__main__":
    unittest.main()
