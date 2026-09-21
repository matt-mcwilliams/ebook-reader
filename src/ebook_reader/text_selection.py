"""PDF text-selection values, coordinate conversion, and Poppler adapters.

The reader renders a PDF page into integer-sized pixels, while Poppler's
selection APIs use page points.  Keeping that boundary here makes the GTK
gesture code responsible only for input and presentation state.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import logging
import math
from typing import Any

import gi

gi.require_version("Poppler", "0.18")
from gi.repository import GLib, Poppler  # noqa: E402


logger = logging.getLogger(__name__)

MAX_SELECTION_RECTANGLES = 4096


def _validate_dimensions(width: float, height: float, label: str) -> None:
    if (
        not math.isfinite(width)
        or not math.isfinite(height)
        or width <= 0
        or height <= 0
    ):
        raise ValueError(f"{label} dimensions must be finite and positive")


@dataclass(frozen=True)
class SelectionPoint:
    """A point in PDF page coordinates, measured in points."""

    x: float
    y: float

    def __post_init__(self) -> None:
        if not math.isfinite(self.x) or not math.isfinite(self.y):
            raise ValueError("Selection points must be finite")


PdfPoint = SelectionPoint


@dataclass(frozen=True)
class RenderPoint:
    """A point in rendered-page pixel coordinates."""

    x: float
    y: float

    def __post_init__(self) -> None:
        if not math.isfinite(self.x) or not math.isfinite(self.y):
            raise ValueError("Rendered points must be finite")


@dataclass(frozen=True)
class SelectionRectangle:
    """A directional Poppler selection rectangle in PDF coordinates."""

    x1: float
    y1: float
    x2: float
    y2: float

    def __post_init__(self) -> None:
        if not all(math.isfinite(value) for value in (self.x1, self.y1, self.x2, self.y2)):
            raise ValueError("Selection rectangles must be finite")

    @property
    def left(self) -> float:
        return min(self.x1, self.x2)

    @property
    def top(self) -> float:
        return min(self.y1, self.y2)

    @property
    def right(self) -> float:
        return max(self.x1, self.x2)

    @property
    def bottom(self) -> float:
        return max(self.y1, self.y2)

    @property
    def width(self) -> float:
        return abs(self.x2 - self.x1)

    @property
    def height(self) -> float:
        return abs(self.y2 - self.y1)


PdfRectangle = SelectionRectangle


@dataclass(frozen=True)
class HighlightRectangle:
    """One rendered-page rectangle returned by Poppler's selection region."""

    x: float
    y: float
    width: float
    height: float

    def __post_init__(self) -> None:
        if not all(
            math.isfinite(value)
            for value in (self.x, self.y, self.width, self.height)
        ):
            raise ValueError("Highlight rectangles must be finite")
        if self.width < 0 or self.height < 0:
            raise ValueError("Highlight rectangle dimensions cannot be negative")


HighlightRect = HighlightRectangle


@dataclass(frozen=True)
class TextSelection:
    """The immutable PDF-space state of one page selection."""

    page_index: int
    anchor: SelectionPoint
    current: SelectionPoint
    text: str = ""

    def __post_init__(self) -> None:
        if self.page_index < 0:
            raise ValueError("Page index cannot be negative")
        if not isinstance(self.text, str):
            raise TypeError("Selected text must be a string")

    @property
    def rectangle(self) -> SelectionRectangle:
        return SelectionRectangle(
            self.anchor.x,
            self.anchor.y,
            self.current.x,
            self.current.y,
        )

    def with_text(self, text: str) -> "TextSelection":
        return replace(self, text=text)


Selection = TextSelection


def clamp_render_point(
    x: float,
    y: float,
    rendered_width: float,
    rendered_height: float,
) -> RenderPoint:
    """Clamp a pointer to the inclusive bounds of a rendered page."""

    _validate_dimensions(rendered_width, rendered_height, "Rendered page")
    if not math.isfinite(x) or not math.isfinite(y):
        raise ValueError("Pointer coordinates must be finite")
    return RenderPoint(
        min(rendered_width, max(0.0, x)),
        min(rendered_height, max(0.0, y)),
    )


def render_to_pdf_point(
    point: RenderPoint,
    rendered_width: float,
    rendered_height: float,
    source_width: float,
    source_height: float,
) -> SelectionPoint:
    """Convert a rendered-page point to independent PDF page ratios."""

    _validate_dimensions(rendered_width, rendered_height, "Rendered page")
    _validate_dimensions(source_width, source_height, "PDF page")
    return SelectionPoint(
        point.x / rendered_width * source_width,
        point.y / rendered_height * source_height,
    )


def pdf_to_render_point(
    point: SelectionPoint,
    rendered_width: float,
    rendered_height: float,
    source_width: float,
    source_height: float,
) -> RenderPoint:
    """Convert a PDF page point to rendered-page pixels."""

    _validate_dimensions(rendered_width, rendered_height, "Rendered page")
    _validate_dimensions(source_width, source_height, "PDF page")
    return RenderPoint(
        point.x / source_width * rendered_width,
        point.y / source_height * rendered_height,
    )


