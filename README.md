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
- **Custom Telephony Services**: Complete set of services to control SIP calls (`sip.dial`, `sip.hangup`, `sip.answer`, `sip.send_dtmf`, `sip.start_recording`, `sip.stop_recording`, `sip.start_assist`). Voice Assist sessions support **multi-turn conversation** within a single call.
- **Interactive Voice Response (IVR) Engine**: Construct nested DTMF automated phone trees with TTS prompt templates, custom PIN authentication, and native Home Assistant service triggers.
- **Wideband Audio (G.722)**: Negotiates G.722 (16 kHz) when the remote party supports it, falling back to G.711 µ-law / A-law. Voice Assist receives true 16 kHz PCM on G.722 calls instead of upsampled narrowband.
- **Continuous Voice Assist**: During an active call, `sip.start_assist` keeps listening for follow-up commands until silence, a turn limit, or hangup — no need to redial between commands.
- **Sensors**: Exposes registration status, last caller ID, negotiated codec, last-call audio path (`none` / `no_rx` / `no_tx` / `bidirectional`), hangup reason, and a bidirectional-audio binary sensor.

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

> **Inbound DTMF:** digits pressed by the remote party are accepted both as RFC 2833 / RFC 4733 telephone-event packets and as SIP INFO (`application/dtmf-relay`), which is what many ATA/VoIP adapters send. **In-band DTMF (audio tones) is not detected** — set your ATA or PBX to RFC 2833 or SIP INFO if key presses are not recognised.

### `sip.start_recording`
Starts recording call audio to a local WAV file. Can run at the same time as `sip.start_assist` — received audio is copied to both the WAV file and the Assist pipeline, and stopping one does not mute the other.
- `entity_id` *(Required)*: The target SIP media player entity.
- `recording_file` *(Required)*: Path of the WAV file to save. Relative paths are resolved against the Home Assistant config directory (e.g. `www/sip/last_msg.wav`). Absolute paths must stay inside an allowed directory:
  - the config directory
  - `allowlist_external_dirs` and `media_dirs`
  - Home Assistant OS `/media` and `/share` (when those mounts exist)

Parent directories are created if missing. Hangup closes the file and fires `sip_recording_stopped`; you do not have to call `sip.stop_recording` first. A new recording never appends to a previous call's WAV.

### `sip.stop_recording`
Stops active call recording and finalizes the WAV so it is immediately playable.
- `entity_id` *(Required)*: The target SIP media player entity.

### `sip.start_assist`
Bridges the active call to Home Assistant's Voice Assist pipeline for **multi-turn conversation**. After each command and TTS response, the integration listens for the next command without hanging up. Conversation context is preserved across turns (e.g. "turn on the kitchen light" → "set it to 50%").

Use `initial_prompt` when the agent should open the conversation with a spoken response, and `system_prompt` for silent instructions that should guide every agent turn:

```yaml
action: sip.start_assist
target:
  entity_id: media_player.sip
data:
  conversation_id: "existing-conversation"
  system_prompt: >-
    You are answering a support phone line. Keep responses concise and
    never expose internal implementation details.
  initial_prompt: >-
    Greet the caller, introduce yourself, and ask how you can help.
```

Supplying only `system_prompt` does not create an additional opening pipeline turn or play audio. The bridge begins by listening to the caller as usual, then passes the instructions to the conversation agent with each turn.

Use `conversation_id` to continue an existing Home Assistant conversation, such as one started by `conversation.process`. The Assist pipeline must use the same conversation agent. Conversation sessions are short-lived and may be replaced after Home Assistant cleans up their context.

The session ends when:
- The caller says nothing for `max_silent_turns` consecutive turns (default: 2, ~30 s of silence)
- `max_turns` is reached (default: 0 = unlimited)
- The call is hung up, or `close()` is triggered

