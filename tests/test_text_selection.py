from __future__ import annotations

import math
import unittest

from ebook_reader.text_selection import (
    HighlightRectangle,
    RenderPoint,
    SelectionPoint,
    SelectionRectangle,
    clamp_render_point,
    directional_selection_rectangle,
    pdf_to_render_point,
    render_to_pdf_point,
    selected_text,
    selection_from_render_points,
    selection_region,
)


class _RegionRectangle:
    def __init__(self, x: float, y: float, width: float, height: float) -> None:
        self.x = x
        self.y = y
        self.width = width
        self.height = height


class _Region:
    def __init__(self, *rectangles: _RegionRectangle) -> None:
        self._rectangles = rectangles

    def get_num_rectangles(self) -> int:
        return len(self._rectangles)

    def get_rectangle(self, index: int) -> _RegionRectangle:
        return self._rectangles[index]


class _Page:
    def __init__(self, text: str = "") -> None:
        self.text = text
        self.last_rectangle: SelectionRectangle | None = None

    def get_size(self) -> tuple[float, float]:
        return (600.0, 900.0)

    def get_selected_region(self, _scale: float, _style: object, rectangle: object) -> _Region:
        self.last_rectangle = SelectionRectangle(
            rectangle.x1,
            rectangle.y1,
            rectangle.x2,
            rectangle.y2,
        )
        return _Region(_RegionRectangle(10, 20, 100, 30))

    def get_selected_text(self, _style: object, rectangle: object) -> str:
        self.last_rectangle = SelectionRectangle(
            rectangle.x1,
            rectangle.y1,
            rectangle.x2,
            rectangle.y2,
        )
        return self.text


class _BrokenPage(_Page):
    def get_selected_region(self, _scale: float, _style: object, _rectangle: object) -> None:
        return None

    def get_selected_text(self, _style: object, _rectangle: object) -> None:
        return None


class TextSelectionTests(unittest.TestCase):
    def test_forward_and_reverse_drags_keep_direction(self) -> None:
        forward = directional_selection_rectangle(
            RenderPoint(100, 200),
            RenderPoint(700, 1000),
            800,
            1200,
            600,
            900,
        )
        reverse = directional_selection_rectangle(
            RenderPoint(700, 1000),
            RenderPoint(100, 200),
            800,
            1200,
            600,
            900,
        )
        self.assertEqual((forward.x1, forward.y1), (75.0, 150.0))
        self.assertEqual((forward.x2, forward.y2), (525.0, 750.0))
        self.assertEqual((reverse.x1, reverse.y1), (525.0, 750.0))
        self.assertEqual((reverse.x2, reverse.y2), (75.0, 150.0))

    def test_pointer_is_clamped_to_inclusive_render_bounds(self) -> None:
        self.assertEqual(clamp_render_point(-1, 1201, 801, 1201), RenderPoint(0, 1201))
        self.assertEqual(
            render_to_pdf_point(RenderPoint(801, 1201), 801, 1201, 600, 900),
            SelectionPoint(600, 900),
        )

    def test_invalid_dimensions_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            clamp_render_point(0, 0, 0, 100)
        with self.assertRaises(ValueError):
            pdf_to_render_point(SelectionPoint(1, 1), math.inf, 100, 600, 900)
        with self.assertRaises(ValueError):
            selection_from_render_points(0, RenderPoint(0, 0), RenderPoint(1, 1), 100, 100, -1, 100)

    def test_render_region_uses_actual_axis_scales(self) -> None:
        page = _Page()
        highlights = selection_region(
            page,
            SelectionRectangle(10, 20, 100, 200),
            (801, 1201),
        )
        self.assertEqual(len(highlights), 1)
        highlight = highlights[0]
        self.assertIsInstance(highlight, HighlightRectangle)
        representative_scale = ((801 / 600) + (1201 / 900)) / 2
        self.assertAlmostEqual(highlight.x, 10 * (801 / 600) / representative_scale, places=6)
        self.assertAlmostEqual(highlight.y, 20 * (1201 / 900) / representative_scale, places=6)
        assert page.last_rectangle is not None
        self.assertEqual(page.last_rectangle.x1, 10)
        self.assertEqual(page.last_rectangle.x2, 100)

    def test_text_preserves_unicode_and_line_breaks(self) -> None:
        page = _Page("First line\nπ and 你好\n✓")
        value = selected_text(page, SelectionRectangle(1, 2, 3, 4))
        self.assertEqual(value, "First line\nπ and 你好\n✓")

    def test_empty_and_malformed_poppler_results_are_safe(self) -> None:
        page = _BrokenPage()
        self.assertEqual(selection_region(page, SelectionRectangle(1, 2, 3, 4), (600, 900)), ())
        self.assertEqual(selected_text(page, SelectionRectangle(1, 2, 3, 4)), "")

    def test_oversized_region_is_ignored(self) -> None:
        class ManyRectangles(_Page):
            def get_selected_region(self, _scale: float, _style: object, _rectangle: object) -> _Region:
                return _Region(*(_RegionRectangle(0, 0, 1, 1) for _ in range(3)))

        self.assertEqual(
            selection_region(
                ManyRectangles(),
                SelectionRectangle(1, 2, 3, 4),
                (600, 900),
                max_rectangles=2,
            ),
            (),
        )


if __name__ == "__main__":
    unittest.main()
