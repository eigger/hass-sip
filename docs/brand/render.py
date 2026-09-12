#!/usr/bin/env python3
"""Render the hass-sip brand assets (icon + logo) with Pillow.

Outputs into ``custom_components/sip/brand/``:

* ``icon.png``      256×256  — rounded square, roof chevron + handset
* ``icon@2x.png``   512×512
* ``logo.png``      512×128  — mark + "SIP Client" wordmark, transparent
* ``logo@2x.png``   1024×256

The sizes follow the home-assistant/brands rules. The handset glyph is the
Material Design Icons "phone" path (Pictogrammers, Apache 2.0), traced here so
no SVG library is needed. Everything is drawn at 4× and downsampled so edges
are anti-aliased.

Requires Pillow (``pip install pillow``). Fonts: Arial → DejaVu → Liberation →
Pillow default.
"""
from __future__ import annotations

import math
import re
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parents[2] / "custom_components" / "sip" / "brand"
SS = 4  # supersampling factor

BLUE_TOP = (24, 188, 242)  # Home Assistant brand blue
BLUE_BOTTOM = (10, 120, 205)
WHITE = (255, 255, 255, 255)
WORDMARK = (17, 132, 214)
TAGLINE = (110, 122, 138)

_FONT_CANDIDATES: tuple[tuple[str, str], ...] = (
    (
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    ),
    ("/Library/Fonts/Arial.ttf", "/Library/Fonts/Arial Bold.ttf"),
    (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ),
    (
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    ),
    (r"C:\Windows\Fonts\arial.ttf", r"C:\Windows\Fonts\arialbd.ttf"),
)


@lru_cache(maxsize=1)
def _font_files() -> tuple[str | None, str | None]:
    for regular, bold in _FONT_CANDIDATES:
        if Path(regular).is_file():
            return regular, bold if Path(bold).is_file() else regular
    return None, None


def font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    regular, bold_path = _font_files()
    path = bold_path if bold else regular
    if path:
        return ImageFont.truetype(path, size)
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


# --------------------------------------------------------------------------
# MDI "phone" glyph (24×24 viewBox). Arcs in this path are 1-unit corner
# roundings, so they are traced as straight segments; cubics are sampled.

_MDI_PHONE = (
    "M6.62,10.79C8.06,13.62 10.38,15.94 13.21,17.38L15.41,15.18C15.69,14.9 "
    "16.08,14.82 16.43,14.93C17.55,15.3 18.75,15.5 20,15.5A1,1 0 0,1 21,16.5V20"
    "A1,1 0 0,1 20,21A17,17 0 0,1 3,4A1,1 0 0,1 4,3H7.5A1,1 0 0,1 8.5,4C8.5,5.25 "
    "8.7,6.45 9.07,7.57C9.18,7.92 9.1,8.31 8.82,8.59L6.62,10.79Z"
)

_TOKEN = re.compile(r"[MLCVHAZ]|-?\d*\.?\d+")


def _bezier(p0, p1, p2, p3, n: int = 12):
    for i in range(1, n + 1):
        t = i / n
        u = 1 - t
        x = u**3 * p0[0] + 3 * u**2 * t * p1[0] + 3 * u * t**2 * p2[0] + t**3 * p3[0]
        y = u**3 * p0[1] + 3 * u**2 * t * p1[1] + 3 * u * t**2 * p2[1] + t**3 * p3[1]
        yield (x, y)


def _arc_points(p0, rx, ry, large, sweep, p1, n: int = 24):
    """SVG elliptical arc (rotation 0) → polyline. Enough for the 17-unit dial arc."""
    x0, y0 = p0
    x1, y1 = p1
    dx, dy = (x0 - x1) / 2, (y0 - y1) / 2
    lam = (dx * dx) / (rx * rx) + (dy * dy) / (ry * ry)
    if lam > 1:
        rx, ry = rx * math.sqrt(lam), ry * math.sqrt(lam)
    num = rx * rx * ry * ry - rx * rx * dy * dy - ry * ry * dx * dx
    den = rx * rx * dy * dy + ry * ry * dx * dx
    coef = math.sqrt(max(0.0, num / den)) if den else 0.0
    if large == sweep:
        coef = -coef
    cx_ = coef * rx * dy / ry
    cy_ = -coef * ry * dx / rx
    cx, cy = cx_ + (x0 + x1) / 2, cy_ + (y0 + y1) / 2

    def ang(ux, uy, vx, vy):
        a = math.atan2(ux * vy - uy * vx, ux * vx + uy * vy)
        return a

    a0 = ang(1, 0, (x0 - cx) / rx, (y0 - cy) / ry)
    da = ang((x0 - cx) / rx, (y0 - cy) / ry, (x1 - cx) / rx, (y1 - cy) / ry)
    if not sweep and da > 0:
        da -= 2 * math.pi
    elif sweep and da < 0:
        da += 2 * math.pi
    for i in range(1, n + 1):
        a = a0 + da * i / n
        yield (cx + rx * math.cos(a), cy + ry * math.sin(a))


