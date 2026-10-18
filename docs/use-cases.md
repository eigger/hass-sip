# Use cases

[README](../README.md) · [Setup](setup.md) · [Services](services.md) · [Examples](examples.md) · [Security](security.md) · [Troubleshooting](troubleshooting.md)

Core scenarios with demo storyboards, moved here from the README. Full automation recipes are in [Examples](examples.md).

## Control Home Assistant by voice

Dial the hass-sip extension → answer → Assist. Several spoken commands in one call, no redial.

```yaml
action: sip.start_assist
target:
  entity_id: media_player.phone_line
```

![Phone dials Home Assistant, Assist turns on a light, then sets brightness — storyboard](https://raw.githubusercontent.com/eigger/hass-sip/main/docs/demo/phone-assist.gif)

Full flow: [Voice Assist example](examples.md#voice-assist-automation-example). Door locks: [allow-list + PIN](security.md#door-locks-and-other-security-intents).

## Control Home Assistant with the phone keypad

Any phone on the PBX can call the extension and press digits. An IVR menu runs Home Assistant services — this is not limited to intercoms.

```yaml
action: sip.answer
target:
  entity_id: media_player.phone_line
data:
  menu:
    message: "Press 1 to toggle the living room light."
    choices:
      "1":
        action:
          domain: light
          service: toggle
          entity_id: light.living_room_light
        message: "Toggling the light now."
        post_action: hangup
```

![Phone dials Home Assistant, presses 1 on the keypad, living room light turns on — storyboard](https://raw.githubusercontent.com/eigger/hass-sip/main/docs/demo/phone-dtmf.gif)

Menu fields and nesting: [IVR example](examples.md#ivr-configuration-example). Step-by-step: [DTMF from any phone](examples.md#control-home-assistant-with-dtmf-any-phone).

## Intercom auto-answer

Door station rings → hass-sip answers immediately → optional DTMF to open the gate.

```json
{
  "102": { "name": "Front Doorbell", "auto_answer": true }
}
```

![Door station rings Home Assistant, auto-answer opens audio, dashboard DTMF opens the gate — storyboard](https://raw.githubusercontent.com/eigger/hass-sip/main/docs/demo/intercom-autoanswer.gif)

Put that in `sip_contacts.json` (or send SIP auto-answer headers). [Intercom details](examples.md#intercom--auto-answer-mode).

> **DIY Hardware**: Looking to build an ESP32-based intercom or doorbell? Check out the [ESPHome External Component](../README.md#esphome-external-component).

## Home Assistant calls you with TTS

An automation dials your phone, speaks a message when you answer, then hangs up — package delivery, garage open too long, alarm, and so on.

```yaml
action: sip.dial
target:
  entity_id: media_player.phone_line
data:
  number: "100"
  message: "The garage door has been open for ten minutes."
```

![Garage sensor triggers Home Assistant, which dials your phone and speaks a TTS warning — storyboard](https://raw.githubusercontent.com/eigger/hass-sip/main/docs/demo/sensor-tts-call.gif)

More options: [Announce a TTS message](examples.md#example-announce-a-tts-message-then-hang-up). Outbound-focused notes: [HA calls you](examples.md#home-assistant-calls-you-with-tts).
