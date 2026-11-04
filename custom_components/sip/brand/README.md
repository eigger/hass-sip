# SIP brand assets

An original geometric brand icon (a cyan Home Assistant house pentagon whose
upper-right quadrant opens into concentric golden signal arcs, fused with a
crisp white telephone handset outlined in negative space) drawn by
`docs/brand/render.py` on a dark navy rounded square, with a matching cyan
`SIP` wordmark for the landscape logo. No third-party artwork is used.

| File | Size | Purpose |
| --- | --- | --- |
| `icon.png` | 256 × 256 | Home Assistant and HACS icon |
| `icon@2x.png` | 512 × 512 | High-resolution icon |
| `logo.png` | 625 × 256 | Landscape integration logo; README artwork |
| `logo@2x.png` | 1251 × 512 | High-resolution landscape logo |

All images are RGBA PNGs with transparent outer corners and backgrounds. These
sizes follow the [HA brand image guidelines](https://contributors.home-assistant.io/core/integrations/brands/).
Home Assistant 2026.3 and later serves these assets from the integration's local
`brand/` directory. The cyan wordmark works against light and dark backgrounds,
so separate dark variants are not required.

## Source and permission

The icon is drawn from geometric primitives in this repository's
`docs/brand/render.py` and is covered by the repository's MIT license
(Copyright (c) 2026 eigger). The handset contour follows Material Design Icons
`phone` (Pictogrammers, Apache 2.0 / MIT compatible). The wordmark is rendered
with Pillow's bundled Aileron Regular via
[`ImageFont.load_default`](https://pillow.readthedocs.io/en/stable/reference/ImageFont.html#PIL.ImageFont.load_default).
[Aileron](https://dotcolon.net/font/aileron/) permits commercial use and is
released with no rights reserved. No font file is redistributed or loaded from
the system.

## Reproduce

Use Python 3.10 or newer and Pillow 12.3.0 (also supplied by HA 2026.10.2):

```sh
python -m pip install Pillow==12.3.0
python docs/brand/render.py
```

The script needs no network access and produces the same PNG bytes on every
run with that Pillow version.

SHA256 (Pillow 12.3.0):

- `icon@2x.png`: `95722f9e258ebaa180b22e8de36b1c9307b3211265a3a638bafb6bd588160f73`
- `icon.png`: `3881067e708a256eb18c3344a788f6058f6dba2ff1b06ff72e44e25453a5671a`
- `logo@2x.png`: `70a89b3d6069b56bceba923015e7ba015f38c6029a662cfaced7daa0d5ec44e0`
- `logo.png`: `35545563f646b876e27c45be5b29798186519d3eb139cc01802477578c212f55`

The local `brand/icon.png` is used by the
[HACS brands check](https://hacs.xyz/docs/publish/include/#check-brands).
README pages use `custom_components/sip/brand/logo.png`.
