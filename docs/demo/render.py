#!/usr/bin/env python3
"""Render the three README storyboard GIFs (not live captures).

Each GIF is a short comic strip: three actors drawn as icons (a phone, a door
station, a sensor / Home Assistant / a light, a gate, a phone), arrows that
light up for the hop that is happening, and speech bubbles for what is said.
Frames are meant to be understood without reading entity names.

Requires Pillow (``pip install pillow``). Fonts are resolved at runtime:
Arial → DejaVu Sans → Liberation Sans → Pillow's default bitmap font.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parent
W, H = 720, 380
BG = (17, 18, 23)
CARD = (28, 29, 34)
LINE = (48, 50, 58)
TEXT = (232, 232, 232)
MUTED = (150, 153, 160)
DIM = (70, 72, 80)
ACCENT = (3, 169, 244)
OK = (76, 175, 80)
WARN = (255, 171, 0)
BUBBLE_IN = (40, 44, 54)
BUBBLE_HA = (10, 70, 100)

# Actor centres (x) and the row they sit on.
X_LEFT, X_MID, X_RIGHT = 120, 360, 600
Y_ACTOR = 192
ICON = 54  # half-size of the icon box

# (regular, bold). Bold may reuse regular when a face is missing.
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
    (
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
    ),
    (r"C:\Windows\Fonts\arial.ttf", r"C:\Windows\Fonts\arialbd.ttf"),
)


@lru_cache(maxsize=1)
def _font_files() -> tuple[str | None, str | None]:
    """Return (regular, bold) paths, or (None, None) to use load_default."""
    for regular, bold in _FONT_CANDIDATES:
        if not Path(regular).is_file():
            continue
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
# primitives


def text_size(draw: ImageDraw.ImageDraw, s: str, f: ImageFont.ImageFont) -> tuple[int, int]:
    box = draw.textbbox((0, 0), s, font=f)
    return box[2] - box[0], box[3] - box[1]


def centered(draw: ImageDraw.ImageDraw, xy: tuple[int, int], s: str, f: ImageFont.ImageFont, fill) -> None:
    w, h = text_size(draw, s, f)
    draw.text((xy[0] - w // 2, xy[1] - h // 2), s, font=f, fill=fill)


def pill(draw: ImageDraw.ImageDraw, center: tuple[int, int], label: str, color) -> None:
    f = font(12, True)
    w, h = text_size(draw, label, f)
    x, y = center
    draw.rounded_rectangle((x - w // 2 - 10, y - h // 2 - 5, x + w // 2 + 10, y + h // 2 + 5), 11, fill=color)
    centered(draw, (x, y - 1), label, f, BG)


def arrow(draw: ImageDraw.ImageDraw, x0: int, x1: int, y: int, color, width: int = 3, label: str | None = None, dashed: bool = False) -> None:
    """Horizontal arrow from x0 to x1 (either direction) with an optional label above."""
    step = 1 if x1 > x0 else -1
    head = 12
    if dashed:
        x = x0
        while (x1 - x) * step > 18:
            draw.line((x, y, x + 10 * step, y), fill=color, width=width)
            x += 18 * step
    else:
        draw.line((x0, y, x1 - head * step, y), fill=color, width=width)
    draw.polygon([(x1, y), (x1 - head * step, y - 7), (x1 - head * step, y + 7)], fill=color)
    if label:
        centered(draw, ((x0 + x1) // 2, y - 16), label, font(12, True), color)


def bubble(draw: ImageDraw.ImageDraw, anchor: tuple[int, int], lines: list[str], fill, side: str = "right", width: int | None = None) -> None:
    """Speech bubble whose tail points at ``anchor``; grows to the given side."""
    f = font(14)
    pad = 12
    tw = max(text_size(draw, s, f)[0] for s in lines)
    th = 20 * len(lines)
    bw = (width or tw + pad * 2)
    bh = th + pad * 2 - 4
    ax, ay = anchor
    if side == "top":
        x0, x1 = ax - bw // 2, ax + bw // 2
        y0, y1 = ay - 14 - bh, ay - 14
    elif side == "right":
        x0, x1 = ax + 18, ax + 18 + bw
        y0, y1 = ay - bh // 2, ay + bh // 2
    else:
        x0, x1 = ax - 18 - bw, ax - 18
        y0, y1 = ay - bh // 2, ay + bh // 2
    draw.rounded_rectangle((x0, y0, x1, y1), 12, fill=fill)
    if side == "top":
        draw.polygon([(ax - 9, y1), (ax + 9, y1), (ax, ay - 2)], fill=fill)
    elif side == "right":
        draw.polygon([(x0, ay - 8), (x0, ay + 8), (ax + 4, ay)], fill=fill)
    else:
        draw.polygon([(x1, ay - 8), (x1, ay + 8), (ax - 4, ay)], fill=fill)
    y = y0 + pad - 2
    for s in lines:
        draw.text((x0 + pad, y), s, font=f, fill=TEXT)
        y += 20


# --------------------------------------------------------------------------
# icons (drawn, not emoji, so they render identically everywhere)


def icon_phone(draw: ImageDraw.ImageDraw, cx: int, cy: int, active: bool, ringing: bool = False) -> None:
    c = TEXT if active else DIM
    draw.rounded_rectangle((cx - 22, cy - 42, cx + 22, cy + 42), 9, outline=c, width=3, fill=CARD)
    draw.rounded_rectangle((cx - 16, cy - 32, cx + 16, cy + 26), 4, fill=(BUBBLE_IN if active else BG))
    draw.ellipse((cx - 4, cy + 30, cx + 4, cy + 38), fill=c)
    if ringing:
        for r in (30, 40):
            draw.arc((cx - 22 - r, cy - r, cx - 22 + r, cy + r), 200, 250, fill=WARN, width=3)
            draw.arc((cx + 22 - r, cy - r, cx + 22 + r, cy + r), 290, 340, fill=WARN, width=3)


def icon_home(draw: ImageDraw.ImageDraw, cx: int, cy: int, active: bool = True, listening: bool = False) -> None:
    c = ACCENT if active else DIM
    # roof + body
    draw.polygon([(cx - 48, cy - 6), (cx, cy - 46), (cx + 48, cy - 6)], outline=c, fill=CARD, width=3)
    draw.rectangle((cx - 36, cy - 6, cx + 36, cy + 44), outline=c, fill=CARD, width=3)
    draw.line((cx - 36, cy - 6, cx + 36, cy - 6), fill=CARD, width=4)
    if listening:
        # microphone
        draw.rounded_rectangle((cx - 8, cy + 2, cx + 8, cy + 26), 8, fill=OK)
        draw.arc((cx - 15, cy + 6, cx + 15, cy + 34), 0, 180, fill=OK, width=3)
        draw.line((cx, cy + 34, cx, cy + 40), fill=OK, width=3)
    else:
        # door
        draw.rounded_rectangle((cx - 9, cy + 14, cx + 9, cy + 44), 4, fill=c)


def icon_bulb(draw: ImageDraw.ImageDraw, cx: int, cy: int, on: bool, level: float = 1.0) -> None:
    if on:
        glow = (int(255 * level + 60 * (1 - level)), int(213 * level + 60 * (1 - level)), 60)
        for r, a in ((46, 40), (38, 70)):
            draw.ellipse((cx - r, cy - 8 - r, cx + r, cy - 8 + r), fill=(a, a - 5, 20))
        draw.ellipse((cx - 28, cy - 36, cx + 28, cy + 20), fill=glow)
    else:
        draw.ellipse((cx - 28, cy - 36, cx + 28, cy + 20), outline=DIM, width=3, fill=CARD)
    base = TEXT if on else DIM
    draw.rectangle((cx - 12, cy + 20, cx + 12, cy + 30), fill=base)
    draw.rectangle((cx - 9, cy + 32, cx + 9, cy + 38), fill=base)


def icon_doorstation(draw: ImageDraw.ImageDraw, cx: int, cy: int, active: bool, pressed: bool = False, ringing: bool = False) -> None:
    c = TEXT if active else DIM
    draw.rounded_rectangle((cx - 26, cy - 44, cx + 26, cy + 44), 8, outline=c, width=3, fill=CARD)
    draw.ellipse((cx - 12, cy - 30, cx + 12, cy - 6), outline=c, width=3, fill=BG)  # camera
    draw.ellipse((cx - 4, cy - 22, cx + 4, cy - 14), fill=c)
    draw.rounded_rectangle((cx - 16, cy + 6, cx + 16, cy + 18), 3, fill=(BUBBLE_IN if active else BG))  # speaker grille
    btn = WARN if pressed else c
    draw.ellipse((cx - 9, cy + 24, cx + 9, cy + 42), fill=btn)
    if ringing:
        for r in (34, 44):
            draw.arc((cx + 26 - r, cy - r, cx + 26 + r, cy + r), 300, 350, fill=WARN, width=3)


def icon_gate(draw: ImageDraw.ImageDraw, cx: int, cy: int, open_: bool) -> None:
    c = OK if open_ else DIM
    # posts
    draw.rectangle((cx - 44, cy - 40, cx - 36, cy + 44), fill=c)
    draw.rectangle((cx + 36, cy - 40, cx + 44, cy + 44), fill=c)
    if open_:
        # leaves swung outward, seen edge-on as two slim bars leaning away
        draw.line((cx - 38, cy + 40, cx - 62, cy - 26), fill=c, width=6)
        draw.line((cx + 38, cy + 40, cx + 62, cy - 26), fill=c, width=6)
        draw.line((cx - 30, cy - 36, cx + 30, cy - 36), fill=c, width=3)  # top rail stays
    else:
        for i in range(-24, 25, 12):
            draw.line((cx + i, cy - 36, cx + i, cy + 40), fill=c, width=4)
        draw.line((cx - 36, cy - 20, cx + 36, cy - 20), fill=c, width=4)
        draw.line((cx - 36, cy + 20, cx + 36, cy + 20), fill=c, width=4)


def icon_garage(draw: ImageDraw.ImageDraw, cx: int, cy: int, open_: bool) -> None:
    c = WARN if open_ else TEXT
    draw.polygon([(cx - 52, cy - 6), (cx, cy - 44), (cx + 52, cy - 6)], outline=c, fill=CARD, width=3)
    draw.rectangle((cx - 44, cy - 6, cx + 44, cy + 44), outline=c, fill=CARD, width=3)
    draw.line((cx - 44, cy - 6, cx + 44, cy - 6), fill=CARD, width=4)
    if open_:
        draw.rectangle((cx - 34, cy + 2, cx + 34, cy + 12), fill=c)  # door rolled up
        draw.rectangle((cx - 34, cy + 14, cx + 34, cy + 44), fill=BG)
    else:
        for y in range(cy + 4, cy + 44, 9):
            draw.line((cx - 34, y, cx + 34, y), fill=c, width=3)


def icon_dashboard(draw: ImageDraw.ImageDraw, cx: int, cy: int, tapped: bool) -> None:
    c = TEXT if tapped else DIM
    draw.rounded_rectangle((cx - 48, cy - 22, cx + 48, cy + 22), 8, outline=c, width=3, fill=CARD)
    draw.rounded_rectangle((cx - 38, cy - 12, cx + 38, cy + 12), 6, fill=(OK if tapped else BUBBLE_IN))
    centered(draw, (cx, cy), "Open gate", font(12, True), BG if tapped else MUTED)


# --------------------------------------------------------------------------
# scene


@dataclass
class Actor:
    x: int
    label: str
    sub: str = ""


def new_frame(title: str, step: int, total: int) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((14, 14, W - 14, H - 14), 16, fill=CARD, outline=LINE, width=1)
    d.text((30, 26), "STORYBOARD · NOT A LIVE CAPTURE", fill=ACCENT, font=font(11, True))
    d.text((30, 44), title, fill=TEXT, font=font(21, True))
    # step dots
    for i in range(total):
        x = W - 30 - (total - 1 - i) * 18
        d.ellipse((x - 5, 52, x + 5, 62), fill=(ACCENT if i == step else DIM))
    d.line((30, 80, W - 30, 80), fill=LINE, width=1)
    return img, d


def labels(d: ImageDraw.ImageDraw, actors: list[Actor]) -> None:
    for a in actors:
        centered(d, (a.x, Y_ACTOR + ICON + 24), a.label, font(13, True), TEXT)
        if a.sub:
            centered(d, (a.x, Y_ACTOR + ICON + 42), a.sub, font(12), MUTED)


def caption(d: ImageDraw.ImageDraw, text: str) -> None:
    centered(d, (W // 2, H - 38), text, font(14), MUTED)


def save(name: str, frames: list[Image.Image], duration: int = 1900) -> None:
    path = OUT / name
    durations = [duration] * len(frames)
    durations[-1] = duration + 900  # linger on the ending
    frames[0].save(
        path,
        save_all=True,
        append_images=frames[1:],
        duration=durations,
        loop=0,
        optimize=True,
        disposal=2,
    )
    print(f"{path.name} {path.stat().st_size // 1024} KiB")


# --------------------------------------------------------------------------
# storyboards

Y_ARROW = Y_ACTOR - 4


def phone_assist() -> None:
    title = "Control Home Assistant from a phone"
    actors = [Actor(X_LEFT, "Your phone", "ext 100"), Actor(X_MID, "Home Assistant", "hass-sip · ext 1001"), Actor(X_RIGHT, "Kitchen light")]
    seq: list[Image.Image] = []
    total = 4

    # 1. call rings HA
    img, d = new_frame(title, 0, total)
    icon_phone(d, X_LEFT, Y_ACTOR, True, ringing=False)
    icon_home(d, X_MID, Y_ACTOR)
    icon_bulb(d, X_RIGHT, Y_ACTOR, on=False)
    arrow(d, X_LEFT + 40, X_MID - 60, Y_ARROW, WARN, label="dial 1001")
    arrow(d, X_MID + 60, X_RIGHT - 50, Y_ARROW, DIM, dashed=True)
    pill(d, (X_MID, Y_ACTOR - 74), "RINGING", WARN)
    labels(d, actors)
    caption(d, "You dial the hass-sip extension from any phone on the PBX.")
    seq.append(img)

    # 2. answered, Assist listening
    img, d = new_frame(title, 1, total)
    icon_phone(d, X_LEFT, Y_ACTOR, True)
    icon_home(d, X_MID, Y_ACTOR, listening=True)
    icon_bulb(d, X_RIGHT, Y_ACTOR, on=False)
    arrow(d, X_LEFT + 40, X_MID - 60, Y_ARROW, OK, label="call connected")
    arrow(d, X_MID + 60, X_RIGHT - 50, Y_ARROW, DIM, dashed=True)
    pill(d, (X_MID, Y_ACTOR - 74), "ASSIST LISTENING", OK)
    labels(d, actors)
    caption(d, "sip.answer + sip.start_assist — the call becomes a voice session.")
    seq.append(img)

    # 3. first command → light on
    img, d = new_frame(title, 2, total)
    icon_phone(d, X_LEFT, Y_ACTOR, True)
    icon_home(d, X_MID, Y_ACTOR, listening=True)
    icon_bulb(d, X_RIGHT, Y_ACTOR, on=True)
    arrow(d, X_LEFT + 40, X_MID - 60, Y_ARROW, ACCENT)
    arrow(d, X_MID + 60, X_RIGHT - 50, Y_ARROW, OK, label="light.turn_on")
    bubble(d, (X_LEFT + 26, Y_ACTOR - 66), ['"Turn on the kitchen light"'], BUBBLE_IN, side="right")
    labels(d, actors)
    caption(d, '→ "Kitchen light is on."  Still listening — no redial.')
    seq.append(img)

    # 4. follow-up in the same call
    img, d = new_frame(title, 3, total)
    icon_phone(d, X_LEFT, Y_ACTOR, True)
    icon_home(d, X_MID, Y_ACTOR, listening=True)
    icon_bulb(d, X_RIGHT, Y_ACTOR, on=True, level=0.55)
    arrow(d, X_LEFT + 40, X_MID - 60, Y_ARROW, ACCENT)
    arrow(d, X_MID + 60, X_RIGHT - 50, Y_ARROW, OK, label="brightness 50%")
    bubble(d, (X_LEFT + 26, Y_ACTOR - 66), ['"Set it to 50%"'], BUBBLE_IN, side="right")
    pill(d, (X_MID, Y_ACTOR - 74), "SAME CONVERSATION", ACCENT)
    labels(d, actors)
    caption(d, "Follow-ups keep context: \"it\" still means the kitchen light.")
    seq.append(img)

    save("phone-assist.gif", seq)


def intercom() -> None:
    title = "Intercom auto-answer"
    actors = [Actor(X_LEFT, "Door station", "ext 102"), Actor(X_MID, "Home Assistant", "hass-sip · ext 1001"), Actor(X_RIGHT, "Front gate")]
    seq: list[Image.Image] = []
    total = 3

    # 1. visitor presses the bell
    img, d = new_frame(title, 0, total)
    icon_doorstation(d, X_LEFT, Y_ACTOR, True, pressed=True, ringing=True)
    icon_home(d, X_MID, Y_ACTOR)
    icon_gate(d, X_RIGHT, Y_ACTOR, open_=False)
    arrow(d, X_LEFT + 44, X_MID - 60, Y_ARROW, WARN, label="rings 1001")
    arrow(d, X_MID + 60, X_RIGHT - 56, Y_ARROW, DIM, dashed=True)
    labels(d, actors)
    caption(d, "A visitor presses the bell. The door station calls the hass-sip extension.")
    seq.append(img)

    # 2. auto-answered, two-way audio
    img, d = new_frame(title, 1, total)
    icon_doorstation(d, X_LEFT, Y_ACTOR, True)
    icon_home(d, X_MID, Y_ACTOR, listening=True)
    icon_gate(d, X_RIGHT, Y_ACTOR, open_=False)
    arrow(d, X_LEFT + 44, X_MID - 60, Y_ARROW - 10, OK)
    arrow(d, X_MID - 60, X_LEFT + 44, Y_ARROW + 10, OK)
    centered(d, ((X_LEFT + X_MID) // 2, Y_ARROW - 28), "two-way audio", font(12, True), OK)
    arrow(d, X_MID + 60, X_RIGHT - 56, Y_ARROW, DIM, dashed=True)
    pill(d, (X_MID, Y_ACTOR - 74), "ANSWERED INSTANTLY", OK)
    labels(d, actors)
    caption(d, 'sip_contacts.json: "102": { "auto_answer": true } — no ring, no tap.')
    seq.append(img)

    # 3. release the gate with DTMF
    img, d = new_frame(title, 2, total)
    icon_doorstation(d, X_LEFT, Y_ACTOR, True)
    icon_home(d, X_MID, Y_ACTOR, listening=True)
    icon_gate(d, X_RIGHT, Y_ACTOR, open_=True)
    arrow(d, X_MID - 60, X_LEFT + 44, Y_ARROW, OK, label='DTMF "1"')
    arrow(d, X_MID + 60, X_RIGHT - 70, Y_ARROW, DIM, dashed=True)
    icon_dashboard(d, X_MID, Y_ARROW - 66, tapped=True)
    pill(d, (X_RIGHT, Y_ACTOR - 74), "OPEN", OK)
    labels(d, actors)
    caption(d, "Tap a dashboard button → sip.send_dtmf → the door station releases the gate.")
    seq.append(img)

    save("intercom-autoanswer.gif", seq)


def sensor_call() -> None:
    title = "Sensor event → phone call + TTS"
    actors = [Actor(X_LEFT, "Garage door", "binary_sensor"), Actor(X_MID, "Home Assistant", "hass-sip · ext 1001"), Actor(X_RIGHT, "Your phone", "ext 100")]
    seq: list[Image.Image] = []
    total = 3

    # 1. trigger
    img, d = new_frame(title, 0, total)
    icon_garage(d, X_LEFT, Y_ACTOR, open_=True)
    icon_home(d, X_MID, Y_ACTOR)
    icon_phone(d, X_RIGHT, Y_ACTOR, False)
    arrow(d, X_LEFT + 56, X_MID - 60, Y_ARROW, WARN, label="open for 10 min")
    arrow(d, X_MID + 60, X_RIGHT - 40, Y_ARROW, DIM, dashed=True)
    pill(d, (X_MID, Y_ACTOR - 74), "AUTOMATION TRIGGERS", WARN)
    labels(d, actors)
    caption(d, "Any Home Assistant trigger can start a call.")
    seq.append(img)

    # 2. dial out
    img, d = new_frame(title, 1, total)
    icon_garage(d, X_LEFT, Y_ACTOR, open_=True)
    icon_home(d, X_MID, Y_ACTOR)
    icon_phone(d, X_RIGHT, Y_ACTOR, True, ringing=True)
    arrow(d, X_LEFT + 56, X_MID - 60, Y_ARROW, DIM, dashed=True)
    arrow(d, X_MID + 60, X_RIGHT - 40, Y_ARROW, ACCENT, label="sip.dial 100")
    pill(d, (X_RIGHT, Y_ACTOR - 74), "RINGING", WARN)
    labels(d, actors)
    caption(d, "hass-sip is a registered extension, so it can place outbound calls.")
    seq.append(img)

    # 3. speak, then hang up
    img, d = new_frame(title, 2, total)
    icon_garage(d, X_LEFT, Y_ACTOR, open_=True)
    icon_home(d, X_MID, Y_ACTOR)
    icon_phone(d, X_RIGHT, Y_ACTOR, True)
    arrow(d, X_LEFT + 56, X_MID - 60, Y_ARROW, DIM, dashed=True)
    arrow(d, X_MID + 60, X_RIGHT - 40, Y_ARROW, OK, label="TTS")
    bubble(d, (X_MID, Y_ACTOR - 48), ['"The garage door has been open for ten minutes."'], BUBBLE_HA, side="top")
    pill(d, (X_RIGHT, Y_ACTOR - 74), "then hangs up", DIM)
    labels(d, actors)
    caption(d, "You answer, hear the message, and the call ends by itself.")
    seq.append(img)

    save("sensor-tts-call.gif", seq)


if __name__ == "__main__":
    phone_assist()
    intercom()
    sensor_call()
