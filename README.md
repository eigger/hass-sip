# SIP Client Home Assistant Integration (hass-sip)

**English** | [한국어](README.ko.md)

<!-- hass-standards:badges:start -->
[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5?style=flat-square)](#installation)
[![Release](https://img.shields.io/github/v/release/eigger/hass-sip?style=flat-square)](https://github.com/eigger/hass-sip/releases)
[![Home Assistant minimum](https://img.shields.io/badge/HA-%E2%89%A5%202026.10.0-41BDF5?style=flat-square)](hacs.json)
[![Tests](https://img.shields.io/github/actions/workflow/status/eigger/hass-sip/tests.yml?branch=main&style=flat-square&label=Tests)](https://github.com/eigger/hass-sip/actions/workflows/tests.yml)
[![Validate](https://img.shields.io/github/actions/workflow/status/eigger/hass-sip/validate.yml?branch=main&style=flat-square&label=Validate)](https://github.com/eigger/hass-sip/actions/workflows/validate.yml)
[![License](https://img.shields.io/github/license/eigger/hass-sip?style=flat-square)](LICENSE)
[![Analytics reporting installs](https://img.shields.io/badge/dynamic/json?style=flat-square&color=41BDF5&label=Usage&query=%24.sip.total&url=https%3A%2F%2Fanalytics.home-assistant.io%2Fcustom_integrations.json)](https://analytics.home-assistant.io/)
<!-- hass-standards:badges:end -->

hass-sip **registers as a SIP extension** on your PBX. It exposes the line as a media player, sends and receives DTMF, runs IVR menus, records calls, and can bridge a call to Home Assistant Voice Assist.

Transport is **SIP over UDP** with **G.711 (PCMU/PCMA) and G.722**. TLS, SRTP, and Opus are not implemented. **Internet exposure is unsupported.** Unverified PBXs are not labelled "supported".

| Get registered | Automate | Reference |
|---|---|---|
| [Setup & Quick Start](docs/setup.md) | [Examples](docs/examples.md) | [Services, entities, events](docs/services.md) |
| [PBX compatibility](docs/setup.md#pbx-compatibility) | [Security](docs/security.md) | [Troubleshooting](docs/troubleshooting.md) |
| [ESPHome Component](#esphome-external-component) | [Intercom example](docs/examples.md#intercom--auto-answer-mode) | [espcomponents repo](https://github.com/eigger/espcomponents/tree/master/components/sip_client#readme) |

## What it is for

| Use case | What happens | Details |
|---|---|---|
| Voice control | Dial the extension, answer, talk to Assist — several commands per call | [Use cases](docs/use-cases.md#control-home-assistant-by-voice) |
| Phone keypad | Any phone on the PBX presses digits; an IVR menu runs Home Assistant services | [Use cases](docs/use-cases.md#control-home-assistant-with-the-phone-keypad) |
| Intercom | Door station rings, hass-sip auto-answers, optional DTMF opens the gate | [Use cases](docs/use-cases.md#intercom-auto-answer) |
| Home Assistant calls you | An automation dials your phone and speaks a TTS message | [Use cases](docs/use-cases.md#home-assistant-calls-you-with-tts) |

```yaml
action: sip.start_assist
target:
  entity_id: media_player.phone_line
```

![Phone dials Home Assistant, Assist turns on a light, then sets brightness — storyboard](https://raw.githubusercontent.com/eigger/hass-sip/main/docs/demo/phone-assist.gif)

Door locks: [allow-list + PIN](docs/security.md#door-locks-and-other-security-intents).

## Requirements

Home Assistant 2026.10.0 or newer (`hacs.json`) and a SIP PBX on the same LAN or VPN. This is a HACS custom integration, not part of Home Assistant Core.

## Installation

1. **HACS**: Add this repository (`eigger/hass-sip`) to HACS as a custom repository, or
   **Manual**: Copy the `custom_components/sip` directory into your Home Assistant `custom_components` folder.
2. Restart Home Assistant.

## Get started

On the same LAN as FreePBX:

1. Create a **chan_pjsip** extension (`g722` / `ulaw` / `alaw`, DTMF RFC 4733, Direct Media **No**).
2. **Settings → Devices & Services → Add Integration → SIP Client** — host, port `5060`, username, password.
3. **Registration status** should read `registered`.
4. Dial the extension; **Call Audio** (`audio_path`) should become `bidirectional`.

Field list, firewall notes, and the pjsip snippet: **[Setup](docs/setup.md)**.

## Features

- `media_player` line: TTS or audio URLs into the active call
- Services: `sip.dial`, `sip.hangup`, `sip.answer`, `sip.send_dtmf`, `sip.start_recording`, `sip.stop_recording`, `sip.start_assist` (multi-turn)
- IVR trees with DTMF, PIN, and Home Assistant service actions
- G.722 (16 kHz) when the far end offers it, otherwise G.711; the codec order (or G.711-only) is selectable under **Configure** on the integration
- Sensors: registration, last caller, codec, audio path (`none` / `no_rx` / `no_tx` / `bidirectional`)

## ESPHome External Component

Looking to build a DIY physical endpoint — such as an ESP32 intercom, doorbell, or paging speaker?

Check out the ESPHome external component: [**`sip_client` in eigger/espcomponents**](https://github.com/eigger/espcomponents/tree/master/components/sip_client#readme).

It registers an ESP32 as a SIP extension on your PBX with support for ESPHome standard `microphone` and `speaker` (I2S audio), G.711 / wideband G.722 codecs, DTMF, and full or half-duplex modes. Full configuration, hardware wiring, and YAML examples are documented in the [espcomponents repository](https://github.com/eigger/espcomponents/tree/master/components/sip_client#readme).

## Security

Run hass-sip on the same LAN (or VPN) as the PBX. Do not port-forward UDP 5060 or RTP (`local_rtp_port`, default 7078). For Assist that can unlock a door, use allow-list + DTMF PIN + a dedicated pipeline — **[Security](docs/security.md)**.

## Development

[Contributing](CONTRIBUTING.md) covers the local test setup, ruff, and the real Home Assistant test layout (`tests/components/sip/`). This repository follows [hass-repo-standards](https://github.com/eigger/hass-repo-standards); see [STANDARDS.md](STANDARDS.md).

## Support and license

🐞 [Issue](https://github.com/eigger/hass-sip/issues) · 💡 [Discussion](https://github.com/eigger/hass-sip/discussions)

Released under the [LICENSE](LICENSE) in this repository. Shared development rules: [STANDARDS.md](STANDARDS.md).