def directional_selection_rectangle(
    anchor: RenderPoint,
    current: RenderPoint,
    rendered_width: float,
    rendered_height: float,
    source_width: float,
    source_height: float,
) -> SelectionRectangle:
    """Make a directional PDF rectangle without sorting its corners."""

    anchor_pdf = render_to_pdf_point(
        clamp_render_point(anchor.x, anchor.y, rendered_width, rendered_height),
        rendered_width,
        rendered_height,
        source_width,
        source_height,
    )
    current_pdf = render_to_pdf_point(
        clamp_render_point(current.x, current.y, rendered_width, rendered_height),
        rendered_width,
        rendered_height,
        source_width,
        source_height,
    )
    return SelectionRectangle(
        anchor_pdf.x,
        anchor_pdf.y,
        current_pdf.x,
        current_pdf.y,
    )


def selection_from_render_points(
    page_index: int,
    anchor: RenderPoint,
    current: RenderPoint,
    rendered_width: float,
    rendered_height: float,
    source_width: float,
    source_height: float,
    *,
    text: str = "",
) -> TextSelection:
    """Create immutable selection state from two rendered-page points."""

    rectangle = directional_selection_rectangle(
        anchor,
        current,
        rendered_width,
        rendered_height,
        source_width,
        source_height,
    )
    return TextSelection(
        page_index=page_index,
        anchor=SelectionPoint(rectangle.x1, rectangle.y1),
        current=SelectionPoint(rectangle.x2, rectangle.y2),
        text=text,
    )


def _poppler_rectangle(rectangle: SelectionRectangle) -> Poppler.Rectangle:
    value = Poppler.Rectangle()
    value.x1 = rectangle.x1
    value.y1 = rectangle.y1
    value.x2 = rectangle.x2
    value.y2 = rectangle.y2
    return value


def _region_rectangle(region: Any, index: int) -> tuple[float, float, float, float]:
    value = region.get_rectangle(index)
    return float(value.x), float(value.y), float(value.width), float(value.height)


def selection_region(
    page: Any,
    rectangle: SelectionRectangle,
    rendered_size: tuple[float, float],
    *,
    max_rectangles: int = MAX_SELECTION_RECTANGLES,
) -> tuple[HighlightRectangle, ...]:
    """Return Poppler's glyph-aligned selection region in render pixels.

    ``get_selected_region`` accepts one pixels-per-point scale, while the
    renderer can have slightly different X/Y scales after integer rounding.
    The representative scale is used for Poppler and the exact axis scales
    are applied when mapping its integer region back to the rendered page.
    """

    rendered_width, rendered_height = rendered_size
    _validate_dimensions(rendered_width, rendered_height, "Rendered page")
    if max_rectangles <= 0:
        raise ValueError("Maximum selection rectangles must be positive")

    try:
        source_width, source_height = page.get_size()
        _validate_dimensions(float(source_width), float(source_height), "PDF page")
        scale_x = rendered_width / float(source_width)
        scale_y = rendered_height / float(source_height)
        scale = (scale_x + scale_y) / 2.0
        region = page.get_selected_region(
            scale,
            Poppler.SelectionStyle.GLYPH,
            _poppler_rectangle(rectangle),
        )
        if region is None:
            return ()
        count = int(region.get_num_rectangles())
        if count < 0:
            raise ValueError("Poppler returned a negative region size")
        if count > max_rectangles:
            logger.warning(
                "Ignoring oversized Poppler selection region with %d rectangles",
                count,
            )
            return ()

        highlights: list[HighlightRectangle] = []
        for index in range(count):
            x, y, width, height = _region_rectangle(region, index)
            if width < 0 or height < 0:
                raise ValueError("Poppler returned a negative region rectangle")
            highlights.append(
                HighlightRectangle(
                    x * scale_x / scale,
                    y * scale_y / scale,
                    width * scale_x / scale,
                    height * scale_y / scale,
                )
            )
        return tuple(highlights)
    except (GLib.Error, AttributeError, IndexError, TypeError, ValueError, OverflowError) as error:
        logger.warning("Unable to calculate the PDF selection region: %s", error)
        return ()
    except Exception as error:  # pragma: no cover - defensive binding boundary
        logger.warning("Unexpected PDF selection region failure: %s", error)
        return ()


def selected_text(page: Any, rectangle: SelectionRectangle) -> str:
    """Extract safe plain Unicode text for a directional PDF rectangle."""

    try:
        value = page.get_selected_text(
            Poppler.SelectionStyle.GLYPH,
            _poppler_rectangle(rectangle),
        )
        return value if isinstance(value, str) else ""
    except (GLib.Error, AttributeError, TypeError, ValueError) as error:
        logger.warning("Unable to extract PDF selection text: %s", error)
        return ""
    except Exception as error:  # pragma: no cover - defensive binding boundary
        logger.warning("Unexpected PDF selection text failure: %s", error)
        return ""


__all__ = [
    "HighlightRect",
    "HighlightRectangle",
    "MAX_SELECTION_RECTANGLES",
    "PdfPoint",
    "PdfRectangle",
    "RenderPoint",
    "Selection",
    "SelectionPoint",
    "SelectionRectangle",
    "TextSelection",
    "clamp_render_point",
    "directional_selection_rectangle",
    "pdf_to_render_point",
    "selected_text",
    "selection_from_render_points",
    "selection_region",
    "render_to_pdf_point",
]
