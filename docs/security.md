# Security

[README](../README.md) · [Setup](setup.md) · [Services](services.md) · [Examples](examples.md) · [Security](security.md) · [Troubleshooting](troubleshooting.md)

**Internet exposure is not supported.** Signalling is SIP/2.0/UDP with no TLS and no SRTP. Run hass-sip on the same LAN (or VPN) as the PBX. Do not port-forward UDP 5060 or `local_rtp_port` (default 7078) to the public internet. A PBX that requires TLS or SRTP will not interoperate with this client.

## Door locks and other security intents

Caller ID can be spoofed. An allow-list alone is not a security boundary.

For Assist that can unlock a door, use **allow-list + DTMF PIN + a dedicated Assist pipeline**, and grant the `SIP Assist ({extension})` user access only to those entities. The service fields, spoofing warning, and example YAML are under [`sip.start_assist`](services.md#sipstart_assist). IVR `assist: true` does not use that gate — give that menu its own PIN if needed.
