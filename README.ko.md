<!-- hass-standards:header:start -->
# SIP Client Home Assistant Integration (hass-sip)

SIP 통화, IVR, 인터폰, 음성 Assist를 위한 Home Assistant 커스텀 통합입니다.

[English](README.md) | **한국어**

<!-- hass-standards:badges:start -->
[![Release](https://img.shields.io/github/v/release/eigger/hass-sip?style=flat)](https://github.com/eigger/hass-sip/releases)
[![HACS Default](https://img.shields.io/badge/HACS-Default-41BDF5?style=flat)](#설치)
[![Home Assistant minimum](https://img.shields.io/badge/HA-%E2%89%A5%202026.10.0-41BDF5?style=flat)](hacs.json)
[![Tests](https://img.shields.io/github/actions/workflow/status/eigger/hass-sip/tests.yml?branch=main&style=flat&label=Tests)](https://github.com/eigger/hass-sip/actions/workflows/tests.yml)
[![Validate](https://img.shields.io/github/actions/workflow/status/eigger/hass-sip/validate.yml?branch=main&style=flat&label=Validate)](https://github.com/eigger/hass-sip/actions/workflows/validate.yml)
[![License](https://img.shields.io/github/license/eigger/hass-sip?style=flat)](LICENSE)
[![Downloads](https://img.shields.io/github/downloads/eigger/hass-sip/total?style=flat&color=41BDF5&label=Downloads)](https://github.com/eigger/hass-sip/releases)
<!-- hass-standards:badges:end -->
<!-- hass-standards:header:end -->

Downloads 배지는 모든 GitHub 릴리스의 첨부 asset 다운로드 합계이며 활성 설치 수가 아닙니다. HACS 소스 파일 설치는 집계되지 않으며 ZIP 릴리스 배포는 사용하지 않습니다.

hass-sip는 PBX에 **SIP 내선으로 등록**됩니다. 이 회선을 미디어 플레이어로 노출하고, DTMF 송수신, IVR 메뉴, 통화 녹음, Home Assistant 음성 Assist 연결을 지원합니다. HACS 커스텀 통합이며 Home Assistant Core에 포함된 통합이 아닙니다.

전송은 **UDP 기반 SIP**이고 **G.711(PCMU/PCMA)과 G.722**를 지원합니다. TLS, SRTP, Opus는 구현되어 있지 않습니다. **인터넷 노출은 지원하지 않으며** 검증되지 않은 PBX를 "지원"으로 표기하지 않습니다.

| 내선 등록 | 자동화 | 참고 자료 |
|---|---|---|
| [설정 및 빠른 시작](docs/setup.md) | [예제](docs/examples.md) | [서비스, 엔티티, 이벤트](docs/services.md) |
| [PBX 호환성](docs/setup.md#pbx-compatibility) | [보안](docs/security.md) | [문제 해결](docs/troubleshooting.md) |
| [ESPHome 컴포넌트](#esphome-외부-컴포넌트) | [인터폰 예제](docs/examples.md#intercom--auto-answer-mode) | [espcomponents 리포지토리](https://github.com/eigger/espcomponents/tree/master/components/sip_client#readme) |

위 상세 문서는 영어로 제공됩니다.

## 사용 목적

| 사용 사례 | 동작 | 상세 (영어) |
|---|---|---|
| 음성 제어 | 내선에 전화해 응답 후 Assist와 대화하며 한 통화에서 여러 명령 실행 | [사용 사례](docs/use-cases.md#control-home-assistant-by-voice) |
| 전화 키패드 | PBX의 아무 전화로 숫자를 누르면 IVR 메뉴가 Home Assistant 서비스 실행 | [사용 사례](docs/use-cases.md#control-home-assistant-with-the-phone-keypad) |
| 인터폰 | 도어 스테이션이 호출하면 hass-sip가 자동 응답하고 선택적으로 DTMF로 문 열기 | [사용 사례](docs/use-cases.md#intercom-auto-answer) |
| Home Assistant에서 전화 걸기 | 자동화가 전화로 TTS 메시지 전달 | [사용 사례](docs/use-cases.md#home-assistant-calls-you-with-tts) |

도어락에는 [허용 목록 + PIN](docs/security.md#door-locks-and-other-security-intents)을 사용하세요.

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

<!-- hass-standards:installation:start -->
[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=eigger&repository=hass-sip)
<!-- hass-standards:installation:end -->

1. **HACS**: HACS 기본 저장소 목록에서 **SIP Client**(`eigger/hass-sip`)를 검색해 다운로드하거나, **수동**으로 `custom_components/sip` 폴더를 Home Assistant의 `custom_components`에 복사합니다.
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

![전화로 Home Assistant에 걸어 Assist로 조명을 켜고 밝기를 조절하는 스토리보드](https://raw.githubusercontent.com/eigger/hass-sip/main/docs/demo/phone-assist.gif)

시나리오별 데모(음성, 키패드, 인터폰, TTS 발신)는 [사용 사례(영어)](docs/use-cases.md), 더 많은 예제는 [예제(영어)](docs/examples.md)에 있습니다.

### 키패드/IVR, 인터폰, TTS 발신

- **키패드 제어**: PBX의 아무 전화로 내선에 전화해 숫자를 누르면 IVR 메뉴가 Home Assistant 서비스를 실행합니다 ([예제](docs/examples.md#control-home-assistant-with-dtmf-any-phone)).
- **인터폰 자동 응답**: `sip_contacts.json` 에 `{"102": {"name": "Front Doorbell", "auto_answer": true}}` 처럼 지정하면 도어 스테이션 호출에 즉시 응답합니다 ([상세](docs/examples.md#intercom--auto-answer-mode)).
- **TTS 발신**: 자동화가 전화를 걸고 응답하면 메시지를 읽은 뒤 끊습니다 (`sip.dial` 의 `message`, [예제](docs/examples.md#example-announce-a-tts-message-then-hang-up)).
- **ESPHome**: ESP32를 SIP 내선으로 등록하는 DIY 인터폰/초인종 컴포넌트는 [espcomponents](https://github.com/eigger/espcomponents/tree/master/components/sip_client#readme)를 참고하세요.

## ESPHome 외부 컴포넌트

ESP32 인터폰, 초인종, 방송용 스피커 같은 DIY 물리 단말을 만들려면 [**eigger/espcomponents의 `sip_client`**](https://github.com/eigger/espcomponents/tree/master/components/sip_client#readme) ESPHome 외부 컴포넌트를 참고하세요.

ESP32를 PBX의 SIP 내선으로 등록하며 ESPHome 표준 `microphone`과 `speaker`(I2S 오디오), G.711 / 광대역 G.722 코덱, DTMF, 전이중 또는 반이중 모드를 지원합니다. 전체 설정, 하드웨어 배선, YAML 예제는 [espcomponents 리포지토리(영어)](https://github.com/eigger/espcomponents/tree/master/components/sip_client#readme)에 있습니다.

## 중요 제한 사항

PBX와 같은 LAN(또는 VPN)에서 실행하세요. UDP 5060이나 RTP(`local_rtp_port`, 기본 7078)를 포트 포워딩하지 마세요. 도어락을 제어할 수 있는 Assist는 허용 목록 + DTMF PIN + 전용 파이프라인을 사용하세요. 자세한 내용은 [보안(영어)](docs/security.md)을 참고하세요.

## 문서 (영어)

- [설정 및 빠른 시작](docs/setup.md) · [PBX 호환성](docs/setup.md#pbx-compatibility)
- [서비스, 엔티티, 이벤트](docs/services.md)
- [사용 사례](docs/use-cases.md)
- [예제](docs/examples.md)
- [보안](docs/security.md)
- [문제 해결](docs/troubleshooting.md)
- [ESPHome 외부 컴포넌트](https://github.com/eigger/espcomponents/tree/master/components/sip_client#readme)
- [공통 개발 규칙 적용 기록](STANDARDS.md) · [적용 체크리스트](ADOPTION_CHECKLIST.md)

## 개발

로컬 테스트·ruff·실제 HA 테스트 구조는 [CONTRIBUTING(영어)](CONTRIBUTING.md)을 참고하세요. 이 리포는 [hass-repo-standards](https://github.com/eigger/hass-repo-standards)를 따르며 [STANDARDS.md](STANDARDS.md)에 기록합니다.

<!-- hass-standards:support:start -->
## 지원

- [Issues](https://github.com/eigger/hass-sip/issues): 버그를 신고하거나 기능을 요청하세요.
- [Discussions](https://github.com/eigger/hass-sip/discussions): 질문하고 사용 경험을 공유하세요.
<!-- hass-standards:support:end -->

<!-- hass-standards:related-projects:start -->
## 관련 프로젝트

- [espcomponents / sip_client](https://github.com/eigger/espcomponents/tree/master/components/sip_client#readme): ESP32 SIP 단말을 위한 ESPHome 외부 컴포넌트입니다.
- [hass-repo-standards](https://github.com/eigger/hass-repo-standards): [STANDARDS.md](STANDARDS.md)에 적용을 기록한 공통 개발 규칙입니다.
<!-- hass-standards:related-projects:end -->

<!-- hass-standards:license:start -->
## 라이선스

[MIT License](LICENSE) 라이선스로 배포됩니다.
<!-- hass-standards:license:end -->
