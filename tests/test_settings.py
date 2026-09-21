from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from ebook_reader.settings import (
    MAX_RECENT_BOOKS,
    DocumentIdentity,
    SettingsStore,
)


class SettingsStoreTests(unittest.TestCase):
    def test_round_trip_restores_document_and_window_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            book = root / "A book with spaces.pdf"
            book.write_bytes(b"pdf")
            state_path = root / "state" / "state.json"
            identity = DocumentIdentity.from_path(book)

            store = SettingsStore(state_path)
            store.remember(identity, page=11, zoom_mode="manual", manual_zoom=9)
            store.set_window_size(1200, 800)
            store.save()

            restored = SettingsStore(state_path)
            saved = restored.find(identity)
            self.assertIsNotNone(saved)
            assert saved is not None
            self.assertEqual(saved.page, 11)
            self.assertEqual(saved.zoom_mode, "manual")
            self.assertEqual(saved.manual_zoom, 3.0)
            self.assertEqual(restored.window_size, (1200, 800))
            self.assertEqual(list((root / "state").glob("*.tmp")), [])

    def test_recent_books_are_trimmed_and_reopened_books_move_to_front(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            state_path = root / "state.json"
            store = SettingsStore(state_path)
            identities = []
            for number in range(MAX_RECENT_BOOKS + 2):
                path = root / f"book-{number}.pdf"
                path.write_bytes(str(number).encode())
                identity = DocumentIdentity.from_path(path)
                identities.append(identity)
                store.remember(identity, page=number, zoom_mode="fit", manual_zoom=1.0)

            self.assertEqual(len(store.recent_books), MAX_RECENT_BOOKS)
            store.remember(identities[2], page=99, zoom_mode="fit", manual_zoom=1.0)
            self.assertEqual(store.recent_books[0].identity, identities[2])
            self.assertEqual(store.recent_books[0].page, 99)
            self.assertEqual(len(store.recent_books), MAX_RECENT_BOOKS)

    def test_corrupt_json_falls_back_to_empty_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            state_path = Path(temporary_directory) / "state.json"
            state_path.write_text("{not valid json", encoding="utf-8")
            store = SettingsStore(state_path)
            self.assertEqual(store.recent_books, ())
            self.assertIsNone(store.window_size)

    def test_missing_recent_books_are_pruned_and_identity_changes_do_not_match(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            state_path = root / "state.json"
            present = root / "present.pdf"
            missing = root / "missing.pdf"
            present.write_bytes(b"original")
            old_identity = DocumentIdentity.from_path(present)
            missing_identity = DocumentIdentity(str(missing), 1, 1)

            store = SettingsStore(state_path)
            store.remember(old_identity, page=4, zoom_mode="fit", manual_zoom=1.0)
            store.remember(missing_identity, page=5, zoom_mode="fit", manual_zoom=1.0)
            self.assertTrue(store.prune_missing())
            self.assertEqual(len(store.recent_books), 1)

            present.write_bytes(b"replacement with a different size")
            new_identity = DocumentIdentity.from_path(present)
            self.assertNotEqual(old_identity, new_identity)
            self.assertIsNone(store.find(new_identity))

    def test_saved_json_contains_only_the_document_state_schema(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            book = root / "book.pdf"
            book.write_bytes(b"pdf")
            state_path = root / "state.json"
            store = SettingsStore(state_path)
            store.remember(DocumentIdentity.from_path(book), page=0, zoom_mode="fit", manual_zoom=1)
            store.save()
            raw = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(raw["version"], 1)
            self.assertEqual(set(raw), {"version", "recent_books", "window"})


if __name__ == "__main__":
    unittest.main()
