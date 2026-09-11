# Troubleshooting

[README](../README.md) · [Setup](setup.md) · [Services](services.md) · [Examples](examples.md) · [Security](security.md) · [Troubleshooting](troubleshooting.md)

- **Registration fails**: Double-check the SIP extension credentials and host IP address. Ensure your firewall or FreePBX settings permit UDP traffic on port `5060` from the Home Assistant host. Follow the [Quick start](setup.md#quick-start-freepbx) and [recommended pjsip values](setup.md#recommended-pjsip-endpoint-values). A `423` from the registrar is handled by raising the register expiration; a `407` on outbound calls usually means you need an outbound proxy.
- **No Audio / One-way Audio**: This is typically caused by NAT or routing issues. Set `direct_media=no`, open UDP `local_rtp_port` (default `7078`; RTCP is not used), and confirm the **Call Audio** sensor (`audio_path`; `no_rx` means we sent but heard nothing). The phone-line media player also exposes `call_duration`, byte counts, and `audio_path`. Download diagnostics from **Settings → Devices & Services → SIP Client → ⋮ → Download diagnostics** for SDP vs latched addresses. Passwords are redacted; phone numbers are masked.
- **FFmpeg errors**: Ensure that the `ffmpeg` system binary is installed and accessible in your Home Assistant path, as it is utilized for audio transcoding.
- **SIP / RTP protocol trace**: Off by default. To capture signalling (INVITE / 200 OK / REGISTER) and a 5-second RTP summary without putting digest credentials in the log, add this to `configuration.yaml` and restart:

  ```yaml
  logger:
    logs:
      custom_components.sip.sip_client.trace: debug
  ```

  `Authorization` / `Proxy-Authorization` values (`response`, `nonce`, `cnonce`) are masked as `****`. RTP is summarised (packet counts, payload type, estimated loss, latched address), not logged packet-by-packet. That YAML logger is the narrow switch; the integration's **Enable debug logging** button also turns this on, because it sets `custom_components.sip` (and therefore this child logger) to DEBUG — credentials stay masked, but phone numbers, SDP, and SIP INFO DTMF digits (including an Assist PIN) will be in the log you download for an issue. Turn the logger back to `info` when you are done.
