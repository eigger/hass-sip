#!/usr/bin/env python3
"""Generate Home Assistant brand images for hass-sip.

The artwork is an original geometric brand mark on a dark navy rounded square:
a cyan Home Assistant house pentagon whose upper-right quadrant opens into three
concentric golden signal arcs, fused with a crisp white telephone handset
outlined in negative space. Drawn from geometric primitives at 4x scale and
downsampled, so no source image, external font file, or network access is
needed. Run with Pillow 12.3.0 for reproducible PNGs.
"""

from __future__ import annotations

import math
import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

BRAND = Path(__file__).resolve().parents[2] / "custom_components" / "sip" / "brand"
SIZE = 512
SCALE = 4
NAVY = "#1b2a41"
CYAN = "#18bcf2"
YELLOW = "#ffd23f"
GOLD = "#f2a900"
WHITE = "#ffffff"
WORDMARK = "#18bcf2"

# MDI "phone" glyph (24x24 viewBox) with slightly softer 1.4-unit outer cap corners.
_MDI_PHONE = (
    "M6.62,10.79C8.06,13.62 10.38,15.94 13.21,17.38L15.41,15.18C15.69,14.9 "
    "16.08,14.82 16.43,14.93C17.45,15.27 18.55,15.46 19.6,15.5A1.4,1.4 0 0,1 21,16.9V19.6"
    "A1.4,1.4 0 0,1 19.6,21A17,17 0 0,1 3,4.4A1.4,1.4 0 0,1 4.4,3H7.1A1.4,1.4 0 0,1 8.5,4.4"
    "C8.54,5.45 8.73,6.55 9.07,7.57C9.18,7.92 9.1,8.31 8.82,8.59L6.62,10.79Z"
)
_TOKEN = re.compile(r"[MLCVHAZ]|-?\d*\.?\d+")


def _bezier(p0, p1, p2, p3, n: int = 36):
    for i in range(1, n + 1):
        t = i / n
        u = 1 - t
        x = u**3 * p0[0] + 3 * u**2 * t * p1[0] + 3 * u * t**2 * p2[0] + t**3 * p3[0]
        y = u**3 * p0[1] + 3 * u**2 * t * p1[1] + 3 * u * t**2 * p2[1] + t**3 * p3[1]
        yield (x, y)


def _arc_points(p0, rx, ry, large, sweep, p1, n: int = 56):
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
        return math.atan2(ux * vy - uy * vx, ux * vx + uy * vy)

    a0 = ang(1, 0, (x0 - cx) / rx, (y0 - cy) / ry)
    da = ang((x0 - cx) / rx, (y0 - cy) / ry, (x1 - cx) / rx, (y1 - cy) / ry)
    if not sweep and da > 0:
        da -= 2 * math.pi
    elif sweep and da < 0:
        da += 2 * math.pi
    for i in range(1, n + 1):
        a = a0 + da * i / n
        yield (cx + rx * math.cos(a), cy + ry * math.sin(a))


def _mdi_phone_polygon() -> list[tuple[float, float]]:
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
        if cmd in ("M", "L"):
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
    return pts