- `entity_id` *(Required)*: The target SIP media player entity.
- `pipeline_id` *(Optional)*: Assist pipeline ID. Uses the Home Assistant default when omitted.
- `conversation_id` *(Optional)*: Existing Home Assistant conversation ID to continue. Use the same conversation agent that created it; expired sessions may start a new conversation.
- `initial_prompt` *(Optional)*: Text submitted as an opening conversation turn. The agent's response is played before the bridge starts listening and does not count toward `max_turns` or `max_silent_turns`.
- `system_prompt` *(Optional)*: Additional instructions passed silently to the conversation agent on every turn. It does not create a conversation turn or trigger TTS by itself.
- `max_turns` *(Optional)*: Maximum conversation turns before ending (default: `0` = unlimited).
- `max_silent_turns` *(Optional)*: Consecutive no-speech turns before ending (default: `2`).
- `barge_in` *(Optional)*: Allow interrupting TTS mid-response by speaking (default: `false`; requires `pymicro_vad` from the Assist pipeline integration; may self-trigger on speakerphones without echo cancellation).
- `silence_seconds` *(Optional)*: Seconds of silence that end a spoken command. Assist defaults to `0.7`, which is tuned for near-field microphones; phone lines usually need `1.0`–`1.5` so callers are not cut off mid-sentence.
- `noise_suppression` *(Optional)*: Noise suppression level applied to caller audio, `0` (off) to `4` (max). Helps on noisy narrowband G.711 lines where line noise is otherwise mistaken for speech.
- `turn_tone` *(Optional)*: Play a short beep when the microphone opens for the next turn (default: `false`). After TTS there is a brief guard before the next pipeline starts; speech in that window is prerolled when voice activity is detected. A completed beep drops the guard/tone capture so speakerphone echo of the beep does not reach STT — speak after the beep. Skipped on barge-in turns that already have preroll, and left off by default so existing calls and IVR `assist: true` prompts are unchanged.
- `hangup_on_end` *(Optional)*: Hang up the call when the Assist session ends (default: `false`).
- `allowed_callers` *(Optional)*: Caller IDs that may start Assist. Omitted means no restriction, so existing automations keep working. Matching uses the SIP user-part (`100`, `sip:100@pbx`, and `<sip:100@host>` all compare as `100`). An empty list allows nobody.
- `contacts_only` *(Optional)*: Only callers listed in `sip_contacts.json` may start Assist (default: `false`). Combined with `allowed_callers`, the caller must match **both**.
- `pin` *(Optional)*: DTMF PIN collected before Assist starts. The caller enters the digits and either presses `#` or matches the PIN length (15 s timeout). Failed, hung-up, or timed-out attempts fire `sip_assist_rejected` and do **not** run intents. The PIN is never logged or included in events.

> **Caller ID can be spoofed.** An allow-list alone is not a security boundary. For door-lock or other security intents, use **allow-list + PIN + a dedicated Assist pipeline** that only exposes those intents. IVR `assist: true` does not use this gate — give that menu its own PIN if needed.

```yaml
  - service: sip.start_assist
    target:
      entity_id: media_player.phone_line
    data:
      allowed_callers:
        - "100"
        - "101"
      contacts_only: true
      pin: "1234"
      pipeline_id: "door-lock-pipeline"
```

> **Tuning for phone calls**: the Assist defaults assume a smart speaker's microphone. On a telephone line, line noise and codec artefacts can trip the voice detector before the caller speaks — the symptom is a reply to a cough or a breath, followed by the real question being split across two turns. Raising `silence_seconds` and enabling `noise_suppression` addresses the first part. Enabling `turn_tone` adds an audible cue when it is the caller's turn to speak. Speech that starts right after TTS is forwarded into the next turn as preroll when it looks like voice (not echo of the reply or the beep). Enabling `barge_in` (handsets only) additionally lets a caller talk over a response instead of waiting for it to finish.

---

## Control Entities (Switches & Buttons)

The integration exposes native switch and button entities for easy dashboard control and automation triggers.

### 1. Switches
- **Do Not Disturb Switch** (`switch.phone_line_dnd`): Turn this ON to automatically reject all incoming calls with a `486 Busy Here` SIP response.

