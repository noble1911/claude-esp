"""SVG → PNG rasterization for the device screen (368×448).

Claude authors an SVG; the gateway rasterizes it to a PNG which the device decodes
and blits full-screen. cairosvg is light (no headless browser) and crisp for text.
"""

from __future__ import annotations

# The image fills the device's card area (not the whole 368×448 panel): the status
# line and Hold-to-talk button frame it above and below.
# Width is a multiple of 32 px (→ 64-byte rows) so every LVGL flush strip is
# cache-line-aligned — the S3's octal PSRAM rejects unaligned-size DMA transfers.
DEVICE_W = 352
DEVICE_H = 280


def svg_to_png(svg: str, width: int = DEVICE_W, height: int = DEVICE_H) -> bytes:
    """Rasterize an SVG string to PNG bytes at the device resolution."""
    import cairosvg  # imported lazily so the gateway still starts if cairo is absent

    return cairosvg.svg2png(
        bytestring=svg.encode("utf-8"),
        output_width=width,
        output_height=height,
        background_color="#0b0f17",
    )


# A self-contained sample used by the gateway's /testimg debug hook to validate the
# image path (rasterize → transport → device decode) without involving the brain.
SAMPLE_SVG = f"""<svg xmlns="http://www.w3.org/2000/svg" width="{DEVICE_W}" height="{DEVICE_H}">
  <rect width="100%" height="100%" rx="12" fill="#111827"/>
  <text x="24" y="56" fill="#22c55e" font-family="sans-serif" font-size="26" font-weight="bold">Image path OK</text>
  <circle cx="312" cy="48" r="13" fill="#22c55e"/>
  <text x="24" y="92" fill="#d1d5db" font-family="sans-serif" font-size="18">SVG → PNG → device</text>
  <rect x="24" y="126" width="296" height="14" rx="7" fill="#1f2937"/>
  <rect x="24" y="126" width="180" height="14" rx="7" fill="#3b82f6"/>
  <text x="24" y="188" fill="#9aa4b2" font-family="sans-serif" font-size="15">Rendered by the gateway</text>
</svg>"""
