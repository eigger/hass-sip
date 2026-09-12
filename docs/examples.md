# Examples

[README](../README.md) · [Setup](setup.md) · [Services](services.md) · [Examples](examples.md) · [Security](security.md) · [Troubleshooting](troubleshooting.md)

- [TTS announce](#example-announce-a-tts-message-then-hang-up)
- [HA calls you with TTS](#home-assistant-calls-you-with-tts)
- [DTMF from any phone](#control-home-assistant-with-dtmf-any-phone)
- [IVR](#ivr-configuration-example)
- [Contacts](#contacts--caller-id-mapping)
- [Intercom auto-answer](#intercom--auto-answer-mode)
- [Voice Assist](#voice-assist-automation-example)
- [Door release (DTMF)](#intercom-door-release-button-example-dtmf)
- [Recent calls card](#lovelace-dashboard-recent-calls-list-card)
- [Voicemail](#voicemail-automation-example)

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
      # Optional: engine-specific options, e.g. a voice
      # tts_options:
      #   voice: ko-KR-SunHiNeural
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

---

### Option C: Play a media-source TTS URL

If you generate the TTS as a `media-source://tts/...` URL (for example by picking it from the media browser), play it straight to the phone line with `media_player.play_media`. This is handy when you want full control over the TTS entity/voice without the `sip.answer` shortcut.

```yaml
alias: "SIP: Announce via media-source TTS"
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
    continue_on_timeout: false
  - service: media_player.play_media
    target:
      entity_id: media_player.phone_line
    data:
      media_content_id: >-
        media-source://tts/tts.edge_tts_service_edge_tts?message=안녕하세요&language=ko-KR
      media_content_type: music
  # Wait until the whole message has been sent (prevents truncation)
  - wait_for_trigger:
      - platform: event
        event_type: sip_playback_done
    timeout: "00:00:30"
  - service: sip.hangup
    target:
      entity_id: media_player.phone_line
```

> The `media_content_id` query string is URL-encoded automatically, so you can write a plain (or templated) `message=...`. A plain audio file URL works in place of the `media-source://` id too.

> **Entity IDs:** examples use `media_player.phone_line` as a placeholder. Your actual ids are prefixed with the account, e.g. `media_player.sip_client_100_phone_line` — copy the real one from **Developer Tools → States** (or target the SIP device instead).

---

## Home Assistant calls you with TTS

hass-sip can **originate** a call because it is a registered PBX extension. Use this when Home Assistant should ring a phone and speak — not only when someone calls in.

Trigger from any automation (sensor, calendar, alarm, script):

```yaml
alias: "SIP: Call me when the garage stays open"
trigger:
  - platform: state
    entity_id: binary_sensor.garage_door
    to: "on"
    for: "00:10:00"
action:
  - service: sip.dial
    target:
      entity_id: media_player.phone_line
    data:
      number: "100"
      ring_timeout: 30
      message: "The garage door has been open for ten minutes."
      tts_engine: tts.google_translate
      language: en
```

Without `message`, the far end still answers into a live line — you can then `tts.speak`, play media, start Assist, or run an IVR `menu` on the same call. Full TTS variants (inbound answer, multi-step, media-source URLs) are in [Announce a TTS message](#example-announce-a-tts-message-then-hang-up).

---

## Control Home Assistant with DTMF (any phone)

Keypad control is not limited to door stations. Any extension or softphone on the PBX can dial hass-sip; answer with an IVR `menu` so digits run Home Assistant services.

```yaml
alias: "SIP: Phone keypad controls lights"
trigger:
  - platform: event
    event_type: sip_incoming_call
action:
  - service: sip.answer
    target:
      entity_id: media_player.phone_line
    data:
      menu:
        id: root
        message: "Press 1 to toggle the living room light. Press 2 for Voice Assist."
        tts_engine: tts.google_translate
        language: en
        timeout: 10
        input: digit
        choices:
          "1":
            action:
              domain: light
              service: toggle
              entity_id: light.living_room_light
            message: "Toggling the light now."
            post_action: hangup
          "2":
            assist: true
        on_invalid:
          message: "Invalid selection."
          post_action: repeat
        on_timeout: hangup
```

That is the same IVR engine used for nested menus and PINs — full field list: [IVR Configuration Example](#ivr-configuration-example).

To **send** DTMF into an active call (for example to open a gate from the dashboard), see [Intercom Door Release](#intercom-door-release-button-example-dtmf).

---

## IVR Configuration Example

You can pass a menu tree to the `menu` field of `sip.dial` or `sip.answer`. TTS settings use the **same flat field names** as the service parameters (`message`, `tts_engine`, `language`, `tts_options`).

```yaml
service: sip.answer
target:
  entity_id: media_player.phone_line
data:
  menu:
    id: root
    message: "Welcome. Press 1 to toggle the living room light. Press 2 to talk to the Voice Assistant. Or enter your four-digit PIN followed by hash."
    tts_engine: tts.google_translate
    language: en
    wait_for_audio: true
    timeout: 10
    input: digit          # "digit" (single key) or "pin" (multi-key, ends with #)
    choices:
      "1":
        action:
          domain: light
          service: toggle
          entity_id: light.living_room_light
        message: "Toggling the light now."
        post_action: hangup
      "2":
        assist: true        # hand the call to Home Assistant Voice Assist
    on_invalid:
      message: "Invalid selection."
      post_action: repeat   # re-play the current menu
    on_timeout: hangup
```

### Menu fields
| Field | Description |
|-------|-------------|
| `id` | Optional menu id; target for `goto`. |
| `message` | TTS text to speak. |
| `audio_file` | Audio file path/URL to play instead of `message`. |
| `template` | `true` to render `message` as a Jinja template before speaking. |
| `tts_engine` / `language` / `tts_options` | TTS voice settings (same as the service params). |
| `wait_for_audio` | `true` (default): collect input only after playback finishes. |
| `timeout` | Seconds to wait for input (default `10`). |
| `input` | `digit` (default, single key) or `pin` (multi-key, ends with `#`). |
| `choices` | Map of input key → target (nested menu, or a bare `post_action` string). |
| `on_invalid` | Target when the input matches no choice. |
| `on_timeout` | Target when no input arrives in time. |
| `action` | A Home Assistant service to call on entry (`domain`, `service`, `entity_id`, `data`). |
| `assist` | `true` to hand the call to Voice Assist. |
| `post_action` | Terminal action: `hangup` · `repeat` · `back [n]` · `goto <id>` · `wait`. |

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

The integration can automatically answer specific incoming calls (useful for intercoms and doorbells). It triggers in two ways:
1. **SIP Headers**: The incoming call includes standard auto-answer headers like `Call-Info: ...; answer-after=0` or `Alert-Info: Ring Answer`.
2. **Contacts Configuration**: The incoming caller ID matches an extension marked with `"auto_answer": true` in `sip_contacts.json`.

When triggered, the integration answers immediately, opens the audio channel, and bypasses the ringing phase. Auto-answer only opens the channel — pair it with `sip.start_assist`, a TTS `message`, or `sip.start_recording` to actually send or capture audio.

> To answer arbitrary calls under your own conditions, trigger an automation on the `sip_incoming_call` event and call `sip.answer` (optionally with a `message` or `menu`) instead.

---

## Voice Assist Automation Example

You can automatically bridge incoming calls directly to Home Assistant's Voice Assist pipeline. The caller can issue **multiple commands in one call** — for example, "turn on the kitchen light" followed by "turn it off" — without hanging up between them.

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
    data:
      max_silent_turns: 2
```

On a noisy or narrowband (G.711) line, the assistant may react to line noise or a breath before the caller speaks. Raise `silence_seconds` and enable `noise_suppression` to compensate:

```yaml
  - service: sip.start_assist
    target:
      entity_id: media_player.phone_line
    data:
      max_silent_turns: 2
      silence_seconds: 1.2
      noise_suppression: 2
      turn_tone: true
```

Each SIP account gets a Home Assistant system user named `SIP Assist ({extension})` in the Users group. Assist intents run as that user and reuse one `Context` for the whole session, so Logbook can attribute those actions to that call. Grant this user access to the entities the phone should control. Removing the SIP entry deletes the user.

---

## Intercom Door Release Button Example (DTMF)

If you have a door entry intercom connected to the SIP line (e.g., at the front gate), you can create a Lovelace dashboard button to trigger the gate/door release mechanism. This works by sending a specific DTMF digit (like `1` or `*`) to the active call.

### 1. Basic Dashboard Button Card (YAML)
Add this button configuration to your Home Assistant dashboard:

```yaml
type: button
name: Open Front Gate
icon: mdi:gate
tap_action:
  action: call-service
  service: sip.send_dtmf
  target:
    entity_id: media_player.phone_line
  data:
    digits: "1" # Digit sequence your gate intercom expects (e.g. 1, *9, etc.)
```

### 2. Conditional Card (Recommended)
To hide the button entirely when there is no call active (preventing accidental triggers), wrap it inside a Conditional Card using the `binary_sensor.phone_line_active` entity:

```yaml
type: conditional
conditions:
  - condition: state
    entity: binary_sensor.phone_line_active
    state: "on"
card:
  type: button
  name: Open Front Gate
  icon: mdi:door-open
  tap_action:
    action: call-service
    service: sip.send_dtmf
    target:
      entity_id: media_player.phone_line
    data:
      digits: "1"
```

---

## Lovelace Dashboard: Recent Calls List Card

You can display a beautiful, dynamically updated call log of the last 20 calls directly on your Home Assistant Lovelace dashboard. This leverages the `call_history` state attribute of the Last Call sensor (`sensor.phone_line_last_call`).

Add a **Markdown Card** to your dashboard with the following YAML template configuration:

```yaml
type: markdown
title: "📞 Recent Calls"
content: >
  <table style="width: 100%; border-collapse: collapse;">
    <thead>
      <tr style="border-bottom: 2px solid var(--divider-color); text-align: left;">
        <th style="padding: 8px;">Time</th>
        <th style="padding: 8px;">Caller</th>
        <th style="padding: 8px;">Direction</th>
        <th style="padding: 8px; text-align: right;">Duration</th>
      </tr>
    </thead>
    <tbody>
      {% set history = state_attr('sensor.phone_line_last_call', 'call_history') %}
      {% if history %}
        {% for call in history %}
          <tr style="border-bottom: 1px solid var(--divider-color);">
            <td style="padding: 8px; font-size: 0.9em; color: var(--secondary-text-color);">
              {{ as_timestamp(call.timestamp) | timestamp_custom('%m/%d %H:%M') }}
            </td>
            <td style="padding: 8px;">
              <b>{{ call.name }}</b> <span style="font-size: 0.8em; color: var(--secondary-text-color);">({{ call.number }})</span>
            </td>
            <td style="padding: 8px; font-size: 0.9em;">
              {% if call.direction == 'incoming' %}
                {% if call.status == 'answered' %}
                  <span style="color: var(--success-color);">🟢 ↙️ Inbound</span>
                {% elif call.status == 'rejected' %}
                  <span style="color: var(--error-color);">🔴 🚫 Rejected</span>
                {% else %}
                  <span style="color: var(--warning-color);">🟠 ↙️ Missed</span>
                {% endif %}
              {% else %}
                {% if call.status == 'answered' %}
                  <span style="color: var(--info-color);">🔵 ↗️ Outbound</span>
                {% else %}
                  <span style="color: var(--secondary-text-color);">⚪ ↗️ Unanswered</span>
                {% endif %}
              {% endif %}
            </td>
            <td style="padding: 8px; text-align: right; font-size: 0.9em;">
              {% if call.duration > 0 %}
                {{ call.duration }}s
              {% else %}
                -
              {% endif %}
            </td>
          </tr>
        {% endfor %}
      {% else %}
        <tr>
          <td colspan="4" style="padding: 16px; text-align: center; color: var(--secondary-text-color);">
            No recent calls logged.
          </td>
        </tr>
      {% endif %}
    </tbody>
  </table>
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