def _rounded_polygon(
    points: list[tuple[float, float]],
    radii: list[float] | float,
    steps: int = 24,
) -> list[tuple[float, float]]:
    """Return polygon vertices with mathematically rounded corners."""
    n = len(points)
    if isinstance(radii, (int, float)):
        radii = [float(radii)] * n
    poly_pts: list[tuple[float, float]] = []
    for i in range(n):
        r = radii[i]
        if r <= 0:
            poly_pts.append(points[i])
            continue
        p_prev = points[(i - 1) % n]
        p_curr = points[i]
        p_next = points[(i + 1) % n]

        v1 = (p_prev[0] - p_curr[0], p_prev[1] - p_curr[1])
        v2 = (p_next[0] - p_curr[0], p_next[1] - p_curr[1])
        l1 = math.hypot(*v1)
        l2 = math.hypot(*v2)
        u1 = (v1[0] / l1, v1[1] / l1)
        u2 = (v2[0] / l2, v2[1] / l2)

        dot = max(-1.0, min(1.0, u1[0] * u2[0] + u1[1] * u2[1]))
        half_ang = math.acos(dot) / 2.0
        dist = min(r / math.tan(half_ang), l1 * 0.45, l2 * 0.45)
        eff_r = dist * math.tan(half_ang)
        t1 = (p_curr[0] + u1[0] * dist, p_curr[1] + u1[1] * dist)
        t2 = (p_curr[0] + u2[0] * dist, p_curr[1] + u2[1] * dist)

        bis = (u1[0] + u2[0], u1[1] + u2[1])
        bl = math.hypot(*bis)
        ub = (bis[0] / bl, bis[1] / bl)
        c_dist = eff_r / math.sin(half_ang)
        center = (p_curr[0] + ub[0] * c_dist, p_curr[1] + ub[1] * c_dist)

        a1 = math.atan2(t1[1] - center[1], t1[0] - center[0])
        a2 = math.atan2(t2[1] - center[1], t2[0] - center[0])
        da = a2 - a1
        while da <= -math.pi:
            da += 2 * math.pi
        while da > math.pi:
            da -= 2 * math.pi

        for s_idx in range(steps + 1):
            a = a1 + da * s_idx / steps
            poly_pts.append(
                (center[0] + eff_r * math.cos(a), center[1] + eff_r * math.sin(a))
            )
    return poly_pts


def _draw_clipped_annulus(
    d: ImageDraw.ImageDraw,
    cx: float,
    cy: float,
    r_in: float,
    r_out: float,
    x_min: float,
    y_max: float,
    fill: str,
) -> None:
    """Draw a quarter-ring centered at (cx, cy) clipped to x >= x_min and y <= y_max."""
    dx = x_min - cx
    dy = cy - y_max
    a_start_out = -math.acos(max(-1.0, min(1.0, dx / r_out)))
    a_end_out = -math.asin(max(-1.0, min(1.0, dy / r_out)))
    a_start_in = -math.acos(max(-1.0, min(1.0, dx / r_in)))
    a_end_in = -math.asin(max(-1.0, min(1.0, dy / r_in)))

    steps = 72
    pts_out = [
        (
            cx + r_out * math.cos(a_start_out + (a_end_out - a_start_out) * i / steps),
            cy + r_out * math.sin(a_start_out + (a_end_out - a_start_out) * i / steps),
        )
        for i in range(steps + 1)
    ]
    pts_in = [
        (
            cx + r_in * math.cos(a_start_in + (a_end_in - a_start_in) * i / steps),
            cy + r_in * math.sin(a_start_in + (a_end_in - a_start_in) * i / steps),
        )
        for i in range(steps + 1)
    ]
    d.polygon(pts_out + list(reversed(pts_in)), fill=fill)


def _draw_phone_with_knockout(
    d: ImageDraw.ImageDraw,
    pts: list[tuple[float, float]],
    knockout_width: float,
) -> None:
    """Draw a thick NAVY knockout border around pts, then fill pts with WHITE."""
    n = len(pts)
    r = knockout_width / 2
    for i in range(n):
        x0, y0 = pts[i]
        x1, y1 = pts[(i + 1) % n]
        dx, dy = x1 - x0, y1 - y0
        length = math.hypot(dx, dy)
        if length > 0:
            nx, ny = -dy / length * r, dx / length * r
            d.polygon(
                [
                    (x0 + nx, y0 + ny),
                    (x1 + nx, y1 + ny),
                    (x1 - nx, y1 - ny),
                    (x0 - nx, y0 - ny),
                ],
                fill=NAVY,
            )
        d.ellipse([x0 - r, y0 - r, x0 + r, y0 + r], fill=NAVY)
    d.polygon(pts, fill=WHITE)


