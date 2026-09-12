# Setup

[README](../README.md) · [Setup](setup.md) · [Services](services.md) · [Examples](examples.md) · [Security](security.md) · [Troubleshooting](troubleshooting.md)

## Quick start (FreePBX)

The goal is a registered extension and a two-way call. This path assumes Home Assistant and FreePBX are on the same LAN.

### 1. Create a pjsip extension

In FreePBX: **Applications → Extensions → Add Extension → Add New SIP (chan_pjsip) Extension**.

| Field | Example |
|-------|---------|
| User Extension | `1001` |
| Display Name | `Home Assistant` |
| Secret | a long random password |

Then on the extension:

1. **Advanced → DTMF Mode**: `RFC 4733` (RFC 2833). hass-sip also accepts SIP INFO if an ATA sends that instead.
2. **Advanced → Direct Media**: `No`. FreePBX defaults this to `Yes`, which sends a re-INVITE to hairpin RTP. The client answers in-dialog re-INVITE, but pinning media through the PBX is the predictable path — especially with NAT.
3. **Advanced → Media Encryption**: `No` (SRTP is not implemented).
4. **Codecs**: enable `g722`, `ulaw`, and `alaw` (order does not have to match; hass-sip offers G.722, then PCMU, then PCMA).
5. **Submit** and **Apply Config**.

Allow **UDP 5060** from the Home Assistant host to the PBX, and **UDP `local_rtp_port`** (default `7078`) for RTP. The client binds that one port only; **RTCP is not used**, so do not open or debug the next odd/even port.

### 2. Add the integration

**Settings → Devices & Services → Add Integration → SIP Client**.

- **Server / Host**: FreePBX IP or hostname  
- **Port**: `5060`  
- **Username**: `1001`  
- **Password**: the extension secret  

Leave Domain empty unless your realm is not the server hostname.

### 3. Confirm registration

Wait a few seconds. The **Registration status** sensor should read `registered`, and Logbook should show `sip_registered`. If it stays `unregistered`, check username/password, UDP 5060, and that the extension is `pjsip` (not chan_sip) on a matching transport.

### 4. Place a test call

From another phone on the PBX, dial `1001`. Answer with `sip.answer` (or enable auto-answer for that caller in `sip_contacts.json`). You should get two-way audio; the **Call Audio** sensor (and the media player's `audio_path` attribute) should become `bidirectional`.

To dial out from Home Assistant:

```yaml
action: sip.dial
target:
  entity_id: media_player.phone_line
data:
  number: "1002"
```

---

## Configuration

Device setup is done entirely through the Home Assistant UI. The [Quick start](#quick-start-freepbx) is the shortest path; this list is the field reference.

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
   - **Outbound proxy** *(Optional)*: Use when the registrar answers `407 Proxy Authentication Required` (see [generic SIP / ITSP](#pbx-compatibility)).
   - **Authentication username** *(Optional)*: Digest username when it differs from the extension.

---

## PBX compatibility

A PBX is not labelled "supported" without a recorded lab version. The table is the current status from recommended settings and community reports.

| PBX | Status | Recorded version | Notes |
|-----|--------|------------------|-------|
| **FreePBX** (Asterisk **pjsip**) | Recommended settings published | — | Follow the Quick Start. No FreePBX/Asterisk version is certified here. |
| **Asterisk** (pjsip, no FreePBX) | Same endpoint settings | — | Use the `pjsip` snippet below. |
| **3CX** (including SBC) | Community reported | — | Inbound via SBC needed Record-Route on 200 OK ([#39](https://github.com/eigger/hass-sip/issues/39)). Current builds preserve Record-Route; no 3CX version is listed here. |
| **Generic SIP / ITSP / UniFi Talk** | Unverified | — | Outbound `407 Proxy Authentication Required` ([#17](https://github.com/eigger/hass-sip/issues/17)): set **Outbound proxy** to the proxy host and **Authentication username** if it differs from the extension. |

SIP **TLS** and **SRTP** are out of scope (UDP signalling only). **Internet exposure is unsupported** — see [Security](security.md).

### Recommended pjsip endpoint values

Use these in FreePBX or in `pjsip.conf`.

| Setting | Value | Why |
|---------|-------|-----|
| Transport | UDP `5060` | Only SIP/2.0/UDP is implemented |
| Codecs | `g722`, `ulaw` (`PCMU`), `alaw` (`PCMA`) | Offer order is G.722 → PCMU → PCMA |
| DTMF | RFC 4733 (`telephone-event`) | RTP events plus SIP INFO (`application/dtmf-relay`) |
| `direct_media` | `no` | Avoids a media hairpin re-INVITE; set `yes` only if you need it and audio stays up |
| `rtp_symmetric` / `force_rport` / `rewrite_contact` | `yes` | NAT; the client also latches the RTP source address |
| Session timers (RFC 4028) | off / not required | No `Supported: timer` / `Session-Expires` |
| Media encryption | none | No SRTP |
| `qualify` | `yes` | OPTIONS keep-alive is fine |

Standalone Asterisk example:

```ini
[1001]
type=endpoint
context=from-internal
disallow=all
allow=g722,ulaw,alaw
direct_media=no
rtp_symmetric=yes
force_rport=yes
rewrite_contact=yes
dtmf_mode=rfc4733
media_encryption=no
timers=no
auth=1001
aors=1001
transport=transport-udp

[1001]
type=auth
auth_type=userpass
username=1001
password=change-me

[1001]
type=aor
max_contacts=1
remove_existing=yes
```
