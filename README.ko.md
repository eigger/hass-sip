# SIP Client Home Assistant 통합 (hass-sip)

[English](README.md) | **한국어**

<!-- hass-standards:badges:start -->
[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5?style=flat-square)](#설치)
[![Release](https://img.shields.io/github/v/release/eigger/hass-sip?style=flat-square)](https://github.com/eigger/hass-sip/releases)
[![Home Assistant minimum](https://img.shields.io/badge/HA-%E2%89%A5%202026.10.0-41BDF5?style=flat-square)](hacs.json)
[![Tests](https://img.shields.io/github/actions/workflow/status/eigger/hass-sip/tests.yml?branch=main&style=flat-square&label=Tests)](https://github.com/eigger/hass-sip/actions/workflows/tests.yml)
[![Validate](https://img.shields.io/github/actions/workflow/status/eigger/hass-sip/validate.yml?branch=main&style=flat-square&label=Validate)](https://github.com/eigger/hass-sip/actions/workflows/validate.yml)
[![License](https://img.shields.io/github/license/eigger/hass-sip?style=flat-square)](LICENSE)
[![Analytics reporting installs](https://img.shields.io/badge/dynamic/json?style=flat-square&color=41BDF5&label=Usage&query=%24.sip.total&url=https%3A%2F%2Fanalytics.home-assistant.io%2Fcustom_integrations.json)](https://analytics.home-assistant.io/)
<!-- hass-standards:badges:end -->

hass-sip는 PBX에 **SIP 내선으로 등록**됩니다. 이 회선을 미디어 플레이어로 노출하고, DTMF 송수신, IVR 메뉴, 통화 녹음, Home Assistant 음성 Assist 연결을 지원합니다. HACS 커스텀 통합이며 Home Assistant Core에 포함된 통합이 아닙니다.

전송은 **UDP 기반 SIP**이고 **G.711(PCMU/PCMA)과 G.722**를 지원합니다. TLS, SRTP, Opus는 구현되어 있지 않습니다. **인터넷 노출은 지원하지 않으며** 검증되지 않은 PBX를 "지원"으로 표기하지 않습니다.

## 주요 기능

- `media_player` 회선: 활성 통화에 TTS 또는 오디오 URL 재생
- 서비스: `sip.dial`, `sip.hangup`, `sip.answer`, `sip.send_dtmf`, `sip.start_recording`, `sip.stop_recording`, `sip.start_assist`(다중 턴)
- DTMF, PIN, Home Assistant 서비스 동작을 쓰는 IVR 트리
- 상대가 제안하면 G.722(16 kHz), 아니면 G.711. 코덱 순서(또는 G.711 전용)는 통합의 **구성(Configure)** 에서 선택
- 센서: 등록 상태, 마지막 발신자, 코덱, 오디오 경로(`none` / `no_rx` / `no_tx` / `bidirectional`)

## 최소 요구사항

- Home Assistant 2026.10.0 이상 (`hacs.json` 기준)
- 같은 LAN(또는 VPN)의 SIP PBX (예: FreePBX chan_pjsip)

## 설치

1. **HACS**: 이 리포지토리(`eigger/hass-sip`)를 HACS 사용자 지정 리포지토리로 추가하거나, **수동**으로 `custom_components/sip` 폴더를 Home Assistant의 `custom_components`에 복사합니다.
2. Home Assistant를 재시작합니다.

## 빠른 설정

FreePBX와 같은 LAN에서:

1. **chan_pjsip** 내선을 만듭니다(`g722` / `ulaw` / `alaw`, DTMF RFC 4733, Direct Media **No**).
2. **설정 → 기기 및 서비스 → 통합구성요소 추가 → SIP Client** 에서 호스트, 포트 `5060`, 사용자 이름, 비밀번호를 입력합니다.
3. **Registration status** 가 `registered` 인지 확인합니다.
4. 내선으로 전화를 걸어 **Call Audio**(`audio_path`)가 `bidirectional` 이 되는지 확인합니다.

필드 목록, 방화벽 참고, pjsip 예시는 [설정(영어)](docs/setup.md)를 참고하세요.

## 대표 예제

전화로 Home Assistant 호출 → 응답 → Assist:

```yaml
action: sip.start_assist
target:
  entity_id: media_player.phone_line
```

IVR, 인터폰 자동 응답, TTS 발신 등 더 많은 예제는 [예제(영어)](docs/examples.md)에 있습니다.

### 키패드/IVR, 인터폰, TTS 발신

- **키패드 제어**: PBX의 아무 전화로 내선에 전화해 숫자를 누르면 IVR 메뉴가 Home Assistant 서비스를 실행합니다 ([예제](docs/examples.md#control-home-assistant-with-dtmf-any-phone)).
- **인터폰 자동 응답**: `sip_contacts.json` 에 `{"102": {"name": "Front Doorbell", "auto_answer": true}}` 처럼 지정하면 도어 스테이션 호출에 즉시 응답합니다 ([상세](docs/examples.md#intercom--auto-answer-mode)).
- **TTS 발신**: 자동화가 전화를 걸고 응답하면 메시지를 읽은 뒤 끊습니다 (`sip.dial` 의 `message`, [예제](docs/examples.md#example-announce-a-tts-message-then-hang-up)).
- **ESPHome**: ESP32를 SIP 내선으로 등록하는 DIY 인터폰/초인종 컴포넌트는 [espcomponents](https://github.com/eigger/espcomponents/tree/master/components/sip_client#readme)를 참고하세요.

## 중요 제한 사항

PBX와 같은 LAN(또는 VPN)에서 실행하세요. UDP 5060이나 RTP(`local_rtp_port`, 기본 7078)를 포트 포워딩하지 마세요. 도어락을 제어할 수 있는 Assist는 허용 목록 + DTMF PIN + 전용 파이프라인을 사용하세요. 자세한 내용은 [보안(영어)](docs/security.md)을 참고하세요.

## 문서 (영어)

- [설정 및 빠른 시작](docs/setup.md) · [PBX 호환성](docs/setup.md#pbx-compatibility)
- [서비스, 엔티티, 이벤트](docs/services.md)
- [예제](docs/examples.md)
- [보안](docs/security.md)
- [문제 해결](docs/troubleshooting.md)
- [ESPHome 외부 컴포넌트](https://github.com/eigger/espcomponents/tree/master/components/sip_client#readme)
- [공통 개발 규칙 적용 기록](STANDARDS.md) · [적용 체크리스트](ADOPTION_CHECKLIST.md)

## 지원 및 라이선스

🐞 [Issues](https://github.com/eigger/hass-sip/issues) · 💡 [Discussions](https://github.com/eigger/hass-sip/discussions)

[LICENSE](LICENSE)
