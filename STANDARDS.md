# 적용한 공통 규칙

- 표준: https://github.com/eigger/hass-repo-standards/tree/0ec31111a81b0ee5702f1d8a17f03616a10a5db6
- 참조 커밋 SHA: 0ec31111a81b0ee5702f1d8a17f03616a10a5db6
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
