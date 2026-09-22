from __future__ import annotations

import unittest

import cairo

from ebook_reader.renderer import (
    MAX_RENDER_BYTES,
    RenderCache,
    RenderCacheKey,
    RenderedPage,
    _invert_surface,
    _render_dimensions,
    clamp_manual_scale,
    fit_scale_for_viewport,
)


def fake_page(width: int, height: int) -> RenderedPage:
    return RenderedPage(texture=None, width=width, height=height)  # type: ignore[arg-type]


class RendererTests(unittest.TestCase):
    def test_fit_scale_preserves_aspect_ratio_for_portrait_and_landscape(self) -> None:
        self.assertAlmostEqual(fit_scale_for_viewport(600, 900, 1000, 700), 700 / 900)
        self.assertAlmostEqual(fit_scale_for_viewport(1200, 600, 1000, 700), 1000 / 1200)

    def test_zoom_and_render_dimensions_are_bounded(self) -> None:
        self.assertEqual(clamp_manual_scale(0.01), 0.5)
        self.assertEqual(clamp_manual_scale(99), 3.0)
        self.assertEqual(clamp_manual_scale(float("nan")), 1.0)
        width, height = _render_dimensions(10_000, 20_000, 3)
        self.assertLessEqual(width, 4096)
        self.assertLessEqual(height, 4096)
        self.assertLessEqual(width * height * 4, MAX_RENDER_BYTES)

    def test_cache_evicts_old_pages_and_can_keep_only_adjacent_active_pages(self) -> None:
        cache = RenderCache(max_bytes=100)
        key_one = RenderCacheKey("book", 0, "manual", 1.0)
        key_two = RenderCacheKey("book", 1, "manual", 1.0)
        key_three = RenderCacheKey("book", 2, "manual", 1.0)
        cache.put(key_one, fake_page(5, 5))
        cache.put(key_two, fake_page(5, 5))
        cache.put(key_three, fake_page(5, 5))
        self.assertEqual(len(cache), 1)
        self.assertIsNotNone(cache.get(key_three))

        cache.put(key_one, fake_page(2, 2))
        cache.put(key_two, fake_page(2, 2))
        cache.put(key_three, fake_page(2, 2))
        cache.prune_around(
            document_id="book",
            page_index=1,
            zoom_mode="manual",
            scale=1.0,
        )
        self.assertEqual(len(cache), 3)
        self.assertLessEqual(cache.bytes_used, 100)

    def test_cache_does_not_store_a_page_larger_than_its_budget(self) -> None:
        cache = RenderCache(max_bytes=10)
        key = RenderCacheKey("book", 0, "manual", 1.0)
        self.assertFalse(cache.put(key, fake_page(2, 2)))
        self.assertEqual(len(cache), 0)

    def test_inverted_cache_entries_are_separate_from_normal_entries(self) -> None:
        cache = RenderCache(max_bytes=100)
        normal_key = RenderCacheKey("book", 0, "manual", 1.0)
        inverted_key = RenderCacheKey("book", 0, "manual", 1.0, invert_colors=True)
        cache.put(normal_key, fake_page(2, 2))
        cache.put(inverted_key, fake_page(2, 2))

        cache.prune_around(
            document_id="book",
            page_index=0,
            zoom_mode="manual",
            scale=1.0,
            invert_colors=True,
        )

        self.assertIsNone(cache.get(normal_key))
        self.assertIsNotNone(cache.get(inverted_key))

    def test_invert_surface_inverts_each_rgb_channel(self) -> None:
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1)
        context = cairo.Context(surface)
        context.set_source_rgb(0.2, 0.4, 0.8)
        context.paint()

        _invert_surface(context)
        surface.flush()

        # Cairo stores FORMAT_ARGB32 as BGRA on the target Linux platform.
        self.assertEqual(list(bytes(surface.get_data())[:4]), [51, 153, 204, 255])


if __name__ == "__main__":
    unittest.main()
