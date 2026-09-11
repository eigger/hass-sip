# Services, entities, and events

[README](../README.md) · [Setup](setup.md) · [Services](services.md) · [Examples](examples.md) · [Security](security.md) · [Troubleshooting](troubleshooting.md)

## Services

This integration registers the following services under the `sip` domain:

### `sip.dial`
Initiates an outbound SIP call.
- `entity_id` *(Required)*: The target SIP media player entity (e.g. `media_player.phone_line`).
- `number` *(Required)*: The destination number or SIP URI to call (e.g., `100` or `sip:100@freepbx`).
- `ring_timeout` *(Optional)*: Number of seconds to let the call ring before canceling (e.g., `30`).
- `menu` *(Optional)*: IVR menu configuration object (see [IVR menus](examples.md#ivr-configuration-example)).
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
- `menu` *(Optional)*: IVR menu configuration object to start immediately on answer (see [IVR menus](examples.md#ivr-configuration-example)).
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
- `pin` *(Optional)*: DTMF PIN collected before Assist starts. The caller enters the digits and either presses `#` or matches the PIN length (15 s timeout). Failed, hung-up, or timed-out attempts fire `sip_assist_rejected` and do **not** run intents. While the PIN is collected, digits are not logged and `sip_dtmf_digit` is not fired, so the PIN cannot be reconstructed from Logbook or automations.

> **Caller ID can be spoofed.** An allow-list alone is not a security boundary. For door-lock or other security intents, use **allow-list + PIN + a dedicated Assist pipeline** that only exposes those intents, and grant the `SIP Assist ({extension})` user access only to those entities. IVR `assist: true` does not use this gate — give that menu its own PIN if needed. SIP INFO DTMF digits can still appear in DEBUG logs and opt-in SIP traces (`Signal=` in INFO bodies); do not attach those logs to an issue if a PIN was entered.

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
