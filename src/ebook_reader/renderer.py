"""Cairo-to-GTK rendering helpers for PDF pages."""

from __future__ import annotations

import math
from collections import OrderedDict
from dataclasses import dataclass

import cairo
import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Poppler", "0.18")
from gi.repository import Gdk, GLib, Poppler  # noqa: E402


DEFAULT_SCALE = 1.0
MIN_MANUAL_SCALE = 0.5
MAX_MANUAL_SCALE = 3.0
ZOOM_STEP = 1.25
MAX_RENDER_DIMENSION = 4096
MAX_RENDER_PIXELS = 16_000_000
MAX_RENDER_BYTES = MAX_RENDER_PIXELS * 4
MAX_CACHE_BYTES = 64 * 1024 * 1024


@dataclass(frozen=True)
class RenderedPage:
    """A page texture and its logical dimensions."""

    texture: Gdk.Texture
    width: int
    height: int

    @property
    def memory_bytes(self) -> int:
        """Return the approximate Cairo/GDK storage cost of this page."""

        return self.width * self.height * 4


@dataclass(frozen=True)
class RenderCacheKey:
    """Identify a rendered page at one document and zoom level."""

    document_id: str
    page_index: int
    zoom_mode: str
    scale: float


class RenderCache:
    """A small LRU cache with a strict approximate pixel-memory budget."""

    def __init__(self, *, max_bytes: int = MAX_CACHE_BYTES) -> None:
        if max_bytes <= 0:
            raise ValueError("Cache size must be positive")
        self.max_bytes = max_bytes
        self._entries: OrderedDict[RenderCacheKey, RenderedPage] = OrderedDict()
        self._bytes = 0

    @property
    def bytes_used(self) -> int:
        return self._bytes

    def __len__(self) -> int:
        return len(self._entries)

    def get(self, key: RenderCacheKey) -> RenderedPage | None:
        page = self._entries.get(key)
        if page is not None:
            self._entries.move_to_end(key)
        return page

    def put(self, key: RenderCacheKey, page: RenderedPage) -> bool:
        """Store a page unless it cannot fit inside the strict cache budget."""

        self._remove(key)
        if page.memory_bytes > self.max_bytes:
            return False

        self._entries[key] = page
        self._bytes += page.memory_bytes
        while self._bytes > self.max_bytes and self._entries:
            _old_key, old_page = self._entries.popitem(last=False)
            self._bytes -= old_page.memory_bytes
        return True

    def clear(self) -> None:
        self._entries.clear()
        self._bytes = 0

    def prune_around(
        self,
        *,
        document_id: str,
        page_index: int,
        zoom_mode: str,
        scale: float,
    ) -> None:
        """Keep only the current/adjacent pages at the active scale."""

        for key in list(self._entries):
            if (
                key.document_id != document_id
                or key.zoom_mode != zoom_mode
                or abs(key.page_index - page_index) > 1
                or not math.isclose(key.scale, scale, rel_tol=0.0, abs_tol=1e-6)
            ):
                self._remove(key)

    def _remove(self, key: RenderCacheKey) -> None:
        page = self._entries.pop(key, None)
        if page is not None:
            self._bytes -= page.memory_bytes


def clamp_manual_scale(scale: float) -> float:
    """Keep a user-selected zoom level inside the supported range."""

    return min(MAX_MANUAL_SCALE, max(MIN_MANUAL_SCALE, scale))


def fit_scale_for_viewport(
    page_width: float,
    page_height: float,
    viewport_width: float,
    viewport_height: float,
) -> float:
    """Return the largest aspect-preserving scale that fits the viewport."""

    if (
        page_width <= 0
        or page_height <= 0
        or viewport_width <= 0
        or viewport_height <= 0
        or not all(math.isfinite(value) for value in (page_width, page_height, viewport_width, viewport_height))
    ):
        raise ValueError("PDF page has invalid dimensions")

    return min(viewport_width / page_width, viewport_height / page_height)


def _render_dimensions(width: float, height: float, scale: float) -> tuple[int, int]:
    """Calculate safe pixel dimensions while preserving the page aspect ratio."""

    if (
        width <= 0
        or height <= 0
        or scale <= 0
        or not all(math.isfinite(value) for value in (width, height, scale))
    ):
        raise ValueError("PDF page has invalid dimensions")

    try:
        requested_width = max(1, math.ceil(width * scale))
        requested_height = max(1, math.ceil(height * scale))
    except (OverflowError, ValueError) as error:
        raise ValueError("Requested PDF render is too large") from error

    requested_pixels = requested_width * requested_height
    try:
        pixel_scale = math.sqrt(MAX_RENDER_PIXELS / requested_pixels)
    except (OverflowError, ZeroDivisionError, ValueError):
        pixel_scale = 0.0
    scale_limit = min(
        1.0,
        MAX_RENDER_DIMENSION / requested_width,
        MAX_RENDER_DIMENSION / requested_height,
        pixel_scale,
    )
    safe_width = max(1, math.floor(requested_width * scale_limit))
    safe_height = max(1, math.floor(requested_height * scale_limit))
    while safe_width * safe_height * 4 > MAX_RENDER_BYTES:
        safe_width = max(1, safe_width // 2)
        safe_height = max(1, safe_height // 2)
    return (
        safe_width,
        safe_height,
    )


def render_page(page: Poppler.Page, *, scale: float = DEFAULT_SCALE) -> RenderedPage:
    """Render a Poppler page to a GTK texture with a white paper background."""

    source_width, source_height = page.get_size()
    width, height = _render_dimensions(source_width, source_height, scale)

    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
    context = cairo.Context(surface)
    context.set_source_rgb(1.0, 1.0, 1.0)
    context.paint()
    context.scale(width / source_width, height / source_height)
    page.render(context)
    surface.flush()

    # Cairo's ARGB32 pixels are premultiplied BGRA on the little-endian Linux
    # systems targeted by this application, matching Gdk's first memory format.
    pixel_bytes = GLib.Bytes.new(bytes(surface.get_data()))
    texture = Gdk.MemoryTexture.new(
        width,
        height,
        Gdk.MemoryFormat.B8G8R8A8_PREMULTIPLIED,
        pixel_bytes,
        surface.get_stride(),
    )
    return RenderedPage(texture=texture, width=width, height=height)