### 2. Buttons
- **Answer Button** (`button.phone_line_answer`): Press this button to answer an active incoming call.
- **Hang Up Button** (`button.phone_line_hangup`): Press this button to end the current call or decline an incoming call.

Each button exposes a `can_press` attribute that reflects whether the action applies in the current call state (`answer` → only while a call is ringing in; `hangup` → whenever any call is active). Use it to hide the buttons when they are not actionable:

```yaml
type: conditional
conditions:
  - condition: state
    entity: button.phone_line_answer
    attribute: can_press
    state: true
card:
  type: button
  entity: button.phone_line_answer
  name: Answer
```

---

## Events & Event Entity

The integration fires raw events on the Home Assistant event bus and exposes a native **Event Entity** (`event.phone_line_call_events`) for easier UI-based automations.

### 1. Call Events Entity (Recommended for Automations)
Each SIP extension device includes a **Call Events** entity (e.g. `event.phone_line_call_events`).
You can use this entity as a trigger in the Home Assistant Automation Editor.

Supported event types (`event_type` attribute):
- `incoming`: Fired when an inbound call arrives. Attributes: `caller`, `caller_name`.
- `connected`: Fired when the call is answered.
- `playback_done`: Fired when TTS or audio playback finishes.
- `ended`: Fired when the call ends.
- `dtmf`: Fired when a DTMF key is pressed. Attributes: `digit`.
- `recording_started` / `recording_stopped`: Fired when call recording starts or stops.
- `registered`: Fired when the SIP client registers successfully.
- `assist_rejected`: Fired when `sip.start_assist` refuses the caller. Attributes: `caller`, `reason` (`not_allowed`, `pin_mismatch`, `pin_timeout`).

#### Example Event Trigger:
```yaml
trigger:
  - platform: state
    entity_id: event.phone_line_call_events
    attribute: event_type
    to: incoming
```

### 2. Raw Event Bus Events
If you prefer triggering directly from the Event Bus, the integration fires the following events:

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
| `sip_assist_rejected` | `caller`, `reason` | Assist was refused (`not_allowed`, `pin_mismatch`, or `pin_timeout`) |

> `sip_call_connected` and `sip_playback_done` are the two events/states you want for "answer → speak → hang up" flows: wait for the call to connect before playing media, and wait for playback to finish before hanging up so the message is never cut off.

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

---

## Troubleshooting

- **Registration fails**: Double-check the SIP extension credentials and host IP address. Ensure your firewall or FreePBX settings permit UDP traffic on port `5060` from the Home Assistant host.
- **No Audio / One-way Audio**: This is typically caused by NAT or routing issues. Ensure the RTP port range (defaults starting at `7078`) is open and routed properly. After a call, check **Call Audio** (`no_rx` means we sent but heard nothing) and **Bidirectional Audio**; the phone-line media player also shows `call_duration`, byte counts, and `audio_path`. Download diagnostics from **Settings → Devices & Services → SIP Client → ⋮ → Download diagnostics** for SDP vs latched addresses. Passwords are redacted; phone numbers are masked.
- **FFmpeg errors**: Ensure that the `ffmpeg` system binary is installed and accessible in your Home Assistant path, as it is utilized for audio transcoding.
- **SIP / RTP protocol trace**: Off by default. To capture signalling (INVITE / 200 OK / REGISTER) and a 5-second RTP summary without putting digest credentials in the log, add this to `configuration.yaml` and restart:

  ```yaml
  logger:
    logs:
      custom_components.sip.sip_client.trace: debug
  ```

  `Authorization` / `Proxy-Authorization` values (`response`, `nonce`, `cnonce`) are masked as `****`. RTP is summarised (packet counts, payload type, estimated loss, latched address), not logged packet-by-packet. That YAML logger is the narrow switch; the integration's **Enable debug logging** button also turns this on, because it sets `custom_components.sip` (and therefore this child logger) to DEBUG — credentials stay masked, but phone numbers and SDP will be in the log you download for an issue. Turn the logger back to `info` when you are done.
