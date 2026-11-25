"""
style.py - the shared look of the crowd AI's visuals, matching the Kavrix frontend.

Colours come from the frontend's src/index.css (light and dark themes) and its riskColor() levels.
Fonts: the frontend uses Inter, Inter Tight, JetBrains Mono and VT323 (pixel). If you put those files in
./fonts/ (Inter-Regular.ttf, Inter-Medium.ttf, Inter-Bold.ttf or a variable Inter[...].ttf,
JetBrainsMono-Regular.ttf, JetBrainsMono-Bold.ttf, VT323-Regular.ttf) they are used. Otherwise it falls back
to the closest system fonts (SF Pro and SF Mono on a Mac, then Helvetica, Arial, DejaVu), so nothing breaks.
"""
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

FONT_DIR = Path(__file__).resolve().parent / "fonts"


def hex_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


# the frontend's :root and :root[data-theme="dark"] tokens (src/index.css)
_THEMES = {
    "light": dict(bg="#f1f0ec", surface="#fbfaf7", panel="#e8e7e2", panel_hi="#f0efeb", ink="#121213",
                  ink2="#2a2a2c", muted="#73736e", muted2="#b0afa9", line="#dcdbd5", stroke="#cfcec7",
                  on_ink="#fbfaf7", alert_bg="#fdeceb",
                  text_low="#1f7a50", text_med="#9a5b00", text_high="#c2272d"),   # darker, readable on light
    "dark": dict(bg="#0e0e0f", surface="#18181a", panel="#1f1f22", panel_hi="#242427", ink="#f1f0ec",
                 ink2="#dddcd6", muted="#8f8f8a", muted2="#4d4d4a", line="#2a2a2d", stroke="#38383c",
                 on_ink="#121213", alert_bg="#3a1a1d",
                 text_low="#30a46c", text_med="#f5a524", text_high="#e5484d"),
}
ACCENT = hex_rgb("#e4f35a")
LEVEL = {"LOW": hex_rgb("#30a46c"), "MEDIUM": hex_rgb("#f5a524"), "HIGH": hex_rgb("#e5484d")}  # = riskColor()
RADIUS = 20


class Theme:
    """Attribute access to one theme's colours as RGB tuples: theme.bg, theme.ink, ..."""

    def __init__(self, name):
        if name not in _THEMES:
            raise ValueError(f"theme must be one of {sorted(_THEMES)}")
        self.name = name
        for k, v in _THEMES[name].items():
            setattr(self, k, hex_rgb(v))


# ----------------------------------------------------------------------- fonts --
# role -> ordered candidates: (file name or path, variable-font instance name or None)
_SF, _SFM = "/System/Library/Fonts/SFNS.ttf", "/System/Library/Fonts/SFNSMono.ttf"
_SANS = {
    "regular": [("Inter-Regular.ttf", None), ("Inter[opsz,wght].ttf", "Regular"), ("Inter.ttf", "Regular"),
                (_SF, "Regular"), ("/System/Library/Fonts/Supplemental/Arial.ttf", None), ("DejaVuSans.ttf", None)],
    "medium": [("Inter-Medium.ttf", None), ("Inter[opsz,wght].ttf", "Medium"), (_SF, "Medium"),
               ("/System/Library/Fonts/Supplemental/Arial.ttf", None), ("DejaVuSans.ttf", None)],
    "bold": [("InterTight-Bold.ttf", None), ("Inter-Bold.ttf", None), ("Inter[opsz,wght].ttf", "Bold"),
             (_SF, "Bold"), ("/System/Library/Fonts/Supplemental/Arial Bold.ttf", None), ("DejaVuSans-Bold.ttf", None)],
}
_MONO = {
    "regular": [("JetBrainsMono-Regular.ttf", None), ("JetBrainsMono[wght].ttf", "Regular"), (_SFM, "Regular"),
                ("/System/Library/Fonts/Menlo.ttc", None), ("DejaVuSansMono.ttf", None)],
    "bold": [("JetBrainsMono-Bold.ttf", None), ("JetBrainsMono[wght].ttf", "Bold"), (_SFM, "Bold"),
             ("/System/Library/Fonts/Menlo.ttc", None), ("DejaVuSansMono-Bold.ttf", None)],
}
_PIXEL = [("VT323-Regular.ttf", None)]      # the frontend's big-number font; falls back to bold mono
_cache = {}


