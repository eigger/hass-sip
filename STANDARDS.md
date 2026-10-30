# 적용한 공통 규칙

- 표준: https://github.com/eigger/hass-repo-standards/tree/v1.1.5
- 참조 커밋 SHA: f56987f7f765a774c926fb8005fe5df68bb32451
- Standards release: v1.1.5
- Resolved standards SHA: f56987f7f765a774c926fb8005fe5df68bb32451
- 최초 채택: 2026-10-18, [PR #105](https://github.com/eigger/hass-sip/pull/105) / 병합 a960d49
- 재검토: 2026-10-22, 공용 템플릿·CI 갱신 및 확인된 HACS Default 반영 (로컬 변경, 원격 CI 미실행)
- profile: integration / ha
- 적용 점검표: [ADOPTION_CHECKLIST.md](ADOPTION_CHECKLIST.md)
- 중앙 현황표: https://github.com/eigger/hass-repo-standards/blob/main/docs/adoption-status.md
- 이번 조율 / 구현 / 독립 리뷰: root / implementation·platform_tests / independent_review
- 최초 채택 구현·리뷰: Opus 서브 에이전트 (2026-10-18 이력)
- 중앙 현황: 2026-10-18 최초 채택 종료 기록 확인. 이번 행은 최신 SHA의 별도 임시 checkout에 준비했으나 원격에는 미게시. 기존 중앙 로컬 체크아웃의 사용자 변경은 보존
- 실제 기본 브랜치: main
- 테스트 경로: tests/components/sip/
- 변경 전 지원 최소 HA / 선언 근거: 2026.10.0 / hacs.json
- 적용 후 지원 최소 HA: 2026.10.0 (변경 없음)
- current HA 테스트 환경: Python 3.14 / homeassistant 2026.10.2 / pytest-homeassistant-custom-component 0.13.373
- 최소 HA 검증 환경: Python 3.14 / homeassistant 2026.10.0 / pytest-homeassistant-custom-component 0.13.371 (requirements_test_min.txt)
- 최소 package 검증: 해당 없음 (manifest requirements 없음)

## 예외

| 규칙 ID | 이유 | 담당자 | 재검토 조건 |
|---|---|---|---|
| GOV-05 | 구현은 서브 에이전트가 했지만 workflow/README/문서 작성은 메인 에이전트가 직접 수행 | @eigger | 다음 표준 갱신 시 |
| HA-13 | 서비스가 async_setup_entry 에서 등록되고 runtime_data 가 untyped dict, 도달 불가한 untargeted fallback 존재. 동작 변경 위험이 커 별도 PR | @eigger | runtime_data typed dataclass + async_setup 서비스 등록 PR |
| TEST-06 | Assist 단위 테스트(test_pure.py, common.py)가 HA assist_pipeline 일부(async_pipeline_from_audio_stream 등)를 patch 함. 외부 통신 경계는 mock PBX 만 사용 | @eigger | 테스트용 STT/TTS provider 로 실제 assist_pipeline 을 쓰는 테스트 추가 |
| CI-09, DEP-03, DEP-04 | manifest requirements 가 없어 dependency-update workflow 미도입 | @eigger | requirements 추가 시 |

## 2026-10-22 재검토 범위

- 이전 기준: `7acd9cab6ac774e4695f2c962e3d88b5faa4620d`. 새 기준은 `git ls-remote`로 확인한 중앙 main `0ec31111a81b0ee5702f1d8a17f03616a10a5db6`.
- HACS Default 근거: [공식 integration 목록](https://github.com/hacs/default/blob/master/integration)의 `eigger/hass-sip`, 2026-10-22 확인. 검증 성공이나 브랜드 등록을 등재 근거로 사용하지 않음.
- README 관리 영역·flat 배지·공식 HACS Downloads·My Home Assistant 설치 링크 및 공통 CI·테스트 기반을 새 중앙 계약에 맞춤. SIP 동작·HA 최소 2026.10.0·현재 테스트 HA 2026.10.2·통합 버전은 유지.
- 기존 HA-13 / TEST-06 구현 예외는 이번 문서·CI 갱신으로 해소되지 않음. 최초 채택의 GOV-05 기록은 당시 이력이며 이번 구현과 독립 리뷰는 서로 다른 서브 에이전트에게 위임.
- 현재 검증 및 미검증 범위는 ADOPTION_CHECKLIST.md 첫 절에 기록. 과거 432개 통과는 이번 변경의 검증 근거로 재사용하지 않음.

### Hassfest categories 스키마 호환 경로

- 독립 리뷰에서 2026-10-22 현재 게시된 Hassfest 이미지의 소스 레이어를 조회·SHA256 검증하여 `manifest.py`에 categories 지원이 없음을 확인. 이미지 설정 digest `sha256:1347798a3d984bfa6f5ff5cd858b1e115adc236d6836aca4e5b2fb06ca992f12` (생성 2026-10-16), 소스 레이어 `sha256:e0d5da91bec494178e85f637e8f0753193179d5156a5edfbd98f44d0b4c9bd04`.
- 중앙 [공식 Core source 경로](https://github.com/eigger/hass-repo-standards/blob/0ec31111a81b0ee5702f1d8a17f03616a10a5db6/docs/ci.md)에 따라 Validate `hassfest-core-ref`를 `ec18d8b25d1ee39aa8b85a72a170e42bee33228c`로 지정. [검토한 upstream manifest 검사](https://github.com/home-assistant/core/blob/ec18d8b25d1ee39aa8b85a72a170e42bee33228c/script/hassfest/manifest.py)에 categories 스키마가 있음.
- 이 입력은 검토한 공식 Core Dockerfile로 빌드해 검사하는 경로이며 validator를 우회·ignore하지 않음. 통합 지원 최소나 HA 테스트 환경은 변경하지 않음.
- 로컬 Docker daemon 미실행으로 실제 이미지 빌드·Hassfest 실행은 미검증. 원격 CI에서 확인해야 하며 PASS로 기록하지 않음. 공식 게시 이미지가 해당 스키마를 지원하면 같은 통합 변경으로 실제 재검증 후 입력을 제거. 담당 @eigger.

## 2026-10-23 다운로드 배지 부분 갱신

- 배지·DOC-03/05와 Validate 형식 검사기만 중앙 [`b54ac48102d6e326c6929071fdc02ba8fade794e`](https://github.com/eigger/hass-repo-standards/tree/b54ac48102d6e326c6929071fdc02ba8fade794e)를 채택합니다. 이전 기준 SHA `0ec31111a81b0ee5702f1d8a17f03616a10a5db6`의 채택 이력·예외는 보존합니다. 모든 중앙 workflow/action pin과 standards-ref는 GOV-02의 단일 기준 SHA 계약에 맞춰 갱신합니다. 중앙 diff에서 공용 workflow/action 실행 파일은 이전 기준과 동일함을 확인했습니다.
- 대상 기본 브랜치 커밋: `6494a4b78011da79cffb046112bdf9e596ba055a`. 주 에이전트 직접 구현·자체 리뷰(GOV-05의 배지/생성물 변경 범위), 검증은 [ADOPTION_CHECKLIST.md](ADOPTION_CHECKLIST.md#2026-10-23-downloads-배지-부분-검증)에 기록합니다.
- Downloads는 현재 릴리스 첨부 asset 다운로드 합계입니다. 사용자 지시에 따라 ZIP 배포를 도입하지 않으며 현재 HACS 소스 파일 설치는 이 수치에 집계되지 않습니다. 통합 코드·의존성·지원 최소·기존 설치 안내는 변경하지 않습니다.

## 2026-10-23 ZIP 릴리스 배포

- 기준 중앙 SHA: `0c1592977591ecba077a4666d699840062711201` ([중앙 PR15](https://github.com/eigger/hass-repo-standards/pull/15) 스쿼시 머지). 이전 `b54ac48102d6e326c6929071fdc02ba8fade794e` 채택 이력·예외를 보존하며 HACS-04·CI-11의 ZIP 배포 계약을 추가합니다.
- 대상 시작 SHA: `b263f160ab5c04ee85e5cc61b3fb412374527ce2`. hacs.json의 zip_release/filename과 중앙 release caller를 함께 적용합니다. archive 파일명은 `sip.zip`, ZIP 루트는 해당 통합 파일입니다.
- 중앙 workflow/action pin과 standards-ref는 단일 SHA로 정합화합니다. release:published에서 기존 해당 릴리스에 ZIP만 첨부하며 소스 버전·태그·릴리스 발행은 자동 변경하지 않습니다. 기존 asset 덮어쓰기를 거부합니다.
- 기존 지원 최소 HA `2026.10.0`와 통합 버전 `3.6.2` 및 의존성·구현은 보존합니다. 기존 태그의 hacs.json은 소급 수정하지 않으므로 다음 릴리스부터 ZIP 설치로 전환됩니다.
- immutable-releases 설정은 enabled=false/enforced_by_owner=false로 확인했습니다. 업로드 지연·실패 및 기존 asset 복구는 중앙 [릴리스 계약](https://github.com/eigger/hass-repo-standards/blob/0c1592977591ecba077a4666d699840062711201/docs/releases.md#zip-릴리스-첨부)을 따릅니다. 실제 새 릴리스 발행·설치·기기 검증은 이번 작업에 포함하지 않습니다.

## 2026-10-25 중앙 검사기 갱신

- 검토한 중앙 main: `1863343638014adb8a4001afc8fddc1a27dc500f` (git ls-remote 확인). 이전 기본 기준 `0c1592977591ecba077a4666d699840062711201`의 채택 이력은 위 기록을 보존합니다.
- 모든 중앙 workflow/action pin과 reusable workflow의 standards-ref를 같은 검토 SHA로 맞춥니다. HACS-04는 actions/*의 full SHA 고정을 유지하면서 SHA 값 차이를 허용합니다.
- 통합 코드·런타임 의존성·지원 최소·배포 버전은 이번 변경 대상이 아닙니다. 기존 채택 범위를 전체 완료로 바꾸지 않으며 실제 검증 결과와 남은 항목은 ADOPTION_CHECKLIST.md의 같은 날짜 기록을 참조합니다.


### 2026-10-25 Standards 파일·표시 이름 분리

- 기존 Validate의 중앙 검사 job을 `standards.yml`로 옮기고 workflow/job 표시 이름을 `Standards`로 줄였습니다. Validate의 HACS/Hassfest, 검사 항목·통과 기준·실행 단계·조건·권한은 유지합니다.
- 위치 인식과 중앙 명명 안내를 맞춘 검토 소스: `ca740d88b32fa5dcbc94c41fce2908450c76ed50` ([중앙 PR #24](https://github.com/eigger/hass-repo-standards/pull/24), 현재 이 PR의 의존 변경). 이전 기준 `1863343638014adb8a4001afc8fddc1a27dc500f`의 검증 이력은 앞 기록으로 보존합니다. 모든 중앙 pin·standards-ref는 같은 새 검토 소스로 맞춥니다.

## 2026-10-25 중앙 immutable release 참조 전환

- 사용자 요청에 따라 현재 중앙 참조를 immutable `v1.0.0` / resolved SHA `44349ee5621eea5d80e5c427f7a2435d2c2491bb`로 전환했습니다. 중앙 workflow/action caller는 exact release tag를, `standards-ref`는 resolved full SHA를 사용합니다.
- 범위는 중앙 참조·기록 정합화뿐입니다. 기존 지원 최소, 통합 버전, manifest, 런타임 요구사항, 구현, 테스트와 CI의 caller-owned 설정은 보존했습니다.
- 과거 SHA·검증·예외 기록은 역사적 증거로 보존합니다. 이번 변경은 generated reference-only 범위이므로 GOV-05 자체 검토이며 PR CI/병합은 대기 상태입니다.
