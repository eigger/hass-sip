# hass-sip 작업 계획

완료된 P0–P5 카드(P3-5 제외)는 지웠다. 이 문서는 남은 일과 작업 원칙만 담는다.

---

## 0. 사용법

- 작업 단위는 카드다. 카드 하나가 PR 하나에 대응하는 크기다.
- 카드를 시작하기 전에 아래 원칙을 읽는다. 특히 "건드리지 말 것"은 이미 검증된
  자산을 되돌리지 않기 위한 것이다.
- 카드 작업이 끝나면 `[x]`를 표시하고, 수용 기준으로 추가한 테스트 이름을 기록한다.
- 카드를 진행하다 계획 자체가 틀렸다고 판단되면 코드를 밀어붙이지 말고 이 문서를 고친다.

---

## 1. 작업 원칙

### 건드리지 말 것

- `sip_client/` 의 계층 구조. 이미 분리돼 있다. HA 의존을 절대 추가하지 않는다.
- `codecs.py`의 G.722 clock/sample rate 분리와 G.722 참조 벡터 테스트.
- 이미 통과하는 테스트. 기존 테스트를 수정해야 한다면 그건 행동 변경이므로
  PR 설명에 이유를 명시한다.
- 라우팅/트랜잭션 처리(strict/loose router, Record-Route, forked 2xx, CANCEL 경합).
  실제 이슈를 갚아온 코드이므로 "정리"하지 않는다.

### 지킬 것

- SIP core 수정은 반드시 회귀 테스트를 함께 추가한다 (`tests/test_pure.py` 또는
  전용 테스트 파일).
- SIP core에는 `homeassistant` import 금지. 새 기능이 HA를 필요로 하면 콜백/주입으로 뺀다.
- 이벤트 루프에서 블로킹 I/O 금지(파일, 소켓, subprocess 대기).
- 로그에 크리덴셜, `Authorization` 헤더 값, digest response를 절대 남기지 않는다.
- `ruff check custom_components/` 통과. CI는 Python 3.14, `homeassistant>=2026.8.0`.
- 사용자에게 보이는 동작이 바뀌면 `strings.json` + `translations/{en,ko,de,ru}.json`을
  함께 갱신한다.

### 검증

```
python -m pytest tests/
ruff check custom_components/ tests/
```

SIP core는 HA 없이 단독 로드할 수 있다. `tests/test_pure.py` 상단의
`_load_pkg_module` / `_load_component_module` 패턴으로 `SipClient`에 메시지를
주입하고 `_send_raw`를 patch해 송신 문자열을 검사한다.

---

## 2. 남은 카드

### [ ] P3-5. RTP TX를 버퍼 점유량으로 페이싱

**근거** #46 리뷰: `_RealtimePacer`는 벽시계 0.5 s 리드이고 `_sender`는 늦으면
프레임을 따라잡지 않고 시계만 맞춘다. 점유가 1 s를 넘으면 앞에서 드롭.
`sip.dial` TTS가 2.0.1 이후에도 끊기면 이 층이다.

**작업** (리포터 재확인 또는 로컬 재현 후에만)
- 페이서를 `len(_tx_buffer)` 기준으로 바꿔 AudioSource가 TX 백로그를 보게 한다.
- RTP 트레이스에 컴포트 사일런스 vs 실음 프레임 비율을 남겨 트레이스만으로
  끊김을 구분할 수 있게 한다.
- G.722 인코드를 이벤트 루프 밖으로 빼는 것은 측정 후에만.

**수용 기준** 루프 stall이 0.5 s 이상 쌓여도 안내 방송이 앞에서 잘리지 않고,
트레이스에서 사일런스 비율을 읽을 수 있다.

---

## 3. 범위 외 / 장기 후보

### 하지 않기로 한 것