def draw_icon() -> Image.Image:
    """Draw the 512px RGBA app icon."""
    s = SCALE
    image = Image.new("RGBA", (SIZE * s, SIZE * s), (0, 0, 0, 0))
    d = ImageDraw.Draw(image)

    def box(x0: float, y0: float, x1: float, y1: float) -> list[float]:
        return [x0 * s, y0 * s, x1 * s, y1 * s]

    # 1. Dark navy rounded square background.
    d.rounded_rectangle(box(0, 0, SIZE - 1, SIZE - 1), radius=104 * s, fill=NAVY)

    # 2. Geometric cyan house pentagon with subtly rounded corners, sized so the
    #    left roof slope and walls completely frame the handset without clipping it.
    house_raw = [
        (248 * s, 86 * s),
        (410 * s, 224 * s),
        (360 * s, 412 * s),
        (134 * s, 412 * s),
        (84 * s, 224 * s),
    ]
    d.polygon(_rounded_polygon(house_raw, radii=14 * s), fill=CYAN)

    # 3. Carve out the upper-right quadrant around (cx, cy) and leave the cyan
    #    quarter-circle core node at the center of the house.
    cx, cy = 262 * s, 256 * s
    gap = 16 * s
    core_r = 38 * s
    d.rectangle([cx, 70 * s, 440 * s, cy], fill=NAVY)
    d.pieslice(
        [cx - core_r, cy - core_r, cx + core_r, cy + core_r],
        start=-90,
        end=0,
        fill=CYAN,
    )

    # 4. Three concentric golden signal arcs with uniform radial/axial spacing.
    x_min = cx + gap
    y_max = cy - gap
    for r_in, r_out, col in (
        (54 * s, 76 * s, YELLOW),
        (92 * s, 114 * s, YELLOW),
        (130 * s, 152 * s, GOLD),
    ):
        _draw_clipped_annulus(d, cx, cy, r_in, r_out, x_min, y_max, fill=col)

    # 5. Crisp white handset with dark-navy negative-space knockout outline,
    #    nested cleanly inside the cyan house frame.
    glyph = _mdi_phone_polygon()
    scale = (194 * s) / 24.0
    ox = 232 * s - 12 * scale
    oy = 278 * s - 12 * scale
    phone_pts = [(ox + x * scale, oy + y * scale) for x, y in glyph]
    _draw_phone_with_knockout(d, phone_pts, knockout_width=32 * s)

    return image.resize((SIZE, SIZE), Image.Resampling.LANCZOS)


def main() -> None:
    """Write icon.png, icon@2x.png, logo.png and logo@2x.png."""
    BRAND.mkdir(parents=True, exist_ok=True)
    icon = draw_icon()
    icon.save(BRAND / "icon@2x.png", optimize=True)
    icon.resize((256, 256), Image.Resampling.LANCZOS).save(
        BRAND / "icon.png", optimize=True
    )

    # Pillow bundles Aileron Regular, so regeneration needs no system fonts.
    font = ImageFont.load_default(size=448)
    bounds = font.getbbox("SIP")
    wordmark = Image.new("RGBA", (bounds[2] - bounds[0], bounds[3] - bounds[1]))
    ImageDraw.Draw(wordmark).text(
        (-bounds[0], -bounds[1]), "SIP", font=font, fill=WORDMARK
    )
    gap = 96
    logo = Image.new("RGBA", (SIZE + gap + wordmark.width, SIZE))
    logo.alpha_composite(icon, (0, 0))
    logo.alpha_composite(wordmark, (SIZE + gap, (SIZE - wordmark.height) // 2))
    logo = logo.crop(logo.getbbox())
    logo.save(BRAND / "logo@2x.png", optimize=True)
    logo.resize((logo.width // 2, logo.height // 2), Image.Resampling.LANCZOS).save(
        BRAND / "logo.png", optimize=True
    )


if __name__ == "__main__":
    main()
