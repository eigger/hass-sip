# SIP Client Home Assistant Integration (hass-sip)

[![GitHub Release](https://img.shields.io/github/v/release/eigger/hass-sip?style=flat-square)](https://github.com/eigger/hass-sip/releases)
[![License](https://img.shields.io/github/license/eigger/hass-sip?style=flat-square)](LICENSE)
[![HACS](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
![integration usage](https://img.shields.io/badge/dynamic/json?color=41BDF5&logo=home-assistant&label=usage&suffix=%20installs&cacheSeconds=15600&query=%24.sip.total&url=https%3A%2F%2Fanalytics.home-assistant.io%2Fcustom_integrations.json)

hass-sip **registers as a SIP extension** on your PBX. It exposes the line as a media player, sends and receives DTMF, runs IVR menus, records calls, and can bridge a call to Home Assistant Voice Assist.

Transport is **SIP over UDP** with **G.711 (PCMU/PCMA) and G.722**. TLS, SRTP, and Opus are not implemented. **Internet exposure is unsupported.** Unverified PBXs are not labelled "supported".

| Get registered | Automate | Reference |
|---|---|---|
| [Setup & Quick Start](docs/setup.md) | [Examples](docs/examples.md) | [Services, entities, events](docs/services.md) |
| [PBX compatibility](docs/setup.md#pbx-compatibility) | [Security](docs/security.md) | [Troubleshooting](docs/troubleshooting.md) |

## What it is for

### Control Home Assistant from a phone

Dial the hass-sip extension → answer → Assist. Several commands in one call, no redial.

```yaml
action: sip.start_assist
target:
  entity_id: media_player.phone_line
```

![Phone dials Home Assistant, Assist turns on a light, then sets brightness — storyboard](https://raw.githubusercontent.com/eigger/hass-sip/main/docs/demo/phone-assist.gif)

Full flow: [Voice Assist example](docs/examples.md#voice-assist-automation-example). Door locks: [allow-list + PIN](docs/security.md#door-locks-and-other-security-intents).

### Intercom auto-answer

Door station rings → hass-sip answers immediately → optional DTMF to open the gate.

```json
{
  "102": { "name": "Front Doorbell", "auto_answer": true }
}
```

![Door station rings Home Assistant, auto-answer opens audio, dashboard DTMF opens the gate — storyboard](https://raw.githubusercontent.com/eigger/hass-sip/main/docs/demo/intercom-autoanswer.gif)

Put that in `sip_contacts.json` (or send SIP auto-answer headers). [Intercom details](docs/examples.md#intercom--auto-answer-mode).

### Sensor event → phone + TTS

A sensor fires → hass-sip dials your phone → speaks a message → hangs up.

```yaml
action: sip.dial
target:
  entity_id: media_player.phone_line
data:
  number: "100"
  message: "The garage door has been open for ten minutes."
```

![Garage sensor triggers Home Assistant, which dials your phone and speaks a TTS warning — storyboard](https://raw.githubusercontent.com/eigger/hass-sip/main/docs/demo/sensor-tts-call.gif)

More TTS options: [Announce a TTS message](docs/examples.md#example-announce-a-tts-message-then-hang-up).

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
- G.722 (16 kHz) when the far end offers it, otherwise G.711
- Sensors: registration, last caller, codec, audio path (`none` / `no_rx` / `no_tx` / `bidirectional`)

## Security

Run hass-sip on the same LAN (or VPN) as the PBX. Do not port-forward UDP 5060 or RTP (`local_rtp_port`, default 7078). For Assist that can unlock a door, use allow-list + DTMF PIN + a dedicated pipeline — **[Security](docs/security.md)**.

## Feedback

🐞 [Issue](https://github.com/eigger/hass-sip/issues) · 💡 [Discussion](https://github.com/eigger/hass-sip/discussions)