| 항목 | 이유 |
|---|---|
| **SIP over TLS / SRTP** | 전송 계층 재작업. `_open_socket`이 `create_datagram_endpoint` 고정이고 `Via: SIP/2.0/UDP`가 메시지 빌더에 문자열로 박혀 있다. SRTP는 `a=crypto` 협상·키교환·암복호 전부 신규. 대신 외부 노출은 미지원으로 둔다. |
| **동시 다중 통화** | 단일 다이얼로그 전제(`_d_*`)를 유지한다. 통화 중 신규 착신에 `486 Busy Here`를 보내는 현재 동작은 엔드포인트로서 올바르다. |
| **비디오 / 영상 인터폰** | 범위 밖. 필요하면 카메라 엔티티와 조합한다. |

### 장기 후보 (우선순위 낮음, 근거 있음)

- **Session Timer (RFC 4028)**: `Supported: timer` / `Session-Expires` / 422 미지원.
  협상을 안 해서 대부분 Asterisk는 타이머를 끄지만, 강제하는 SBC·사업자 트렁크에서
  장시간 통화가 끊긴다. 장시간 통화 단절 리포트가 남으면 착수한다.
- **digest SHA-256 (RFC 8760) + stale nonce 재시도**: 현재 `algorithm=MD5` 하드코딩이고
  챌린지의 `algorithm`을 읽지 않는다. 해당 레지스트라를 쓰는 사용자 리포트가 나오면 착수.
- **REGISTER Call-ID 유지**: `_do_register`가 갱신마다 Call-ID/tag를 새로 만든다.
  RFC 3261 §10.2는 동일 Call-ID 유지를 SHOULD로 두며, registrar에 따라 Contact가
  중복 쌓일 수 있다. 중복 등록 리포트가 나오면 착수.
- **수신 지터 버퍼 / 시퀀스 재정렬 / PLC**: 현재 시퀀스 번호를 읽지 않고 도착 순서대로
  즉시 디코드한다. G.722는 stateful ADPCM이라 재정렬에 더 취약하다. 수신 깨짐
  리포트가 나오면 착수.
- **connected UDP 소켓 재검토**: `_open_socket`이 `remote_addr`를 지정해 등록 서버 외
  IP에서 온 패킷을 커널이 버린다. 소스 필터링 이득은 있지만, outbound proxy 설정 시
  PBX 직접 INVITE가 사라지고 멀티 인터페이스/SBC 환경에서 조용히 실패한다.
  관련 이슈가 나오면 unconnected 소켓 + 명시적 소스 검증으로 전환.
- **테스트를 `pytest-homeassistant-custom-component`로 이전**: 현재 `tests/conftest.py`가
  HA를 `MagicMock`으로 대체해 `__init__.py`·엔티티·config_flow의 HA 생명주기가
  사실상 미검증이다. HA 재시작, 언로드/재로드, 통화 중 언로드를 테스트할 수 없다.
- **`entry.runtime_data`를 typed dataclass로**: 현재 dict에 클로저를 담아 문자열 키로
  함수를 꺼내 쓴다(`data["trigger_assist_fn"]`). HA 권장 패턴(`ConfigEntry[SipData]`)과 어긋난다.
- **`sip_client/`를 PyPI 라이브러리로 분리**: 이미 HA 의존이 없어 비용이 거의 없다.

---

## 4. 결정이 필요한 열린 질문

작업 중 이 질문에 부딪히면 임의로 정하지 말고 저장소 소유자에게 확인한다.

1. **미디어 타임아웃 / 최대 통화 시간 기본값**: 30초 / 3600초가 적절한가?
   인터폰 용도에서는 더 짧아야 할 수 있다.
2. **PBX 검증 범위**: Asterisk/FreePBX를 컨테이너로 띄워 CI에서 E2E를 돌릴 것인가,
   수동 검증 결과만 문서화할 것인가?
3. **이 문서와 README의 언어**: 계획 문서는 한국어다. 공개 저장소에 두면서 외부
   기여자를 받으려면 영어 병기 또는 전환이 필요할 수 있다.

---

## 5. 참고

이 저장소 이슈: #16(answer/hangup), #17(407), #20(423), #23(DTMF), #28(재설정),
#39(3CX SBC Record-Route), #41·#42·#44(Assist), #45(choppy TTS, 리포터 재확인 대기).
