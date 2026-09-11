#!/usr/bin/env python3
"""Render the three README storyboard GIFs (not live captures).

Requires Pillow (``pip install pillow``). Fonts are resolved at runtime:
Arial → DejaVu Sans → Liberation Sans → Pillow's default bitmap font.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parent
W, H = 640, 300
BG = (17, 18, 23)
CARD = (28, 29, 34)
LINE = (48, 50, 58)
TEXT = (225, 225, 225)
MUTED = (155, 158, 166)
ACCENT = (3, 169, 244)
OK = (67, 160, 71)
WARN = (255, 166, 0)

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


def new_frame(kicker: str, title: str) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    img = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle((16, 16, W - 16, H - 16), 16, fill=CARD, outline=LINE, width=1)
    draw.text((32, 28), kicker.upper(), fill=ACCENT, font=font(11, True))
    draw.text((32, 48), title, fill=TEXT, font=font(20, True))
    draw.line((32, 82, W - 32, 82), fill=LINE, width=1)
    return img, draw


def pill(draw: ImageDraw.ImageDraw, xy: tuple[int, int], label: str, color: tuple[int, int, int]) -> None:
    x, y = xy
    pad_x, pad_y = 10, 4
    bbox = draw.textbbox((0, 0), label, font=font(12, True))
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.rounded_rectangle((x, y, x + tw + pad_x * 2, y + th + pad_y * 2), 10, fill=color)
    draw.text((x + pad_x, y + pad_y - 1), label, fill=(17, 18, 23), font=font(12, True))


def rows(draw: ImageDraw.ImageDraw, items: list[tuple[str, str]]) -> None:
    y = 100
    for name, value in items:
        draw.text((32, y), name, fill=MUTED, font=font(13))
        draw.text((250, y), value, fill=TEXT, font=font(13, True))
        y += 28


def caption(draw: ImageDraw.ImageDraw, text: str) -> None:
    draw.text((32, H - 48), text, fill=MUTED, font=font(12))


def save(name: str, frames: list[Image.Image], duration: int = 1600) -> None:
    path = OUT / name
    frames[0].save(
        path,
        save_all=True,
        append_images=frames[1:],
        duration=duration,
        loop=0,
        optimize=True,
        disposal=2,
    )
    print(f"{path.name} {path.stat().st_size // 1024} KiB")


def phone_assist() -> None:
    seq: list[Image.Image] = []

    img, draw = new_frame("Storyboard · not a live capture", "Phone → Assist")
    rows(draw, [("media_player.phone_line", "ringing"), ("last_caller", "100  Dad"), ("action", "sip.answer")])
    pill(draw, (480, 100), "INCOMING", WARN)
    caption(draw, "Desk phone dials the hass-sip extension.")
    seq.append(img)

    img, draw = new_frame("Storyboard · not a live capture", "Phone → Assist")
    rows(draw, [("media_player.phone_line", "on"), ("service", "sip.start_assist"), ("session", "listening")])
    pill(draw, (480, 100), "ASSIST", ACCENT)
    caption(draw, "Bridge the call. Several commands in one session.")
    seq.append(img)

    img, draw = new_frame("Storyboard · not a live capture", "Phone → Assist")
    rows(draw, [("caller", '"Turn on the kitchen light"'), ("Assist", "Kitchen light is on."), ("next", "still listening")])
    pill(draw, (480, 100), "TURN 1", OK)
    caption(draw, "No hangup between commands.")
    seq.append(img)

    img, draw = new_frame("Storyboard · not a live capture", "Phone → Assist")
    rows(draw, [("caller", '"Set it to 50%"'), ("Assist", "Kitchen light set to 50%."), ("context", "same conversation")])
    pill(draw, (480, 100), "TURN 2", OK)
    caption(draw, "Conversation context is kept across turns.")
    seq.append(img)

    save("phone-assist.gif", seq)


def intercom() -> None:
    seq: list[Image.Image] = []

    img, draw = new_frame("Storyboard · not a live capture", "Intercom auto-answer")
    rows(draw, [("caller", "102  Front Doorbell"), ("sip_contacts.json", "auto_answer: true"), ("ring", "skipped")])
    pill(draw, (470, 100), "INVITE", WARN)
    caption(draw, "Door station rings the hass-sip extension.")
    seq.append(img)

    img, draw = new_frame("Storyboard · not a live capture", "Intercom auto-answer")
    rows(draw, [("media_player.phone_line", "on"), ("Call Audio", "bidirectional"), ("codec", "G.722")])
    pill(draw, (470, 100), "ANSWERED", OK)
    caption(draw, "Channel opens immediately — no dashboard tap.")
    seq.append(img)

    img, draw = new_frame("Storyboard · not a live capture", "Intercom auto-answer")
    rows(draw, [("dashboard", "Open Front Gate"), ("service", "sip.send_dtmf"), ("digits", "1")])
    pill(draw, (470, 100), "DTMF", ACCENT)
    caption(draw, "Optional: release the lock with a DTMF digit.")
    seq.append(img)

    save("intercom-autoanswer.gif", seq)


def sensor_call() -> None:
    seq: list[Image.Image] = []

    img, draw = new_frame("Storyboard · not a live capture", "Sensor → phone + TTS")
    rows(draw, [("binary_sensor.garage_door", "on for 10 min"), ("automation", "fires"), ("next", "sip.dial")])
    pill(draw, (470, 100), "TRIGGER", WARN)
    caption(draw, "A Home Assistant state change starts the call.")
    seq.append(img)

    img, draw = new_frame("Storyboard · not a live capture", "Sensor → phone + TTS")
    rows(draw, [("service", "sip.dial"), ("number", "100"), ("state", "calling")])
    pill(draw, (470, 100), "DIALING", ACCENT)
    caption(draw, "hass-sip registers on the PBX, so it can originate.")
    seq.append(img)

    img, draw = new_frame("Storyboard · not a live capture", "Sensor → phone + TTS")
    rows(
        draw,
        [
            ("connected", "yes"),
            ("TTS", '"The garage door has been open for ten minutes."'),
            ("after", "hang up"),
        ],
    )
    pill(draw, (470, 100), "SPEAKING", OK)
    caption(draw, "Speak the message, then hang up automatically.")
    seq.append(img)

    save("sensor-tts-call.gif", seq)


if __name__ == "__main__":
    phone_assist()
    intercom()
    sensor_call()
