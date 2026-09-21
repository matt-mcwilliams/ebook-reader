"""Small, local-only Poppler document wrapper."""

from __future__ import annotations

import os
from pathlib import Path

import gi

gi.require_version("Gio", "2.0")
gi.require_version("Poppler", "0.18")
from gi.repository import Gio, GLib, Poppler  # noqa: E402

from .settings import DocumentIdentity


class DocumentError(Exception):
    """Base class for errors that can be shown directly to the user."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.user_message = message


class DocumentAccessError(DocumentError):
    """The requested path is not a readable local file."""


class ProtectedDocumentError(DocumentError):
    """The PDF requires a password or uses unsupported document security."""


class InvalidDocumentError(DocumentError):
    """The file is not a usable PDF document."""


def _looks_protected(error: GLib.Error) -> bool:
    """Recognize Poppler's varying encrypted-document error messages."""

    message = str(error).lower()
    return any(word in message for word in ("password", "encrypted", "security", "permission"))


def _has_pdf_content_type(path: Path) -> bool:
    """Use GIO's content sniffing for PDFs without a .pdf suffix."""

    try:
        info = Gio.File.new_for_path(str(path)).query_info(
            "standard::content-type",
            Gio.FileQueryInfoFlags.NONE,
            None,
        )
    except GLib.Error:
        return False

    content_type = info.get_content_type()
    return bool(content_type and Gio.content_type_is_a(content_type, "application/pdf"))


class PdfDocument:
    """A successfully opened local PDF and the reader's document API."""

    def __init__(self, *, path: Path, document: Poppler.Document) -> None:
        self.path = path
        self._document = document

        try:
            self.identity = DocumentIdentity.from_path(path)
        except OSError as error:
            raise DocumentAccessError("The selected PDF is no longer available.") from error

        try:
            self.page_count = document.get_n_pages()
        except GLib.Error as error:
            raise InvalidDocumentError("The PDF's page count could not be read.") from error

        if self.page_count < 1:
            raise InvalidDocumentError("This PDF does not contain any pages.")

        title = document.get_title()
        self.title = title.strip() if title and title.strip() else path.name

    @classmethod
    def open(cls, candidate: str | os.PathLike[str]) -> "PdfDocument":
        """Validate and open a local path through Poppler."""

        try:
            path = Path(candidate).expanduser().resolve(strict=True)
        except (OSError, RuntimeError, TypeError) as error:
            raise DocumentAccessError("The selected file could not be found.") from error

        if not path.is_file():
            raise DocumentAccessError("The selected path is not a regular file.")
        if not os.access(path, os.R_OK):
            raise DocumentAccessError("The selected PDF cannot be read.")
        if path.suffix.lower() != ".pdf" and not _has_pdf_content_type(path):
            raise InvalidDocumentError("Please choose a PDF file.")

        uri = Gio.File.new_for_path(str(path)).get_uri()
        try:
            document = Poppler.Document.new_from_file(uri, None)
        except GLib.Error as error:
            if _looks_protected(error):
                raise ProtectedDocumentError(
                    "This PDF is password-protected or uses unsupported document security."
                ) from error
            raise InvalidDocumentError("This file could not be opened as a valid PDF.") from error

        if document is None:
            raise InvalidDocumentError("This file could not be opened as a valid PDF.")

        return cls(path=path, document=document)

    def page(self, index: int) -> Poppler.Page:
        """Return one zero-based page, mapping unexpected Poppler failures."""

        if not 0 <= index < self.page_count:
            raise IndexError(f"Page index out of range: {index}")

        try:
            page = self._document.get_page(index)
        except GLib.Error as error:
            if _looks_protected(error):
                raise ProtectedDocumentError(
                    "This PDF is password-protected or uses unsupported document security."
                ) from error
            raise InvalidDocumentError("This PDF page could not be read.") from error

        if page is None:
            raise InvalidDocumentError("This PDF page could not be read.")
        return page
