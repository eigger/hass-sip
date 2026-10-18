# 공통 규칙 적용 체크리스트

hass-sip 의 적용 기록입니다. 실행하지 않은 검사는 PASS 로 표시하지 않았습니다. [사용법과 판정 기준](https://github.com/eigger/hass-repo-standards/blob/7acd9cab6ac774e4695f2c962e3d88b5faa4620d/docs/checklist.md)을 읽고, 아래 규칙은 `STANDARDS.md`에 기록한 표준 커밋에서 확인합니다. 태그·릴리스 대신 `main`의 검토 커밋을 기준으로 하며, 

## 검토 대상

| 항목 | 값 |
|---|---|
| 통합 리포 / 실제 기본 브랜치 | eigger/hass-sip / main |
| domain / 구현 경로 | sip / custom_components/sip |
| 검토 대상 통합 커밋 / 채택 PR | fc6e1f0 / [PR #105](https://github.com/eigger/hass-sip/pull/105) |
| 검토한 표준 전체 커밋 SHA | 7acd9cab6ac774e4695f2c962e3d88b5faa4620d |
| 검토일 / 조율 담당 | 2026-10-18 / 메인 에이전트 |
| 구현 서브 에이전트 / 독립 리뷰·검증 서브 에이전트 | Opus 구현 서브 에이전트 2회 / Opus 독립 리뷰 2회 |
| 중앙 현황표 반영일 / 기록 | 2026-10-18 (진행 중 기록, 최종 갱신은 병합 후) |
| 변경 전 지원 최소 HA / 근거 파일(hacs.json·manifest·README) | 2026.10.0 / hacs.json |
| 유지·변경할 지원 최소 / 변경 시 실제 비호환 근거·명시적 승인 | 2026.10.0 유지(변경 없음) |
| current 테스트 HA / 호환 Python·플러그인 | 2026.10.2 / Python 3.14, pytest-homeassistant-custom-component 0.13.373 |
| 최소 테스트 HA / 호환 Python·플러그인 | 2026.10.0 / Python 3.14, pytest-homeassistant-custom-component 0.13.371 |
| profile / 테스트 경로 | integration / ha, tests/components/sip/ |
| 이전 검토 기록 | 최초 채택 |
| 채택 판정 | 예외 포함 후보 — 필수 EXCEPTION(GOV-05, HA-13, TEST-06) 남음, 기본 브랜치 병합·병합 커밋 검증 전까지 진행 중 |

상태: `TODO` = 미확인·미충족, `PASS` = 근거로 확인, `EXCEPTION` = 적용 대상의 기록된 예외, `N/A` = 적용 조건에 해당 없음. 모든 항목은 미확인으로 시작합니다. `TODO`를 해당 없음으로 처리하지 않습니다. 묶은 규칙의 결과가 다르면 행을 나눕니다. 권장 항목은 별도로 표시했으며 미채택 이유를 기록합니다.

## 채택·구조·사용자 데이터

| 규칙 ID | 확인 항목 | 상태 | 근거 / 남은 작업 |
|---|---|---|---|
| GOV-01, GOV-02 | `STANDARDS.md`에 표준 SHA·profile·실제 기본 브랜치·예외 기록, workflow/action은 검토 SHA로 고정 | PASS | STANDARDS.md 기록, workflow 7acd9ca full SHA 고정 |
| GOV-03 | 새 표준 채택 변경의 영향·이관 방법과 명시적 채택 PR 확인. 규칙 자체를 변경하면 공통 CHANGELOG 기록 | PASS | PR #105 본문에 테스트 인프라 전환·로그 마스킹·번역 키 추가 영향 기재 |
| GOV-04 | 중앙 표에 작업 시작·단계 변경·종료 상태, 담당·적용 내용·검증/리뷰 근거·남은 작업 갱신, 타 리포 기록 보존 | TODO | 중앙 adoption-status.md hass-sip 행 갱신 (진행 기록, 완료 시 최종 갱신) |
| GOV-05 | 구현과 독립 리뷰·검증을 별도 서브 에이전트에게 위임, 지적 수정·재검증 및 결과 취합 | EXCEPTION | 구현 서브 에이전트 2개(테스트 이관, 리뷰 지적 수정) + 독립 Opus 리뷰 2회 수행. 다만 어댑터·문서 작성은 메인 에이전트가 수행 — STANDARDS.md 참조 |
| HA-01, HA-13 | `custom_components/<domain>/`와 domain 일치, 필요한 모듈·공개 API·생명주기는 Core 대응 구조 | EXCEPTION | 디렉터리·domain 일치(PASS). HA-13: 서비스를 async_setup 이 아닌 async_setup_entry 에서 등록하고 runtime_data 가 untyped dict — STANDARDS.md 예외, ROADMAP 에 후속 기록 |
| HA-02 | manifest 필드·정렬·동작 선언 확인, 공식 품질 등급 자가 표기 없음 | PASS | manifest 필드 정렬·media_source 의존성 추가, Hassfest Validate CI https://github.com/eigger/hass-sip/actions/runs/39098721626 |
| HA-03 (권장) | 공통 상수·설정·갱신·플랫폼 역할 및 프로토콜 경계에 맞게 모듈 구성 | TODO | (권장) entity.py 공통 베이스 미도입 — 미채택, 중복 dispatcher 코드 후속 과제 |
| HA-04, HA-08 | UI 설정·중복·안정적 식별 및 기능별 options/reauth/reconfigure, entity 의미·registry 영향 확인 | PASS | user/reconfigure/options flow·중복·unique ID 테스트(test_config_flow.py). reauth 는 repair issue 로 처리(권장 미채택) |
| HA-01, HA-04, HA-08, REL-01, REL-03 | domain·unique ID·entry schema·상태/통계 계약 변경 시 migration 테스트와 사용자 조치 안내 | N/A | entry VERSION=1, unique ID/schema 변경 없음 → migration 불필요 |
| HA-05, HA-06 | setup 실패 구분, unload/reload·부분 실패의 task/listener/연결 정리, async·timeout·취소 처리 | PASS | setup 부분 실패 시 client 정리(test_platform_setup_failure_stops_client), unload/reload/취소 테스트(test_init.py) |
| HA-07 | 중복 polling·property I/O 방지, push 보존, unavailable/unknown·실패 후 복구 | PASS | 기존 폴링 동작 유지(should_poll 변경 안 함 — 통화 중 상태 stale 회귀를 리뷰에서 발견해 되돌림), unavailable 복구·retry 테스트 |
| HA-11, HA-12 | 번역·action/schema 일치, 로그/diagnostics/fixture 비식별화와 제공 시 redaction 테스트 | PASS | strings/en/ko/de/ru 키 일치, 예외 번역 키화, 전화번호 로그 마스킹, diagnostics caller_id/outbound_proxy 마스킹(테스트). username 은 의도적으로 유지 |

## 실제 HA 테스트·호환성·의존성

| 규칙 ID | 확인 항목 | 상태 | 근거 / 남은 작업 |
|---|---|---|---|
| TEST-08 | `tests/components/<domain>/`에 구현과 대응하는 테스트·domain fixture 배치, discovery·CI 경로 일치 | PASS | tests/components/sip/ (test_pure.py 분할은 권장 후속) |
| TEST-02, TEST-03, DEP-07 | 실제 HA·호환 플러그인과 `hass`/`enable_custom_integrations` fixture 사용, HA core stub/복사본/`--no-deps`로 대체하지 않음 | PASS | sys.modules mock 제거, hass/enable_custom_integrations/recorder_mock 사용, HA·플러그인 동시 고정 |
| TEST-09 | 실제 loader·config entry manager로 setup/플랫폼 로딩, HA 상태·registry·service/action으로 결과 검증 | PASS | hass.config_entries.async_setup 로 실제 로딩, states/registry/service 로 검증 |
| TEST-01, TEST-06, TEST-07 | 구현 기능별 성공·실패·중복·migration·unload·복구 회귀 검증, 외부 통신 경계만 mock, skip 사유 | EXCEPTION | 회귀 보존(+31 테스트), flow/setup/unload/reload/DnD/recording/repair 검증(PASS). TEST-06: Assist 테스트가 assist_pipeline 일부를 patch 함(test_pure.py, common.py) — STANDARDS.md 예외 |
| TEST-04, HACS-01 | 변경 전 지원 최소·current·각 Python/플러그인 분리, 선언한 정확한 최소 검증, 상향 시 비호환 근거·명시적 승인 확인 | PASS | current 2026.10.2 / 정확한 최소 2026.10.0 별도 job. 로컬 각 432 passed. Tests CI https://github.com/eigger/hass-sip/actions/runs/39098721627. hacs.json 최소 유지 |
| TEST-05, HA-10, DEP-02, DEP-05 | imagespec·blesession 등 최소/현재 라이브러리 별도 환경, 실제 HA 테스트·정확한 설치값·API 경계 검증 | N/A | manifest requirements·라이브러리 하한 없음, BLE 미사용 |
| DEP-01, DEP-06 | Python runtime은 manifest requirements, HA domain은 dependencies/after_dependencies, 테스트·린트 requirements는 별도 | PASS | Python requirements 없음, HA domain 은 dependencies(media_source 추가)/after_dependencies, 테스트·린트 requirements 분리 |
| DEP-07, CI-06 | 실제 사용하는 HA 구성요소의 requirements·필수 재귀 의존성·선택 로딩 경로 설치, resolver·`pip check` 통과 | PASS | ha-components 로 manifest 의존 HA 컴포넌트 요구사항 설치, pip check 로컬 통과, Tests CI https://github.com/eigger/hass-sip/actions/runs/39098721627 |
| DEP-02, DEP-03, DEP-08 | 의존성 추가/변경의 버전·extras·marker·선언 위치·지원 플랫폼·라이선스 검토, 실제 HA 테스트와 문서 갱신 | PASS | 테스트 의존성 변경(plugin 도입)만 해당: 존재하는 버전, 실제 HA 테스트·문서(CONTRIBUTING) 갱신 |

독립 리뷰는 변경 전후 지원 최소와 current를 비교하고, 봇 PR이 과거 최소 requirements·constraints까지 갱신하거나 expected-ha-version·Python을 누락하지 않았는지 확인합니다. 최신 테스트 실패는 호환 수정 대상으로 처리합니다.

환경마다 아래 행을 복사합니다. 최소 HA와 최소 라이브러리 검증을 혼동하지 않고, marker가 활성인 환경에서 대상 패키지 설치값을 확인합니다. 실행하지 않은 조합은 `TODO` 또는 근거가 있는 `EXCEPTION`으로 남깁니다.

| 검증 축 / 상태 | 통합 커밋 | Python / HA / 플러그인 실제 버전 | 대상 패키지 실제 버전·extras·marker | 명령·테스트 결과·pip check / CI run·job | skip·제외·미검증 범위 |
|---|---|---|---|---|---|
| 현재 환경 / PASS(로컬) | fc6e1f0 | Python 3.14.5 / HA 2026.10.2 / phcc 0.13.373 | 해당 없음 | `pytest` 432 passed, `uv pip check` 통과 / Tests CI https://github.com/eigger/hass-sip/actions/runs/39098721627 | ffmpeg 바이너리 없는 환경(FfmpegAudioSource 실행 경로 미검증) |
| 정확한 최소 HA / PASS(로컬) | 동일 | Python 3.14.5 / HA 2026.10.0 / phcc 0.13.371 / frontend 20260930.2 | 해당 없음 | 동일 명령 432 passed, pip check 통과 / Tests CI https://github.com/eigger/hass-sip/actions/runs/39098721627 | 위와 동일 |
| 정확한 최소 라이브러리 / N/A | - | - | manifest requirements 없음 | - | - |

## HACS·CI·의존성 업데이트

| 규칙 ID | 확인 항목 | 상태 | 근거 / 남은 작업 |
|---|---|---|---|
| HACS-01, HACS-02 | 루트 README/LICENSE/hacs.json, 검증한 최소 HA, 실제 배포 구조와 필요한 옵션 일치 | PASS | 루트 파일·hacs.json 최소 유지, HACS Validate CI https://github.com/eigger/hass-sip/actions/runs/39098721626 |
| HACS-03, HACS-05 | Default 여부는 목록 근거로 확인, [로컬 brand/icon.png 또는 원격 brands 근거](https://github.com/eigger/hass-repo-standards/blob/main/docs/hacs.md)와 HACS/Hassfest 실제 실행, ignore는 이유·해제 조건 기록 | PASS | 로컬 brand 존재, ignore 없음, HACS/Hassfest Validate CI https://github.com/eigger/hass-sip/actions/runs/39098721626 |
| HACS-06 (권장) | description·topics·issue 경로·brands 및 사용하는 HACS 필드 지원 확인 | TODO | (권장) description/topics 확인됨. hacs.json render_readme 유지 여부 미확정 |
| CI-01, CI-02 | Validate/Python Tests 공통화, 확장 job 유지, PR·실제 기본 브랜치 push·dispatch 및 필요한 schedule | PASS | Tests/Validate caller, PR·main push·dispatch·schedule. Tests CI https://github.com/eigger/hass-sip/actions/runs/39098721627; Validate CI https://github.com/eigger/hass-sip/actions/runs/39098721626 |
| CI-03, CI-05, CI-08 | 최소 권한·checkout 자격증명 비보존·timeout·concurrency, full SHA·갱신 관리, 비신뢰 입력·secrets 경계 | PASS | contents: read, full SHA, concurrency, secrets 미사용 (중앙 workflow 의 timeout·persist-credentials) |
| CI-04, CI-07, CI-12 | 실패를 숨기지 않는 실제 `ha` 필수 테스트, 버전별 환경·cache·Ruff 설정과 required checks 이름 확인 | PASS | 실패 은닉 없음, ha profile 필수 테스트, ruff==0.17.1 고정. check 이름이 'unit'→'ha' 로 변경됨(브랜치 보호 없음). Tests CI https://github.com/eigger/hass-sip/actions/runs/39098721627 |
| DEP-03, DEP-04, CI-09 | updater의 선택 package·연산자/extras/marker 보존, 안정 버전·major 정책, 실제 기본 브랜치·단일 bot branch·PR만 제안 | N/A | manifest requirements 없음 — dependency-update workflow 미도입 |
| CI-09, CI-10, DEP-08 | 변경 tree의 새 하한·실제 HA 검증 후 PR 동기화, 실패 시 중단·변경 없음 시 기존 PR 정리, 후속 required checks 충족 방식 확인 | N/A | manifest requirements 없음 — dependency-update workflow 미도입 |

## README·Bluetooth·유지보수

| 규칙 ID | 확인 항목 | 상태 | 근거 / 남은 작업 |
|---|---|---|---|
| DOC-01, DOC-02, DOC-06 | 영어 README.md·한국어 README.ko.md·상호 링크·기존 내용 보존, 간결한 제목/소개/핵심지원/최소요구/빠른설치·설정/짧은예제/중요제한/문서목록, 상세옵션·긴YAML·참조·개발/실HA테스트·의존성/검증범위를 docs·examples 등으로 보존·분리하고 유효한 링크 제공, 실제 활성 Issues·Discussions 또는 대체 지원 경로·실제 LICENSE 링크 | PASS | README/README.ko 상호 링크·요구사항·HACS custom 고지, 긴 예제는 docs/use-cases.md, 개발 안내 CONTRIBUTING.md, 링크·앵커 검사 |
| DOC-03, DOC-04, DOC-05 | 제목 아래 관리 블록, flat-square·순서·실제 조건별 배지, CI 실제 기본 브랜치, 필수 동적 Usage·Analytics 참여 설치 의미·미집계/조회 실패와 0 설치 구분·이전 안내 보존 | PASS | sync_badges.py --check 통과 |
| HA-09, HA-10 | BLE 사용 시 HA Bluetooth API·라이브러리 공개 경계, 지원한 proxy 경로·재연결·취소·서비스 변경 검증 | N/A | BLE 미사용 |
| DOC-07 | blesession/proxy 통합 README의 핵심조건·상세문서 링크, 공통 Bluetooth 설정·추천기기·active 스캔/GATT 차이, 통합별 troubleshooting 또는 공통 링크+모델고유조건·실제진단·민감정보 제거 제보 안내 | N/A | BLE 미사용 |
| DOC-07, HA-12 | 기기별 깨우기·인증·타 앱 연결·전송 제약, 실제 검증 경로와 미검증 범위, 공개 문제 로그 비식별화 | N/A | BLE 미사용 (HA-12 는 위 행) |
| HACS-04, REL-01, REL-02, REL-03, CI-11 | 통합 릴리스 시 같은 검증 commit/tag·manifest 버전·산출물·권한, 사용자 영향·지원 변경·제한 안내 | N/A | 이 PR 은 릴리스를 만들지 않음. manifest 3.5.0 = Release 3.5.0. 다음 릴리스 노트에 테스트 전환·로그 마스킹 기재 |
| REL-04 | 이전/중단 시 README 상단 경로·유지보수 상태·재등록 여부, 자동 이전으로 오인하지 않는 안내 | N/A | 이전·중단 아님 |
| REL-05 (권장) | 의존성/HA API 변경과 기능 변경 분리, 표준 변경은 명시적 채택 PR로 반영 | PASS | 표준 채택 PR 과 동작 변경(로그·번역·정리)을 구분해 기재. should_poll 변경은 되돌려 제외 |

## 예외와 남은 작업

예외 원본은 `STANDARDS.md`입니다. 아래에 해당 위치를 연결하고, 예외가 실제 없음을 확인한 뒤에만 `없음`으로 바꿉니다.

| 규칙 ID | 예외 위치 / 이유 | 담당자 | 재검토 조건 |
|---|---|---|---|
| GOV-05, HA-13, TEST-06 | STANDARDS.md 예외표 | @eigger | 예외표의 재검토 조건 |

| 규칙 ID / TODO | 남은 작업 / issue·PR | 담당자 | 완료 근거 |
|---|---|---|---|
| 미확인 | 미확인 | 미확인 | 미확인 |

최종 검토 시 적용 대상 필수 `TODO`와 `EXCEPTION`, 이유가 있는 `N/A`, 권장 미채택을 구분해 적습니다. CI 성공만으로 실물 기기 검증을 주장하지 않습니다. 실물 검증을 수행했다면 모델·펌웨어·어댑터/proxy·연결 경로와 결과를 별도로 기록합니다.

중앙 보고는 [적용 현황표](https://github.com/eigger/hass-repo-standards/blob/main/docs/adoption-status.md)의 자기 리포 행에 반영합니다. 리뷰 지적·수정·재검증 결과와 중앙 갱신 실패 사유가 있으면 남은 작업에도 기록합니다.
| 중앙 표 최종 갱신 | PR 병합 + 병합 커밋 CI 확인 후 adoption-status.md 행을 '예외 포함' 으로 | @eigger | 미완 |
| HA-13 | 서비스 async_setup 등록, typed runtime_data, 죽은 untargeted fallback 제거 | @eigger | 미완 |
| TEST-06 | assist_pipeline 실제 provider 로 Assist 테스트 | @eigger | 미완 |
| 권장 | HA-03 entity.py, reauth, test_pure.py 분할, HACS-06 render_readme | @eigger | 미완 |

## 독립 리뷰 결과 (Opus 서브 에이전트)

- 1차: 체크리스트 과장(PASS) 정정, 필수 행 복원.
- 2차(전 행 검토): should_poll=False 회귀(통화 중 상태 stale)·setup 부분 실패 client 누수·media_source 의존성 누락·번역 안 된 예외·로그 번호 노출 발견 → 수정(되돌림 포함) 후 테스트 432 passed. ruff format 은 AST 동일 확인.
