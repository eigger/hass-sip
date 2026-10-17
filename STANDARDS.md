# 적용한 공통 규칙

- 표준: https://github.com/eigger/hass-repo-standards/tree/7acd9cab6ac774e4695f2c962e3d88b5faa4620d
- 참조 커밋 SHA: 7acd9cab6ac774e4695f2c962e3d88b5faa4620d
- 채택일: 2026-10-18 (채택 PR 전 — PR 번호는 병합 시 기록)
- profile: integration / **unit (부분 채택)** — 실제 HA fixture 테스트(ha profile)는 미이관, 아래 예외 참조
- 적용 점검표: [ADOPTION_CHECKLIST.md](ADOPTION_CHECKLIST.md)
- 중앙 현황표: https://github.com/eigger/hass-repo-standards/blob/main/docs/adoption-status.md
- 조율·구현: 메인 에이전트 (구현 서브 에이전트 없음 — GOV-05 예외) / 독립 리뷰: Opus 서브 에이전트
- 중앙 현황 갱신일: 미등록 (중앙 리포 권한·범위 밖, 반영할 내용은 ADOPTION_CHECKLIST.md 하단)
- 실제 기본 브랜치: main
- 테스트 경로: tests/ (표준 목표: tests/components/sip/)
- 변경 전 지원 최소 HA / 선언 근거: 2026.10.0 / hacs.json
- 적용 후 지원 최소 HA: 2026.10.0 (변경 없음)
- current HA 테스트 환경: Python 3.14 / homeassistant 2026.10.2 / pytest 단독 (pytest-homeassistant-custom-component 미사용)
- 최소 HA 검증 환경: Python 3.14 / homeassistant 2026.10.0 (requirements_test_min.txt)
- 최소 package 검증: 해당 없음 (manifest requirements 없음)

## 예외

| 규칙 ID | 이유 | 담당자 | 재검토 조건 |
|---|---|---|---|
| TEST-02, TEST-03, TEST-08, TEST-09, DEP-07 | 기존 테스트가 tests/conftest.py 에서 homeassistant 모듈을 sys.modules 로 대체함. pytest-homeassistant-custom-component 0.13.373 은 이와 충돌(393 errors, 로컬 확인)하여 도입 불가. 이관은 별도 작업 | @eigger | tests/components/sip/ 에 실제 hass fixture 기반 setup/unload 테스트 추가 PR |
| TEST-04 | current(2026.10.2)/최소(2026.10.0) job 은 분리했으나 HA 가 mock 이라 실제 HA 버전 동작은 검증하지 못함(설치·pip check 만) | @eigger | 위와 동일 |
| CI-07, CI-12 | 위 사유로 ha profile 대신 unit profile 사용 | @eigger | 위와 동일 |
| GOV-05 | 구현을 별도 서브 에이전트에 위임하지 않고 메인 에이전트가 수행 | @eigger | 다음 채택 갱신 시 |
| CI-09, DEP-03, DEP-04 | manifest requirements 가 없어 dependency-update workflow 미도입 | @eigger | requirements 추가 시 |