def mdi_phone_polygon() -> list[tuple[float, float]]:
    toks = _TOKEN.findall(_MDI_PHONE)
    pts: list[tuple[float, float]] = []
    cur = (0.0, 0.0)
    i = 0
    cmd = ""
    while i < len(toks):
        if toks[i].isalpha():
            cmd = toks[i]
            i += 1
            if cmd == "Z":
                continue
        if cmd == "M" or cmd == "L":
            cur = (float(toks[i]), float(toks[i + 1]))
            pts.append(cur)
            i += 2
        elif cmd == "H":
            cur = (float(toks[i]), cur[1])
            pts.append(cur)
            i += 1
        elif cmd == "V":
            cur = (cur[0], float(toks[i]))
            pts.append(cur)
            i += 1
        elif cmd == "C":
            p1 = (float(toks[i]), float(toks[i + 1]))
            p2 = (float(toks[i + 2]), float(toks[i + 3]))
            p3 = (float(toks[i + 4]), float(toks[i + 5]))
            pts.extend(_bezier(cur, p1, p2, p3))
            cur = p3
            i += 6
        elif cmd == "A":
            rx, ry = float(toks[i]), float(toks[i + 1])
            large, sweep = int(float(toks[i + 3])), int(float(toks[i + 4]))
            p1 = (float(toks[i + 5]), float(toks[i + 6]))
            pts.extend(_arc_points(cur, rx, ry, large, sweep, p1))
            cur = p1
            i += 7
        else:
            raise ValueError(f"unsupported command {cmd}")
    return pts


# --------------------------------------------------------------------------
# drawing


def gradient_rounded_square(size: int, radius: int) -> Image.Image:
    grad = Image.new("RGBA", (size, size))
    px = grad.load()
    for y in range(size):
        t = y / (size - 1)
        r = round(BLUE_TOP[0] + (BLUE_BOTTOM[0] - BLUE_TOP[0]) * t)
        g = round(BLUE_TOP[1] + (BLUE_BOTTOM[1] - BLUE_TOP[1]) * t)
        b = round(BLUE_TOP[2] + (BLUE_BOTTOM[2] - BLUE_TOP[2]) * t)
        for x in range(size):
            px[x, y] = (r, g, b, 255)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size - 1, size - 1), radius, fill=255)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(grad, (0, 0), mask)
    return out


def draw_mark(size: int) -> Image.Image:
    """The square mark: gradient tile, roof chevron, handset."""
    s = size * SS
    img = gradient_rounded_square(s, int(s * 0.225))
    d = ImageDraw.Draw(img)

    # Roof chevron — "home". Thick stroke with round joints.
    apex = (s * 0.5, s * 0.20)
    left = (s * 0.19, s * 0.455)
    right = (s * 0.81, s * 0.455)
    w = s * 0.085
    for a, b in ((left, apex), (apex, right)):
        d.line((a, b), fill=WHITE, width=int(w))
    for p in (left, apex, right):
        d.ellipse((p[0] - w / 2, p[1] - w / 2, p[0] + w / 2, p[1] + w / 2), fill=WHITE)

    # Handset — MDI phone glyph scaled into the lower two thirds.
    glyph = mdi_phone_polygon()
    scale = s * 0.50 / 24
    ox = s * 0.525 - 12 * scale  # glyph is left-heavy; nudge right
    oy = s * 0.615 - 12 * scale
    d.polygon([(ox + x * scale, oy + y * scale) for x, y in glyph], fill=WHITE)

    return img.resize((size, size), Image.LANCZOS)


def draw_logo(height: int) -> Image.Image:
    """Wordmark: mark on the left, "SIP Client" + tagline on the right. Transparent."""
    hs = height * SS
    width = height * 4
    ws = width * SS
    img = Image.new("RGBA", (ws, hs), (0, 0, 0, 0))
    mark = draw_mark(int(height * 0.78)).resize((int(hs * 0.78), int(hs * 0.78)), Image.LANCZOS)
    mx = int(hs * 0.11)
    my = (hs - mark.height) // 2
    img.alpha_composite(mark, (mx, my))
    d = ImageDraw.Draw(img)
    tx = mx + mark.width + int(hs * 0.16)
    f1 = font(int(hs * 0.42), True)
    f2 = font(int(hs * 0.165))
    d.text((tx, int(hs * 0.19)), "SIP Client", font=f1, fill=WORDMARK)
    d.text((tx + int(hs * 0.012), int(hs * 0.64)), "SIP extension for Home Assistant", font=f2, fill=TAGLINE)
    return img.resize((width, height), Image.LANCZOS)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, size in (("icon.png", 256), ("icon@2x.png", 512)):
        draw_mark(size).save(OUT / name, optimize=True)
        print(name, size, "x", size)
    for name, height in (("logo.png", 128), ("logo@2x.png", 256)):
        draw_logo(height).save(OUT / name, optimize=True)
        print(name, height * 4, "x", height)


if __name__ == "__main__":
    main()