def _load(candidates, size):
    for name, variation in candidates:
        for path in (FONT_DIR / name, Path(name)):
            if not path.is_absolute() and path.parent == Path("."):
                continue                     # a bare name only counts inside ./fonts/ (or the system path list)
            try:
                f = ImageFont.truetype(str(path), size)
            except OSError:
                continue
            if variation:
                try:
                    f.set_variation_by_name(variation)
                except (OSError, AttributeError, ValueError):
                    continue
            return f
        try:                                 # last resort for bare names: let PIL search system font paths
            if Path(name).parent == Path("."):
                f = ImageFont.truetype(name, size)
                if variation:
                    f.set_variation_by_name(variation)
                return f
        except (OSError, AttributeError, ValueError):
            continue
    return ImageFont.load_default(size=size)


def font(role, size):
    """role: sans, sans-medium, sans-bold, display (Inter Tight, the site's heading face), mono, mono-bold, pixel."""
    key = (role, size)
    if key not in _cache:
        table = {"sans": _SANS["regular"], "sans-medium": _SANS["medium"], "sans-bold": _SANS["bold"],
                 "display": [("InterTight[wght].ttf", "Bold"), ("InterTight-Bold.ttf", None)] + _SANS["bold"],
                 "mono": _MONO["regular"], "mono-bold": _MONO["bold"],
                 "pixel": _PIXEL + _MONO["bold"]}[role]
        _cache[key] = _load(table, size)
    return _cache[key]


def has_pixel_font():
    """True if the frontend's big-number font (VT323) is available in ./fonts/."""
    return (FONT_DIR / "VT323-Regular.ttf").exists()


def mix(a, b, t):
    """Blend colour a toward b by t (0..1)."""
    return tuple(int(round(x + (y - x) * t)) for x, y in zip(a, b))


# ------------------------------------------------------------------ heat ramp --
def heat_lut(alpha_max=0.5):
    """Lookup tables for the density overlay: green, then amber, then red (the level colours), fading
    to transparent where there is almost nobody. Index 0..255 = density / D_CRIT. Returns (bgr, alpha)."""
    x = np.arange(256) / 255.0
    stops = np.array([0.0, 0.5, 1.0])
    cols = np.array([LEVEL["LOW"], LEVEL["MEDIUM"], LEVEL["HIGH"]], float)
    rgb = np.stack([np.interp(x, stops, cols[:, c]) for c in range(3)], axis=1)
    t = np.clip((x - 0.04) / 0.31, 0, 1)
    alpha = alpha_max * t * t * (3 - 2 * t)                       # smoothstep: transparent below ~4%
    return rgb[:, ::-1].astype(np.float32), alpha.astype(np.float32)


# ------------------------------------------------------------- painting helpers --
def paint_rgba(frame_bgr, box, painter, ss=2):
    """Draw anti-aliased vector shapes and text on a small region of a BGR frame, in place.
    painter(draw, ss) draws on a transparent layer ss times larger; it is shrunk and blended over the region."""
    h, w = frame_bgr.shape[:2]
    x, y, bw, bh = (int(v) for v in box)
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(w, x + bw), min(h, y + bh)
    if x1 <= x0 or y1 <= y0:
        return
    base = Image.fromarray(frame_bgr[y0:y1, x0:x1, ::-1]).convert("RGBA")
    layer = Image.new("RGBA", ((x1 - x0) * ss, (y1 - y0) * ss), (0, 0, 0, 0))
    painter(ImageDraw.Draw(layer), ss, (x - x0, y - y0))          # offset of the box inside the crop
    layer = layer.resize((x1 - x0, y1 - y0), Image.LANCZOS)
    frame_bgr[y0:y1, x0:x1] = np.asarray(Image.alpha_composite(base, layer).convert("RGB"))[:, :, ::-1]


def draw_drone_icon(draw, x, y, size, color, k=1.0):
    """The frontend's DroneIcon (App.jsx): 40x24 viewBox, 2.4 stroke, round caps. (x, y) is the top-left."""
    s = size / 40.0 * k
    w = max(1, round(2.4 * s))

    def P(px, py):
        return (x * k + px * s, y * k + py * s)

    for pts in ([(3, 4), (13, 4)], [(27, 4), (37, 4)], [(8, 4), (8, 8), (14, 13), (26, 13), (32, 8), (32, 4)],
                [(15, 13), (13, 20)], [(25, 13), (27, 20)]):
        draw.line([P(*p) for p in pts], fill=color, width=w, joint="curve")
        for p in (pts[0], pts[-1]):
            r = w / 2
            cx, cy = P(*p)
            draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=color)
    a, b = P(15, 10), P(25, 17)
    draw.rounded_rectangle((a[0], a[1], b[0], b[1]), radius=2 * s, fill=color)
