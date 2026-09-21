"""Cairo-to-GTK rendering helpers for PDF pages."""

from __future__ import annotations

import math
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


@dataclass(frozen=True)
class RenderedPage:
    """A page texture and its logical dimensions."""

    texture: Gdk.Texture
    width: int
    height: int


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

    if page_width <= 0 or page_height <= 0:
        raise ValueError("PDF page has invalid dimensions")
    if viewport_width <= 0 or viewport_height <= 0:
        raise ValueError("Viewport dimensions must be positive")

    return min(viewport_width / page_width, viewport_height / page_height)


def _render_dimensions(width: float, height: float, scale: float) -> tuple[int, int]:
    """Calculate safe pixel dimensions while preserving the page aspect ratio."""

    if width <= 0 or height <= 0:
        raise ValueError("PDF page has invalid dimensions")
    if scale <= 0:
        raise ValueError("Render scale must be positive")

    requested_width = max(1, math.ceil(width * scale))
    requested_height = max(1, math.ceil(height * scale))
    scale_limit = min(
        1.0,
        MAX_RENDER_DIMENSION / requested_width,
        MAX_RENDER_DIMENSION / requested_height,
        math.sqrt(MAX_RENDER_PIXELS / (requested_width * requested_height)),
    )
    return (
        max(1, math.floor(requested_width * scale_limit)),
        max(1, math.floor(requested_height * scale_limit)),
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
