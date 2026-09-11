# SIP Client Home Assistant Integration (hass-sip)

[![GitHub Release](https://img.shields.io/github/v/release/eigger/hass-sip?style=flat-square)](https://github.com/eigger/hass-sip/releases)
[![License](https://img.shields.io/github/license/eigger/hass-sip?style=flat-square)](LICENSE)
[![HACS](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)

hass-sip **registers as a SIP extension** on your PBX. It exposes the line as a media player, sends and receives DTMF, runs IVR menus, records calls, and can bridge a call to Home Assistant Voice Assist.

Transport is **SIP over UDP** with **G.711 (PCMU/PCMA) and G.722**. TLS, SRTP, and Opus are not implemented. **Internet exposure is unsupported.** Unverified PBXs are not labelled "supported".

<!-- Absolute raw URLs so HACS `render_readme` can load images and docs. -->
| Get registered | Automate | Reference |
|---|---|---|
| [Setup & Quick Start](https://raw.githubusercontent.com/eigger/hass-sip/main/docs/setup.md) | [Examples](https://raw.githubusercontent.com/eigger/hass-sip/main/docs/examples.md) | [Services, entities, events](https://raw.githubusercontent.com/eigger/hass-sip/main/docs/services.md) |
| [PBX compatibility](https://raw.githubusercontent.com/eigger/hass-sip/main/docs/setup.md) | [Security](https://raw.githubusercontent.com/eigger/hass-sip/main/docs/security.md) | [Troubleshooting](https://raw.githubusercontent.com/eigger/hass-sip/main/docs/troubleshooting.md) |

## What it is for

### Control Home Assistant from a phone

Call the hass-sip extension, answer, and start Assist. The caller can issue several commands in one call without redialing.

```yaml
action: sip.start_assist
target:
  entity_id: media_player.phone_line
```

![Phone to Assist storyboard — not a live capture](https://raw.githubusercontent.com/eigger/hass-sip/main/docs/demo/phone-assist.gif)

Full flow: [Voice Assist example](https://raw.githubusercontent.com/eigger/hass-sip/main/docs/examples.md). Door locks: [allow-list + PIN](https://raw.githubusercontent.com/eigger/hass-sip/main/docs/security.md).

### Intercom auto-answer

A door station rings the extension; hass-sip answers immediately and opens two-way audio.

```json
{
  "102": { "name": "Front Doorbell", "auto_answer": true }
}
```

![Intercom auto-answer storyboard — not a live capture](https://raw.githubusercontent.com/eigger/hass-sip/main/docs/demo/intercom-autoanswer.gif)

Put that in `sip_contacts.json` (or send SIP auto-answer headers). [Intercom details](https://raw.githubusercontent.com/eigger/hass-sip/main/docs/examples.md).

### Sensor event → phone + TTS

An automation dials a number, speaks a message when the far end answers, then hangs up.

```yaml
action: sip.dial
target:
  entity_id: media_player.phone_line
data:
  number: "100"
  message: "The garage door has been open for ten minutes."
```

![Sensor event to phone TTS storyboard — not a live capture](https://raw.githubusercontent.com/eigger/hass-sip/main/docs/demo/sensor-tts-call.gif)

More TTS options: [Announce a TTS message](https://raw.githubusercontent.com/eigger/hass-sip/main/docs/examples.md).

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

Field list, firewall notes, and the pjsip snippet: **[Setup](https://raw.githubusercontent.com/eigger/hass-sip/main/docs/setup.md)**.

## Features

- `media_player` line: TTS or audio URLs into the active call
- Services: `sip.dial`, `sip.hangup`, `sip.answer`, `sip.send_dtmf`, `sip.start_recording`, `sip.stop_recording`, `sip.start_assist` (multi-turn)
- IVR trees with DTMF, PIN, and Home Assistant service actions
- G.722 (16 kHz) when the far end offers it, otherwise G.711
- Sensors: registration, last caller, codec, audio path (`none` / `no_rx` / `no_tx` / `bidirectional`)

## Security

Run hass-sip on the same LAN (or VPN) as the PBX. Do not port-forward UDP 5060 or RTP (`local_rtp_port`, default 7078). For Assist that can unlock a door, use allow-list + DTMF PIN + a dedicated pipeline — **[Security](https://raw.githubusercontent.com/eigger/hass-sip/main/docs/security.md)**.

## Feedback

🐞 [Issue](https://github.com/eigger/hass-sip/issues) · 💡 [Discussion](https://github.com/eigger/hass-sip/discussions)
