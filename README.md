# SIP Client Home Assistant Integration (hass-sip)

[![GitHub Release](https://img.shields.io/github/v/release/eigger/hass-sip?style=flat-square)](https://github.com/eigger/hass-sip/releases)
[![License](https://img.shields.io/github/license/eigger/hass-sip?style=flat-square)](LICENSE)
[![HACS](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
![integration usage](https://img.shields.io/badge/dynamic/json?color=41BDF5&logo=home-assistant&label=usage&suffix=%20installs&cacheSeconds=15600&query=%24.sip.total&url=https%3A%2F%2Fanalytics.home-assistant.io%2Fcustom_integrations.json)

A native custom integration for Home Assistant to connect directly to a SIP server or PBX (such as FreePBX, Asterisk, or any VoIP provider). It exposes the telephone line as a native media player, allows DTMF control, provides an Interactive Voice Response (IVR) menu engine, and bridges calls directly to Home Assistant's Voice Assist.

## 💬 Feedback & Support

🐞 Found a bug? Let us know via an [Issue](https://github.com/eigger/hass-sip/issues).  
💡 Have a question or suggestion? Join the [Discussion](https://github.com/eigger/hass-sip/discussions)!

## Features & Platforms

- **Native Media Player Entity**: Exposes the SIP line as a `media_player` entity. Stream standard TTS messages (e.g. Google Translate, Piper, Nabu Casa) or audio URLs directly into the active SIP call.
- **Custom Telephony Services**: Complete set of services to control SIP calls (`sip.dial`, `sip.hangup`, `sip.answer`, `sip.send_dtmf`, `sip.start_recording`, `sip.stop_recording`, `sip.start_assist`).
- **Interactive Voice Response (IVR) Engine**: Construct nested DTMF automated phone trees with TTS prompt templates, custom PIN authentication, and native Home Assistant service triggers.
- **Voice Assist Integration**: Bidirectional audio streaming between the SIP call and Home Assistant's Voice Assist pipeline, utilizing active 8kHz to 16kHz resampling.
- **Sensors**: Exposes real-time registration status, call state (line active), and last caller ID.

## Installation

1. **HACS**: Add this repository (`eigger/hass-sip`) to HACS as a custom repository, or 
   **Manual**: Copy the `custom_components/sip` directory into your Home Assistant `custom_components` folder.
2. Restart Home Assistant.

## Configuration

Device setup is done entirely through the Home Assistant UI.

1. Go to **Settings** > **Devices & Services**.
2. Click **Add Integration** and search for **SIP Client**.
3. Fill out the configuration fields:
   - **Server / Host**: IP address or hostname of your SIP server (e.g., FreePBX or Asterisk).
   - **Port**: SIP server port (default: `5060`).
   - **Username**: SIP authentication username/extension.
   - **Password**: SIP authentication password.
   - **Domain** *(Optional)*: SIP Domain/Realm (defaults to Server).
   - **Caller ID** *(Optional)*: Caller display name.
   - **RTP Port** *(Optional)*: Base local RTP port for audio stream (default: `7078`).

---

## Services

This integration registers the following services under the `sip` domain:

### `sip.dial`
Initiates an outbound SIP call.
- `entity_id` *(Required)*: The target SIP media player entity (e.g. `media_player.phone_line`).
- `number` *(Required)*: The destination number or SIP URI to call (e.g., `100` or `sip:100@freepbx`).
- `ring_timeout` *(Optional)*: Number of seconds to let the call ring before canceling (e.g., `30`).
- `menu` *(Optional)*: IVR menu configuration object (see below).
- `message` *(Optional)*: Text message to speak via TTS upon call connection. If provided without a menu, the call will automatically hang up after speaking. If both `menu` and `message` are provided, `menu` takes precedence and `message` is ignored.
- `tts_engine` *(Optional)*: Specific TTS engine to use (e.g., `tts.google_translate`, `tts.piper`).
- `language` *(Optional)*: Optional language code for TTS (e.g., `ko`, `en`).
- `tts_options` *(Optional)*: Dictionary of extra voice or speech settings (e.g., dynamic parameters).

### `sip.hangup`
Ends an active SIP call or declines an incoming call.
- `entity_id` *(Required)*: The target SIP media player entity.
- `sip_code` *(Optional)*: Optional status code to send if rejecting an incoming call (e.g., `486` for Busy Here).

### `sip.answer`
Answers an incoming SIP call.
- `entity_id` *(Required)*: The target SIP media player entity.
- `menu` *(Optional)*: IVR menu configuration object to start immediately on answer.
- `message` *(Optional)*: Text message to speak via TTS upon answering. If provided without a menu, the call will automatically hang up after speaking. If both `menu` and `message` are provided, `menu` takes precedence and `message` is ignored.
- `tts_engine` *(Optional)*: Specific TTS engine to use (e.g., `tts.google_translate`, `tts.piper`).
- `language` *(Optional)*: Optional language code for TTS.
- `tts_options` *(Optional)*: Dictionary of extra voice or speech settings.

### `sip.send_dtmf`
Sends DTMF digits to the active SIP call.
- `entity_id` *(Required)*: The target SIP media player entity.
- `digits` *(Required)*: DTMF string to send (e.g., `123#`).

### `sip.start_recording`
Starts recording call audio to a local WAV file.
- `entity_id` *(Required)*: The target SIP media player entity.
- `recording_file` *(Required)*: Absolute path of the WAV file to save (e.g., `/media/recording.wav`).

### `sip.stop_recording`
Stops active call recording.
- `entity_id` *(Required)*: The target SIP media player entity.

### `sip.start_assist`
Bridges the active call directly to Home Assistant's Voice Assist.
- `entity_id` *(Required)*: The target SIP media player entity.

---

## Events

The integration fires the following events on the Home Assistant event bus. Every event includes `sip_account` (the SIP username) and `server`, plus the extra fields noted below.

| Event | Extra data | Fired when |
|-------|-----------|------------|
| `sip_registered` | – | Successfully registered with the PBX |
| `sip_state_changed` | `state` | The SIP line state changes (`idle`, `registering`, `registered`, `inviting`, `ringing_out`, `incoming`, `answering`, `in_call`) |
| `sip_incoming_call` | `caller`, `caller_name` | An inbound call arrives |
| `sip_call_connected` | – | A call becomes two-way connected (use this before playing media) |
| `sip_playback_done` | – | A TTS/audio source has **finished transmitting** to the remote party |
| `sip_call_ended` | – | The call ended (either side hung up) |
| `sip_dtmf_digit` | `digit` | A DTMF digit was received from the remote party |
| `sip_recording_started` | `recording_file` | Call recording started |
| `sip_recording_stopped` | – | Call recording stopped |

> `sip_call_connected` and `sip_playback_done` are the two events you want for "answer → speak → hang up" flows: wait for the call to connect before playing media, and wait for playback to finish before hanging up so the message is never cut off.

---

## Example: Announce a TTS message, then hang up

Any standard Home Assistant TTS engine works (Google Translate, Piper, Nabu Casa Cloud, etc.) — the line audio is transcoded with ffmpeg automatically.

There are two ways to announce messages:

### Option A: Simplified Parameters (Recommended)
You can specify the TTS message and settings directly in the `sip.answer` or `sip.dial` service call. The integration will automatically wait for the connection, speak the message, and hang up when finished.

#### Inbound — answer, speak, and hang up automatically
```yaml
alias: "SIP: Announce on incoming call (Simplified)"
trigger:
  - platform: event
    event_type: sip_incoming_call
action:
  - service: sip.answer
    target:
      entity_id: media_player.phone_line
    data:
      message: "Hello, this is an automated response."
      tts_engine: tts.google_translate
      language: en
```

#### Outbound — dial, speak when answered, and hang up automatically
```yaml
alias: "SIP: Announce on outbound call (Simplified)"
action:
  - service: sip.dial
    target:
      entity_id: media_player.phone_line
    data:
      number: "100"
      ring_timeout: 30
      message: "A package has been delivered."
      tts_engine: tts.google_translate
      language: en
```

---

### Option B: Multi-step Automation (Advanced)
If you need complex scripting or conditional flows between answering, speaking, and hanging up, you can orchestrate it using Home Assistant events (`sip_call_connected` and `sip_playback_done`).

#### Inbound (Multi-step)
```yaml
alias: "SIP: Announce on incoming call (Multi-step)"
trigger:
  - platform: event
    event_type: sip_incoming_call
action:
  - service: sip.answer
    target:
      entity_id: media_player.phone_line
  # Wait until the call is actually two-way connected
  - wait_for_trigger:
      - platform: event
        event_type: sip_call_connected
    timeout: "00:00:10"
  - service: tts.speak
    target:
      entity_id: tts.google_translate
    data:
      media_player_entity_id: media_player.phone_line
      message: "Hello, this is an automated response."
  # Wait until the whole message has been sent (prevents truncation)
  - wait_for_trigger:
      - platform: event
        event_type: sip_playback_done
    timeout: "00:00:30"
  - service: sip.hangup
    target:
      entity_id: media_player.phone_line
```

#### Outbound (Multi-step)
```yaml
alias: "SIP: Announce on outbound call (Multi-step)"
action:
  - service: sip.dial
    target:
      entity_id: media_player.phone_line
    data:
      number: "100"
      ring_timeout: 30
  # Fires when the remote party answers
  - wait_for_trigger:
      - platform: event
        event_type: sip_call_connected
    timeout: "00:00:35"
  - service: tts.speak
    target:
      entity_id: tts.google_translate
    data:
      media_player_entity_id: media_player.phone_line
      message: "A package has been delivered."
  - wait_for_trigger:
      - platform: event
        event_type: sip_playback_done
    timeout: "00:00:30"
  - service: sip.hangup
    target:
      entity_id: media_player.phone_line
```

> Instead of `tts.speak` you can also call `media_player.play_media` with a `media-source://tts/...` id, or a plain audio file URL, on the same entity.

---

## IVR Configuration Example

You can pass a YAML configuration schema to the `menu` field in the `sip.dial` or `sip.answer` service. You can group TTS settings in a dedicated `tts` block for menus and choices.

```yaml
service: sip.answer
target:
  entity_id: media_player.phone_line
data:
  menu:
    id: root
    tts:
      message: "Welcome to our Home. Press 1 to toggle the living room light. Press 2 to talk to our Voice Assistant. Or enter your four-digit PIN code followed by hash."
      engine: tts.google_translate
      language: en
    wait_for_audio_to_finish: true
    timeout: 10
    choices_are_pin: false
    choices:
      "1":
        action:
          domain: light
          service: toggle
          entity_id: light.living_room_light
        tts:
          message: "Toggling the light now."
          engine: tts.google_translate
          language: en
        post_action: hangup
      "2":
        action:
          domain: assist_pipeline
        post_action: noop
      "default":
        tts:
          message: "Invalid selection."
          engine: tts.google_translate
          language: en
        post_action: repeat_message
      "timeout":
        post_action: hangup
```

### IVR TTS Configuration Options
Within the `tts` block of a menu or a choice, you can specify the following parameters:
- `message` (or `text`): The text content to be spoken.
- `engine` (or `tts_engine`): The specific TTS engine to use (e.g., `tts.google_translate`, `tts.piper`).
- `language` (or `lang`): Optional language code (e.g., `en`, `ko`).
- `options` (or `tts_options`): A dictionary of voice-specific configurations.
- `handle_as_template`: A boolean value (`true` or `false`). When set to `true`, the text in `message`/`text` will be rendered as a Home Assistant Jinja template before speaking.

---

## Contacts & Caller ID Mapping

You can map incoming numbers or extensions to friendly names. Create a file named `sip_contacts.json` in your Home Assistant configuration directory (e.g. `/config/` or `/homeassistant/`):

```json
{
  "100": "Dad",
  "101": "Mom",
  "102": {
    "name": "Front Doorbell",
    "auto_answer": true
  }
}
```

If mapped, the `last_call` Friendly Name sensor will display the contact name instead of the raw number. It also exposes a `caller_name` attribute in the `sip_incoming_call` event.

---

## Intercom & Auto-Answer Mode

The integration can automatically answer incoming calls (useful for intercoms and doorbells). It triggers in two ways:
1. **SIP Headers**: The incoming call includes standard auto-answer headers like `Call-Info: ...; answer-after=0` or `Alert-Info: Ring Answer`.
2. **Contacts Configuration**: The incoming caller ID matches an extension marked with `"auto_answer": true` in `sip_contacts.json`.

When triggered, the integration answers immediately, opens a two-way audio channel, and bypasses the ringing phase.

---

## Voice Assist Automation Example

You can automatically bridge incoming calls directly to Home Assistant's Voice Assist pipeline:

```yaml
alias: "SIP: Auto-Answer with Voice Assist"
trigger:
  - platform: state
    entity_id: binary_sensor.phone_line_active
    to: "on"
action:
  - service: sip.answer
    target:
      entity_id: media_player.phone_line
  - service: sip.start_assist
    target:
      entity_id: media_player.phone_line
```

---

## Voicemail Automation Example

The following automation implements a full voicemail system: when a call is not answered within 15 seconds, it answers, plays a TTS greeting, sounds a beep, records the message to a local file, and sends a mobile notification with the audio clip link:

```yaml
alias: "SIP: Voicemail System"
trigger:
  - platform: state
    entity_id: binary_sensor.phone_line_active
    to: "on"
action:
  # Wait for 15 seconds (ring timeout)
  - delay: "00:00:15"
  # If still ringing, answer and record voicemail
  - choose:
      - conditions:
          - condition: state
            entity_id: binary_sensor.phone_line_active
            state: "on"
          - condition: state
            entity_id: media_player.phone_line
            state: "on" # Ringing or not connected yet
        sequence:
          - service: sip.answer
            target:
              entity_id: media_player.phone_line
          - delay: "00:00:01"
          # Speak a greeting
          - service: media_player.play_media
            target:
              entity_id: media_player.phone_line
            data:
              media_content_type: "music"
              # Speak TTS using standard HA TTS
              media_content_id: "media-source://tts/tts.google_translate?message=Please+leave+a+message+after+the+beep."
          # Wait for the TTS greeting to finish transmitting (no fixed delay needed)
          - wait_for_trigger:
              - platform: event
                event_type: sip_playback_done
            timeout: "00:00:15"
          # Sound a beep tone (local audio file or url)
          - service: media_player.play_media
            target:
              entity_id: media_player.phone_line
            data:
              media_content_type: "music"
              media_content_id: "http://local-ip:8123/local/beep.mp3"
          - delay: "00:00:01"
          # Start recording to a local WAV file
          - service: sip.start_recording
            target:
              entity_id: media_player.phone_line
            data:
              recording_file: "/media/voicemails/last_msg.wav"
          # Record for up to 30 seconds or until they hang up
          - wait_for_trigger:
              - platform: state
                entity_id: binary_sensor.phone_line_active
                to: "off"
            timeout: "00:00:30"
          # Stop recording & hang up
          - service: sip.stop_recording
            target:
              entity_id: media_player.phone_line
          - service: sip.hangup
            target:
              entity_id: media_player.phone_line
          # Push notification to user's phone via Companion App
          - service: notify.notify
            data:
              title: "New Voicemail Received"
              message: "You have a new message from {{ state_attr('sensor.phone_line_last_call', 'last_caller') }}"
              data:
                url: "/media/voicemails/last_msg.wav"
```

---

## Troubleshooting

- **Registration fails**: Double-check the SIP extension credentials and host IP address. Ensure your firewall or FreePBX settings permit UDP traffic on port `5060` from the Home Assistant host.
- **No Audio / One-way Audio**: This is typically caused by NAT or routing issues. Ensure the RTP port range (defaults starting at `7078`) is open and routed properly.
- **FFmpeg errors**: Ensure that the `ffmpeg` system binary is installed and accessible in your Home Assistant path, as it is utilized for audio transcoding.
