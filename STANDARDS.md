# 적용한 공통 규칙

- 표준: https://github.com/eigger/hass-repo-standards/tree/7acd9cab6ac774e4695f2c962e3d88b5faa4620d
- 참조 커밋 SHA: 7acd9cab6ac774e4695f2c962e3d88b5faa4620d
- 채택일: 2026-10-18 (채택 PR 전 — PR 번호는 병합 시 기록)
- profile: integration / ha
- 적용 점검표: [ADOPTION_CHECKLIST.md](ADOPTION_CHECKLIST.md)
- 중앙 현황표: https://github.com/eigger/hass-repo-standards/blob/main/docs/adoption-status.md
- 조율 / 구현 / 독립 리뷰: 메인 에이전트 / Opus 구현 서브 에이전트(테스트 이관, 수정) / Opus 독립 리뷰 서브 에이전트
- 중앙 현황 갱신일: 2026-10-18 (진행 중)
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
