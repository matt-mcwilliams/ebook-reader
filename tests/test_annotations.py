from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ebook_reader.annotations import (
    AnnotationStore,
    marker_top_left,
    normalized_to_pixels,
    pixels_to_normalized,
)
from ebook_reader.settings import DocumentIdentity


class AnnotationStoreTests(unittest.TestCase):
    def _identity(self, root: Path, name: str = "book.pdf") -> DocumentIdentity:
        path = root / name
        path.write_bytes(b"pdf")
        return DocumentIdentity.from_path(path)

    def test_numbering_is_document_wide_and_deleted_numbers_are_not_reused(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            identity = self._identity(root)
            store = AnnotationStore(root / "annotations.json")
            first = store.create(identity, page=4, x=0.1, y=0.2)
            second = store.create(identity, page=0, x=0.3, y=0.4)
            self.assertEqual((first.number, second.number), (1, 2))
            store.delete(identity, first.id)
            third = store.create(identity, page=1, x=0.5, y=0.6)
            self.assertEqual(third.number, 3)
            self.assertEqual([item.number for item in store.annotations_for(identity, 0)], [2])
            self.assertEqual([item.number for item in store.annotations_for(identity, 1)], [3])

    def test_notes_and_crud_round_trip_through_disk(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            identity = self._identity(root)
            path = root / "annotations.json"
            store = AnnotationStore(path)
            annotation = store.create(
                identity,
                page=0,
                x=1.4,
                y=-0.4,
                note="Line one\nπ and 你好",
            )
            self.assertEqual((annotation.x, annotation.y), (1.0, 0.0))
            store.save()

            restored = AnnotationStore(path)
            loaded = restored.get(identity, annotation.id)
            self.assertIsNotNone(loaded)
            assert loaded is not None
            self.assertEqual(loaded.note, "Line one\nπ and 你好")
            restored.update(identity, annotation.id, note="Edited\n✓")
            restored.save()
            restored.delete(identity, annotation.id)
            restored.save()

            self.assertIsNone(AnnotationStore(path).get(identity, annotation.id))
            self.assertEqual(AnnotationStore(path).next_number(identity), 2)

    def test_identity_changes_isolate_old_annotations(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            identity = self._identity(root)
            path = root / "annotations.json"
            store = AnnotationStore(path)
            annotation = store.create(identity, page=0, x=0.5, y=0.5)
            store.save()

            (root / "book.pdf").write_bytes(b"replacement")
            replacement = DocumentIdentity.from_path(root / "book.pdf")
            restored = AnnotationStore(path)
            self.assertIsNone(restored.get(replacement, annotation.id))
            self.assertEqual(restored.next_number(replacement), 1)
            self.assertEqual(restored.next_number(identity), 2)

    def test_malformed_records_are_skipped_without_losing_valid_records(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            identity = self._identity(root)
            path = root / "annotations.json"
            store = AnnotationStore(path)
            valid = store.create(identity, page=0, x=0.2, y=0.3, note="valid")
            store.save()
            raw = json.loads(path.read_text(encoding="utf-8"))
            records = raw["documents"][0]["annotations"]
            records.extend(
                [
                    {**records[0], "id": "not-a-uuid"},
                    {**records[0], "number": records[0]["number"]},
                    {**records[0], "x": float("nan")},
                    {**records[0], "page": -1},
                ]
            )
            path.write_text(json.dumps(raw), encoding="utf-8")

            restored = AnnotationStore(path)
            self.assertEqual(restored.annotations_for(identity, 0), (valid,))

    def test_atomic_replace_failure_leaves_previous_destination_intact(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            identity = self._identity(root)
            path = root / "annotations.json"
            store = AnnotationStore(path)
            store.create(identity, page=0, x=0.1, y=0.1)
            store.save()
            original = path.read_bytes()
            store.create(identity, page=1, x=0.8, y=0.9)

            with patch("ebook_reader.annotations.os.replace", side_effect=OSError("read-only")):
                with self.assertRaises(OSError):
                    store.save()
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(list(root.glob("*.tmp")), [])

    def test_missing_or_corrupt_files_load_as_empty(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "annotations.json"
            self.assertEqual(AnnotationStore(path).documents, ())
            path.write_text("{not json", encoding="utf-8")
            self.assertEqual(AnnotationStore(path).documents, ())


class CoordinateTests(unittest.TestCase):
    def test_coordinates_use_actual_rendered_dimensions_and_clamp_edges(self) -> None:
        self.assertEqual(normalized_to_pixels(0.5, 0.25, 801, 1201), (400.5, 300.25))
        self.assertEqual(pixels_to_normalized(400.5, 300.25, 801, 1201), (0.5, 0.25))
        self.assertEqual(marker_top_left(0.0, 0.0, 801, 1201, 40, 40), (0.0, 0.0))
        self.assertEqual(marker_top_left(1.0, 1.0, 801, 1201, 40, 40), (761.0, 1161.0))


if __name__ == "__main__":
    unittest.main()
