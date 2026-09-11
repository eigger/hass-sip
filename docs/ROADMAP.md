# hass-sip 작업 계획

> 2026-09-11 코드 검토 결과를 기반으로 작성한 실행 계획.
> 작업은 여러 에이전트/세션에 걸쳐 나뉘어 진행될 수 있으므로, 각 작업 카드는
> **사전 조사 없이 바로 착수할 수 있도록** 근거·대상 파일·수용 기준을 모두 포함한다.

---

## 0. 이 문서 사용법

- 작업 단위는 `P{단계}-{번호}` 카드다. 카드 하나가 PR 하나에 대응하는 크기로 잡혀 있다.
- **단계(P0 → P5) 순서를 지킨다.** 순서에는 근거가 있고 §3에 기록해 두었다.
- 카드를 시작하기 전에 §1(현황)과 §2(작업 원칙)를 먼저 읽는다. 특히 §2의
  "건드리지 말 것"은 이미 검증된 자산을 되돌리지 않기 위한 것이다.
- 카드 작업이 끝나면 해당 카드에 `[x]`를 표시하고, 수용 기준으로 추가한 테스트 이름을 기록한다.
- 카드를 진행하다 계획 자체가 틀렸다고 판단되면 코드를 밀어붙이지 말고 이 문서를 고친다.

---

## 1. 현황 요약 (검증된 사실)

측정 시점 2026-09-11. 추정과 확인을 구분해 적었다.

### 1.1 프로젝트 규모

| 항목 | 값 |
|---|---|
| 공개 기간 | 약 3개월 (첫 커밋 2026-06-15) |
| 커밋 / 릴리스 / PR | 57 / 29 / 37 |
| 코드 | `custom_components/sip` 약 4,500줄, 테스트 약 2,700줄 |
| Star / Fork | 11 / 6 |
| 외부 이슈 | 8건 (서로 다른 7명) |
| HA analytics 설치 수 | 집계 없음 (`sip` 도메인이 4,248개 목록에 부재) |

외부 이슈 내역: 3CX SBC Record-Route, 407 proxy auth, 423 Interval Too Brief,
DTMF 인식 실패, choppy live TTS(#45, 열림). **전부 안정성·PBX 호환성 문제이고,
기능 부족 요청이 아니다.** 이 사실이 §3 우선순위의 근거다.

### 1.2 잘 되어 있는 것 (되돌리지 말 것)

- **SIP core와 HA layer 분리 완료.** `custom_components/sip/sip_client/` 9개 모듈에
  `homeassistant` import가 0건이다. 테스트가 HA 없이 코어를 로드한다(`tests/conftest.py`).
  추가 계층 분리 리팩터링은 순손실이다.
- **G.722 clock/sample rate 분리가 정확하다.** `sip_client/codecs.py`에서 RTP clock 8000,
  실 PCM 16000으로 구분한다. bit-exact 참조 벡터 테스트와 성능 예산 테스트가 있다.
  자작 구현이 가장 많이 틀리는 지점이고, Assist에 진짜 16 kHz를 공급하는 유일한 경로다.
- **SIP 라우팅/트랜잭션 처리 상당 부분이 이미 견고하다.** Record-Route wire order 보존,
  strict/loose router 분기, CANCEL과 2xx 경합, forked 2xx의 ACK+BYE 정리,
  401 재인증 후 INVITE CSeq 유지, UDP INVITE 재전송 T1 백오프. 모두 테스트로 잠겨 있다.
- **비밀번호는 기본 로그에 노출되지 않는다.** 원시 SIP 메시지는 opt-in
  `custom_components.sip.sip_client.trace` DEBUG에서만 남고, digest
  `response`/`nonce`/`cnonce`는 마스킹된다 (P1-1).
- **Assist 세션 상태 관리가 견고하다.** `_tts_epoch`로 stale TTS 무효화, tone/tts 대기
  이벤트 분리, silent/error streak 구분. 관련 테스트 40개 이상.

### 1.3 확인된 결함 (실행으로 검증)

실제 `SipClient`에 메시지를 주입해 확인한 결과다.

1. **in-dialog re-INVITE 응답이 잘못된다.** (→ P0-1)
   - 수신 통화 중 re-INVITE(CSeq 2, 새 branch) → 원본 INVITE의 `branch`와 `CSeq: 1`로
     200 OK를 보낸다. 상대는 매칭 실패 후 트랜잭션 타임아웃 → 통화 단절.
   - 발신 통화 중 re-INVITE → **`486 Busy Here`** 를 보낸다. 부작용으로 `last_caller`가
     상대 번호로 덮어써진다.
   - re-INVITE는 예외가 아니라 일상이다: hold/재개, 통화 전환, 코덱 변경, 그리고
     **Asterisk pjsip `direct_media`(FreePBX 내선 기본값 yes)의 미디어 직결 전환**.
   - 테스트 107개 중 re-INVITE 케이스가 0개라서 3개월간 잡히지 않았다.

2. **symmetric RTP latching이 없다.** (→ P0-2) `rtp_session.py:313 _receive`가 송신자
   주소를 버리고, 목적지는 SDP `c=` 라인만 신뢰한다(`rtp_session.py:120 set_remote`).
   상대/PBX가 NAT 뒤에서 사설 IP를 SDP에 넣으면 음성이 허공으로 간다.
   **"연결은 되는데 소리가 안 들린다"의 1순위 원인이고 수정 비용이 가장 낮다.**

3. **RTP 타임아웃과 최대 통화 시간이 없다.** (→ P0-3) 미디어가 완전히 끊겨도 통화가
   유지된다. IVR의 `on_timeout: repeat` 루프나 Assist 세션이 무한 통화로 남을 수 있다.

4. **등록 복구 경로에 테스트가 0개다.** (→ P0-4) `_register_tick`(:395), `_reconnect`(:246),
   3회 무응답 후 소켓 재생성, 423 Min-Expires 상향, 401 재인증이 전부 미검증이다.
   **"PBX/네트워크 재시작 후 자동 복구되는가"가 가장 중요한 질문인데 정확히 여기가 공백이다.**

5. **진단 수단이 없다.** (→ P1) `NullSink.bytes_received`로 수신량을 세는데 어디에도
   노출되지 않는다. diagnostics 플랫폼도, opt-in 트레이스도 없다. 사용자가 "소리가 안
   들려요"라고 하면 tcpdump 외에 방법이 없다.

6. **자원 관리 결함 3건.** (→ P2)
   - `_end_call`(:1076)이 `create_task(_stop_media())`를 던지고 즉시 상태 전이 →
     직후 새 착신 auto-answer가 `_start_media`(:1086)를 만들면 `_media_active` 플래그
     경합. 인터폰처럼 연속 호출이 들어오는 시나리오에서 현실적.
   - `on_call_ended`(`__init__.py:278`)가 recorder를 닫지 않는다. WAV 핸들이 열린 채
     남고 다음 통화가 같은 파일에 이어 쓴다.
   - `set_sink` 슬롯이 하나뿐이라 녹음과 Assist가 서로를 밀어낸다.
   - `WavRecorderSink.write`(`audio.py:110`)가 RTP 콜백에서 블로킹 디스크 쓰기를 한다
     (20 ms 주기). `wave.open`(:105)도 async 컨텍스트에서 직접 호출된다.

7. **Assist 품질 3건.** (→ P3)
   - `_upsample_pcm`(`assist.py:59`)이 샘플 복제(zero-order hold)다. anti-imaging 필터가
     없어 STT 인식률을 깎고, 순수 파이썬 루프를 이벤트 루프에서 20 ms마다 돈다.
     G.722에서는 no-op이므로 영향은 G.711 통화 한정.
   - `_play_tts_stream`(`assist.py:592`)이 청크를 전부 모은 뒤 ffmpeg에 넘긴다
     (`chunks = []` → `b"".join`). 첫 음성까지 지연 = TTS 합성 전체 시간.
   - 턴 사이 `_listening=False` 구간 + `asyncio.sleep(0.2)`(`assist.py:543`) + 톤 대기에서
     발화가 유실된다. preroll 주입은 실패 경로에서만 일어난다.

8. **보안 3건.** (→ P4)
   - **Assist가 빈 `Context()`로 실행된다** (`assist.py:388`, `:473`). `user_id`가 없어
     전화 발신자가 모든 intent를 무제한 권한으로 실행하고 감사 로그에 주체가 없다.
   - `sip.start_assist`에 발신자 제한이 전무하다. auto-answer는 contacts 화이트리스트가
     있는데 Assist는 없다. Caller ID는 스푸핑 가능하므로 화이트리스트만으로도 부족하다.
   - 인증 실패(403) 시 10초 고정 간격 무한 재시도(`sip_client.py:472`) → fail2ban IP 차단.
   - config_flow의 password가 `cv.string`(`config_flow.py:49`)이라 UI에 평문 표시.

### 1.4 경쟁 지형 (포지셔닝 근거)

| 프로젝트 | 방식 | 규모 | 관계 |
|---|---|---|---|
| **`voip`** (HA Core 공식, 2023.5) | HA가 SIP 수신 대기, 전화→Assist | Core 내장 | **직접 경쟁** |
| `sipcore` (TECH7Fox) | 브라우저 WebRTC 소프트폰 | 316★ / 237 설치 | 보완재 |
| `asterisk` (TECH7Fox) | AMI로 Asterisk 제어 | 396 설치 | 보완재 |
| **`hass-sip`** | PBX에 등록되는 서버측 SIP UA | 11★ | — |

HA Core에 이미 공식 `voip` 통합이 있고 codeowner가 HA Voice 총괄이다.
**"전화 → Assist"는 이미 공식 기능이므로 그 자체를 차별점으로 내세울 수 없다.**

그런데 core `voip`의 한계가 정확히 이 프로젝트의 강점이다:
(1) Opus 필수라 사실상 Grandstream ATA 전용, (2) PBX에 REGISTER 불가(수신 대기만),
(3) 발신·IVR·DTMF 메뉴·녹음 없음. 커뮤니티에 "PCMU/PCMA/G.722 지원", "HA가 VoIP 서버에
등록되게 해달라", "Asterisk 지원" 요청이 수년간 쌓여 있다.

---

## 2. 작업 원칙

### 2.1 건드리지 말 것

- `sip_client/` 의 계층 구조. 이미 분리돼 있다. HA 의존을 절대 추가하지 않는다.
- `codecs.py`의 G.722 clock/sample rate 분리와 G.722 참조 벡터 테스트.
- 이미 통과하는 107개 테스트. 기존 테스트를 수정해야 한다면 그건 행동 변경이므로
  PR 설명에 이유를 명시한다.
- 라우팅/트랜잭션 처리(strict/loose router, Record-Route, forked 2xx, CANCEL 경합).
  실제 이슈를 갚아온 코드이므로 "정리"하지 않는다.

### 2.2 지킬 것

- **SIP core 수정은 반드시 `tests/test_pure.py`에 회귀 테스트를 함께 추가한다.**
  이 프로젝트의 결함은 대부분 "테스트가 없던 경로"에서 나왔다.
- SIP core에는 `homeassistant` import 금지. 새 기능이 HA를 필요로 하면 콜백/주입으로 뺀다.
- 이벤트 루프에서 블로킹 I/O 금지(파일, 소켓, subprocess 대기).
- 로그에 크리덴셜, `Authorization` 헤더 값, digest response를 절대 남기지 않는다.
- `ruff check custom_components/` 통과. CI는 Python 3.14, `homeassistant>=2026.8.0`.
- 사용자에게 보이는 동작이 바뀌면 `strings.json` + `translations/{en,ko,de,ru}.json`을 함께 갱신한다.

### 2.3 검증 방법

SIP core는 HA 없이 단독 로드할 수 있다. `tests/test_pure.py` 상단(1~91행)의
`_load_pkg_module` / `_load_component_module` 패턴을 그대로 재사용하면
`SipClient`에 임의의 SIP 메시지를 주입하고 `_send_raw`를 patch해서 송신 메시지를
문자열로 검사할 수 있다. §1.3의 결함 1은 이 방법으로 확인했다.

```
py -m pytest tests/               # 전체
ruff check custom_components/     # 린트
```

---

## 3. 단계 순서의 근거

문서화·데모를 먼저 하자는 안을 검토했으나 **뒤집었다.** 근거는 가설이 아니라 이슈
트래커다: 현재까지 들어온 외부 이슈 전부가 안정성·PBX 호환성이다. 데모로 방문자를
늘려도 `direct_media` re-INVITE에서 통화가 끊기면 사용자가 남지 않는다.

| 단계 | 목표 | 완료 판정 |
|---|---|---|
| **P0** | 통화가 끊기는 결함 제거 | 기본 설정 FreePBX/Asterisk에서 hold·전환·direct media를 거쳐도 통화 유지 |
| **P1** | 원격 진단 가능 | 이슈 리포터가 tcpdump 없이 원인 구간(SIP/RTP/코덱)을 특정할 수 있다 |
| **P2** | 자원 관리·동시성 정리 | 연속 통화·녹음·Assist 동시 사용에서 누수/경합 없음 |
| **P3** | Assist 체감 품질 | 첫 음성까지 지연 단축, 턴 경계 발화 유실 없음 |
| **P4** | 보안 게이트 | 도어락 연결을 권장할 수 있는 인가 모델 존재 |
| **P5** | 호환성 문서 + 포지셔닝 | 검증된 PBX만 "지원"으로 표기, README가 core `voip` 대비 차별점을 말한다 |

단, **P5-1(PBX 호환성 표 + 검증 설정 예제)만은 P0와 병행할 가치가 있다.** 외부 이슈
5건 중 3건이 PBX 설정/변종 문제이고, 문서가 이슈 대응 비용을 직접 줄인다.

---

## 4. 제품 방향 결정사항

계획 수립 시 확정한 내용. 카드 작업 중 이 전제를 뒤집지 않는다.

1. **포지셔닝은 "Home Assistant as a Phone"이 아니라 "기존 PBX에 등록되는 진짜 SIP 내선"이다.**
   전자는 core `voip`이 점유한 문장이고, 검색 사용자를 그쪽으로 보낸다.
   메시지 초안: *Works with your existing PBX. G.711 / G.722 — no Opus, no ATA required.*
2. **차별점은 Assist가 아니라 PBX 호환성 + telephony 기능 깊이(IVR/DTMF/녹음/발신/인터폰)다.**
   core 팀이 따라오기 가장 비싼 영역이기도 하다.
3. **외부(인터넷) 노출은 공식 미지원으로 명시한다.** TLS/SRTP는 전송 계층 재작업이므로
   로드맵에서 내린다(§6 참조).
4. **HA Core 통합 제출은 목표로 두지 않는다.** 도메인 기능 중복 때문에 "`voip`에 흡수"
   방향으로 갈 가능성이 높다. HACS 유지 + 코덱/등록 개선분의 upstream 기여 제안이 현실적이다.
5. **동시 다중 통화는 범위 외다.** 단일 다이얼로그 전제(`_d_*` 단일 상태)를 유지한다.
   통화 중 신규 착신에 `486 Busy Here`를 보내는 현재 동작은 SIP 엔드포인트로서 올바르다.

---

## P0 — 통화가 끊기는 결함 (릴리스 블로커)

### [x] P0-1. in-dialog re-INVITE 정상 응답

**근거** §1.3-1. 확인된 결함. 기본 설정 PBX에서 통화가 끊기는 가장 유력한 경로.

**대상** `custom_components/sip/sip_client/sip_client.py:843 _handle_request` INVITE 분기

**현재 동작**
- 수신 통화 중: `:846` "Retransmitted INVITE" 경로가 re-INVITE를 재전송으로 오인해
  원본 INVITE의 branch/CSeq로 200 OK를 보낸다.
- 발신 통화 중: `not self._outbound` 가드에 걸려 신규 착신 경로로 떨어지고
  `:866`에서 `486 Busy Here`를 보낸다. 추가로 `last_caller`가 오염된다.

**작업**
1. INVITE 수신 시 세 경우를 구분한다.
   - **재전송**: Call-ID 일치 **AND** CSeq가 기존과 동일 → 지금처럼 이전 응답 재생.
   - **re-INVITE**: Call-ID 일치 **AND** CSeq가 증가 → in-dialog 재협상으로 처리.
     발신/수신 여부(`_outbound`)와 무관하게 같은 경로를 탄다.
   - **신규 착신**: Call-ID 불일치 → 기존 로직(통화 중이면 486).
2. re-INVITE 처리:
   - **re-INVITE 메시지 자체**의 Via/branch/CSeq로 `200 OK` + SDP answer를 만든다.
     (`_build_response(m, 200, "OK", True)` — 원본 `_incoming_invite`가 아니라 `m`)
   - SDP를 재적용하고(`_apply_remote_sdp`) 원격 RTP 엔드포인트가 바뀌면
     `rtp.set_remote()`를 갱신한다. **미디어 세션은 재시작하지 않는다**(direct media
     전환 시 통화가 끊기지 않아야 한다).
   - `_d_remote_target`을 새 `Contact`로 갱신한다.
   - `last_caller`를 덮어쓰지 않는다.
   - 상태 전이를 일으키지 않는다(IN_CALL 유지). `on_incoming_call`을 emit하지 않는다.
3. hold 감지: SDP에 `a=sendonly`/`a=inactive` 또는 `c=IN IP4 0.0.0.0`이면 송신을
   중단한다(`rtp.send_silence = False` 수준으로 최소 대응). 재개 시 복구.
4. `UPDATE`가 SDP offer를 담고 있으면 answer를 포함한 200 OK를 보낸다.
   현재는 기타 in-dialog 요청 fallback(`:964`)에서 SDP 없이 200 OK를 보낸다.

**수용 기준** (`tests/test_pure.py`에 추가)
- 수신 통화 중 re-INVITE → 응답의 Via branch와 CSeq가 **re-INVITE의 것**과 일치하고
  SDP answer를 포함한다.
- 발신 통화 중 re-INVITE → 486이 아니라 200 OK + SDP answer. `last_caller` 불변, 상태 IN_CALL 유지.
- 동일 CSeq의 INVITE 재전송 → 기존 동작(이전 응답 재생)이 유지된다.
- re-INVITE의 SDP가 원격 RTP 주소를 바꾸면 `rtp.set_remote`가 새 값으로 호출되고
  `rtp.stop()`이 호출되지 **않는다**.
- hold(`a=sendonly`) → 재개 왕복 후 송신이 복구된다.

**추가된 테스트**
- `test_inbound_reinvite_answers_with_new_transaction`
- `test_outbound_reinvite_is_not_busy_here`
- `test_invite_retransmission_replays_prior_response`
- `test_reinvite_hold_and_resume_toggles_tx`
- `test_update_with_sdp_returns_answer`
- `test_parse_sdp_hold_attributes`

**리스크** 재전송과 re-INVITE의 구분을 CSeq로만 하면 CSeq를 증가시키지 않는 비표준
구현에서 오판할 수 있다. Via branch 비교를 보조 조건으로 둔다.

---

### [x] P0-2. symmetric RTP latching

**근거** §1.3-2. one-way audio 1순위 원인, 수정 비용 최소.

**대상** `sip_client/rtp_session.py` — `_RtpProtocol.datagram_received`(:67),
`_receive`(:313), `set_remote`(:120)

**작업**
1. `datagram_received`가 버리는 송신자 `addr`를 `_receive`까지 전달한다.
2. RTP v2 + 알려진 PT(오디오 또는 telephone-event)인 패킷이 **같은 소스에서
   SSRC가 같고 seq가 증가하며 N개(`_LATCH_LEARN_COUNT=4`) 연속**일 때만 TX
   목적지를 latch한다. 한 패킷으로는 바뀌지 않는다(노출된 RTP 포트 스캐너 방어).
3. latch 후에도 학습을 반복한다. 현재 dest의 패킷은 경쟁 candidate를 리셋하고,
   새 소스가 N연속이면 NAT 리매핑으로 전환한다. (최초 계획의 "통화당 1회"는
   스캐너에 약하고 NAT 리매핑에 과도해서 리뷰 후 폐기.)
4. `RtpSession.start()` / `set_remote()`에서 latch·학습 상태를 초기화한다.
5. SDP 주소와 latch 주소를 둘 다 보관해 P1-3에서 노출할 수 있게 한다.

**수용 기준**
- SDP `c=`가 사설 IP인데 실제 RTP가 다른 주소에서 N연속이면 송신 목적지가 실제 소스로 바뀐다.
- 한 패킷(또는 N-1)으로는 목적지가 바뀌지 않는다.
- latch 후 제3의 주소에서 온 패킷이 N개 미만이면 목적지를 바꾸지 않는다.
- 제3의 주소에서 정상 형식 패킷이 N연속이면 NAT 리매핑으로 다시 전환한다.
- SDP 주소와 실제 소스가 같으면 latch 로그가 남지 않고 동작이 변하지 않는다.
- DTMF telephone-event 패킷으로도 latch가 동작한다.
- RTP version ≠ 2, 미지 PT, 학습 중 SSRC 변경, 비증가 seq는 latch하지 않는다.

**추가된 테스트**
- `test_rtp_latches_tx_dest_to_actual_source`
- `test_rtp_one_packet_does_not_latch`
- `test_rtp_latch_ignores_later_sources`
- `test_rtp_relatch_after_consecutive_new_source`
- `test_rtp_same_source_does_not_latch`
- `test_rtp_dtmf_packet_latches`
- `test_rtp_start_resets_latch`
- `test_rtp_set_remote_resets_latch`
- `test_rtp_protocol_forwards_sender_addr`
- `test_rtp_unknown_pt_does_not_latch`
- `test_rtp_non_v2_does_not_latch`
- `test_rtp_ssrc_change_resets_learn`
- `test_rtp_non_increasing_seq_resets_learn`
- `test_rtp_current_source_resets_candidate`

---

### [x] P0-3. RTP 타임아웃 + 최대 통화 시간

**근거** §1.3-3. 죽은 미디어로 통화가 남고, 무한 통화가 가능하다. 외부 트렁크가
붙으면 과금·DoS 문제가 된다.

**대상** `rtp_session.py`(수신 감시), `sip_client.py`(통화 종료 판단),
`config_flow.py` + `const.py`(설정값)

**작업**
1. `RtpSession`에 마지막 수신 시각을 기록하고, N초(기본 30) 동안 수신이 없으면
   콜백으로 알린다. 콜백 이름은 `on_media_timeout` 수준으로 두고 SIP core가 구독한다.
   - 주의: RTP core는 HA를 모른다. 타임아웃 값은 생성자/속성으로 주입받는다.
2. `SipClient`가 media timeout을 받으면 `hangup()` 후 사유를 담아 `on_call_ended`를 emit한다.
   통화 종료 이벤트에 사유 필드(`reason`: `remote_bye` / `local` / `media_timeout` /
   `max_duration` / `remote_reject` / `remote_cancel` / `ring_timeout`)를 추가한다.
3. 최대 통화 시간(기본 3600초, 0이면 무제한)을 config에 추가하고 IN_CALL 진입 시
   타이머를 걸어 초과 시 종료한다.
4. 기본값은 보수적으로: media timeout 30초, max duration 3600초. 둘 다 설정 가능하게.
   hold(`tx_enabled=False`)와 로컬 `sendonly`(상대 recvonly 페이징)에서는 미디어
   타임아웃을 멈추고, 재개 시 다시 센다. RTP v2 패킷(CN PT 13 포함)은 워치독을 리셋한다.

**수용 기준**
- 미디어 무수신 N초 경과 → BYE 송신 + `on_call_ended(reason="media_timeout")`.
- 통화 중 수신이 계속되면 타임아웃이 발동하지 않는다.
- max duration 초과 → BYE 송신 + `reason="max_duration"`.
- 통화 종료 후 타이머가 정리되어 다음 통화에 누수되지 않는다.
- `reason` 필드가 HA 이벤트(`EVENT_SIP_CALL_ENDED`)에 실려 자동화에서 쓸 수 있다.

**추가된 테스트**
- `test_local_hangup_emits_reason_and_bye`
- `test_remote_bye_emits_reason`
- `test_media_timeout_sends_bye_and_reason`
- `test_max_duration_sends_bye_and_reason`
- `test_call_timers_cleared_on_end`
- `test_media_timeout_ignored_when_not_in_call`
- `test_rtp_media_timeout_fires_without_rx`
- `test_rtp_media_timeout_reset_on_rx`
- `test_rtp_media_timeout_paused_on_hold`
- `test_rtp_media_timeout_zero_disabled`
- `test_rtp_media_timeout_cancelled_on_stop`
- `test_rtp_cn_packet_resets_media_timeout`
- `test_rtp_expect_rx_false_disables_watchdog`
- `test_recvonly_peer_disables_media_watchdog`
- `test_remote_reject_emits_reason`
- `test_remote_cancel_emits_reason`
- `test_ring_timeout_emits_reason`

---

### [x] P0-4. mock registrar 테스트 하네스 + 등록 복구 회귀 테스트

**근거** §1.3-4. "PBX/네트워크 재시작 후 자동 복구되는가"가 가장 중요한 질문인데
정확히 여기가 테스트 공백이다. 투자 효율이 가장 높은 카드다.

**대상** 새 파일 `tests/mock_pbx.py`, 테스트는 `tests/test_pure.py` 또는
새 `tests/test_registration.py`

**작업**
1. asyncio UDP 소켓 기반 가짜 registrar/UAS를 만든다. HA 의존 없음.
   필요한 기능:
   - REGISTER에 401 챌린지 → digest 검증 → 200 OK
   - 응답 지연/무응답/특정 코드(403, 423 + Min-Expires, 407) 강제 모드
   - 소켓을 닫아 "PBX 재시작"을 흉내내는 모드
   - INVITE/200 OK/ACK/BYE/re-INVITE를 보낼 수 있는 최소 UAS
2. 이 하네스로 다음 시나리오를 잠근다.
   - 401 → 인증 후 REGISTER 성공, `on_registered` 1회 emit
   - 407 proxy auth 경로 (이슈 #17 회귀)
   - 423 + Min-Expires → Expires 상향 후 재시도 성공 (이슈 #20 회귀)
   - REGISTER 3회 무응답 → 소켓 재생성(`_reconnect`) 후 재등록 성공
   - 등록 만료 전 갱신이 `register_expiration/2` 시점에 발생
   - 통화 중에는 갱신이 미뤄지고(`_register_tick`의 IN_CALL 분기) 통화 종료 후 재개
   - PBX 재시작(소켓 닫힘 → 재개) 후 자동 복구
   - 403 무한 재시도가 백오프된다 (P2-4 이후 활성화)
3. 타이머 대기를 실시간으로 하지 않는다. `loop.call_later`를 patch하거나
   `register_expiration`을 작게 잡아 테스트 시간을 초 단위로 유지한다.

**수용 기준**
- 위 8개 시나리오가 테스트로 존재하고 통과한다.
- 전체 테스트 실행 시간이 현재 대비 10초 이상 늘지 않는다.
- 하네스가 P0-1(re-INVITE)과 P0-2(latching) 테스트에도 재사용된다.

**추가된 테스트** (`tests/test_registration.py`)
- `test_register_401_then_success_emits_once`
- `test_register_407_proxy_auth`
- `test_register_423_raises_expires`
- `test_register_unanswered_reconnects`
- `test_register_refresh_at_half_expiration`
- `test_register_deferred_during_call`
- `test_register_recovers_after_pbx_restart`
- `test_register_403_retries_without_reconnect` (현재 10초 고정 재시도; 백오프는 P2-4)
- `test_harness_reinvite_answers_with_new_transaction` (P0-1 경로를 소켓 하네스로 재사용)

P0-2 latching은 RTP 단위 테스트로 `test_pure.py`에 남아 있다.

---

## P1 — 진단 가능성

이슈 대응 비용을 직접 줄이는 단계. P0 수정의 효과를 사용자 환경에서 확인할 수단도 된다.

### [x] P1-1. opt-in SIP/RTP 트레이스 (크리덴셜 마스킹)

**근거** §1.3-5. 현재 원시 메시지 로깅이 전혀 없어 비밀번호는 안전하지만
원격 진단이 불가능하다. 필요한 것은 "트레이스 없음"이 아니라 "마스킹된 트레이스"다.

**대상** `sip_client.py` `_send_raw`, `_on_packet`, `rtp_session.py`,
새 파일 `sip_client/trace.py`

**작업**
1. `sip.trace` 전용 로거를 만들고 DEBUG 레벨에서만 송수신 SIP 메시지를 남긴다.
   HA `logger` 설정으로 켤 수 있게 한다(`custom_components.sip.sip_client.trace`).
2. **마스킹 필수**: `Authorization` / `Proxy-Authorization` 헤더의 `response=`, `nonce=`,
   `cnonce=` 값과 비밀번호 유래 값 전부. 마스킹 함수는 단위 테스트로 잠근다.
3. RTP는 패킷 단위 로깅을 하지 않는다. 대신 5초 주기 요약(수신/송신 패킷 수, PT,
   손실 추정, latch 주소)을 DEBUG로 남긴다.
4. README 트러블슈팅에 트레이스 켜는 방법을 넣는다.

**수용 기준**
- 트레이스 활성 시 INVITE/200 OK/REGISTER 왕복이 로그에 남는다.
- `Authorization` 헤더가 포함된 메시지에서 `response=` 값이 마스킹된다(테스트).
- 트레이스 비활성이 기본이고, 비활성 시 문자열 포맷 비용이 발생하지 않는다.

**추가된 테스트** (`tests/test_trace.py`)
- `test_trace_disabled_by_default`
- `test_mask_sip_authorization_response`
- `test_mask_sip_nonce_and_cnonce`
- `test_mask_sip_proxy_authorization`
- `test_mask_sip_unquoted_digest_params`
- `test_mask_sip_leaves_www_authenticate`
- `test_mask_sip_basic_authorization`
- `test_mask_sip_password_param`
- `test_log_sip_skips_masking_when_disabled`
- `test_log_sip_masks_before_debug`
- `test_sip_send_and_recv_traced_when_enabled`
- `test_sip_send_raw_does_not_format_when_disabled`
- `test_rtp_trace_summary_counts_loss_and_latch`
- `test_rtp_trace_summary_silent_when_disabled`
- `test_rtp_trace_timer_cancelled_on_stop`

---

### [x] P1-2. diagnostics 플랫폼

**대상** 새 파일 `custom_components/sip/diagnostics.py`

**작업** `async_get_config_entry_diagnostics` 구현. 포함할 내용:
- 설정값 (server, port, domain, codec 설정, RTP 포트) — **password/auth 정보는 `REDACTED`**
- 현재 SIP 상태, 등록 여부, 마지막 등록 시각, 등록 실패 사유
- 협상된 코덱, telephone-event PT, SDP 원격 주소 vs latch된 실제 주소(P0-2)
- 현재/마지막 통화: 방향, 시작·연결 시각, 종료 사유(P0-3), 송수신 바이트
- 통화 이력(`call_history`)에서 번호를 마스킹한 요약

**수용 기준** `async_redact_data`로 크리덴셜이 제거되고, diagnostics 다운로드 결과만으로
"등록 실패 / 코덱 불일치 / one-way audio" 세 가지를 구분할 수 있다.

**추가된 테스트** (`tests/test_diagnostics.py`)
- `test_mask_number_keeps_last_two_digits`
- `test_collect_diagnostics_redacts_password_and_masks_history`
- `test_diagnostics_snapshot_registration_failure`
- `test_diagnostics_snapshot_codec_mismatch`
- `test_diagnostics_snapshot_matching_codec_is_not_mismatch`
- `test_diagnostics_snapshot_one_way_audio`
- `test_diagnostics_no_media_call_does_not_inherit_previous_rtp_bytes`
- `test_rtp_byte_counters_count_pcm_not_dtmf`
- `test_async_get_config_entry_diagnostics_redacts`

구분 키: `sip.registration.last_failure`, `sip.codec.mismatch`, `sip.rtp.audio_path`
(`no_rx` / `no_tx` / `bidirectional` / `none`).

---

### [x] P1-3. 미디어 상태 노출

**근거** `NullSink.bytes_received`가 이미 있는데 노출되지 않는다.

**작업**
- 센서 추가: 협상 코덱, 마지막 통화 수신/송신 바이트, 마지막 통화 종료 사유.
- `binary_sensor` 또는 media_player 속성에 "양방향 음성 확인됨"(수신 바이트 > 임계값)을 노출.
- 통화 시간(초)을 media_player 속성에 노출.

**수용 기준** 사용자가 통화 후 엔티티만 보고 "음성이 한쪽만 흐른다"를 판단할 수 있다.

**추가된 테스트** (`tests/test_media_status.py`)
- `test_audio_confirmed_requires_both_directions_above_threshold`
- `test_call_duration_live_then_history`
- `test_media_view_one_way_is_not_confirmed`
- `test_media_view_bidirectional_is_confirmed`
- `test_media_view_from_real_sip_client_one_way`

엔티티: `sensor.negotiated_codec`, `sensor.call_audio` (`none`/`no_rx`/`no_tx`/`bidirectional`),
`sensor.last_call_reason`, `binary_sensor.audio_bidirectional`.
media_player 속성: `call_duration`, `audio_path`, `bytes_received`, `bytes_sent`.

---

## P2 — 자원 관리·동시성

### [ ] P2-1. 미디어 생명주기 직렬화

**근거** §1.3-6. `_end_call`(:1076)이 `_stop_media`를 fire-and-forget으로 던지고 즉시
상태를 전이시켜 `_start_media`(:1086)와 경합한다.

**작업** `SipClient`에 `asyncio.Lock`을 두고 `_start_media`/`_stop_media`를 직렬화한다.
`_end_call`이 던지는 태스크가 완료되기 전에 새 통화가 시작되어도 순서가 보장되게 한다.
`_media_active` 플래그로 조기 리턴하는 현재 구조를 락 안으로 옮긴다.

**수용 기준** 통화 종료 직후 즉시 새 착신을 auto-answer하는 시나리오에서
새 통화의 RTP 세션이 정상 시작된다(테스트).

---

### [ ] P2-2. 녹음 정리 + 블로킹 I/O 제거

**작업**
1. `on_call_ended`(`__init__.py:278`)에서 recorder를 닫고 sink를 `NullSink`로 되돌리며
   `data["recorder"]`를 제거한다. `EVENT_SIP_RECORDING_STOPPED`도 발행한다.
2. `WavRecorderSink`(`audio.py:101`)를 비블로킹으로 바꾼다. RTP 콜백에서는 큐에 넣고,
   별도 태스크/executor가 디스크에 쓴다. `wave.open`도 executor로 옮긴다.
3. 파일 경로 검증: HA 설정 디렉터리 밖 임의 경로 쓰기를 막을지 결정하고 문서화한다.

**수용 기준**
- 통화 종료 시 WAV 파일이 닫히고 재생 가능한 상태가 된다.
- 연속 두 통화를 녹음하면 서로 다른 파일에 기록된다(같은 파일에 이어 쓰지 않는다).
- RTP 콜백 경로에 파일 I/O가 없다.

---

### [ ] P2-3. tee sink (녹음 + Assist 동시)

**작업** 여러 `AudioSink`에 PCM을 분배하는 `TeeSink`를 `audio.py`에 추가하고,
`set_sink` 단일 슬롯 대신 sink 등록/해제 구조로 바꾼다. 한 sink의 예외가 다른 sink를
막지 않게 한다.

**수용 기준** Assist 세션 중 `sip.start_recording`을 호출해도 Assist가 계속 듣고,
녹음 파일에도 음성이 기록된다.

---

### [ ] P2-4. 등록 실패 백오프

**근거** §1.3-8. 403 무한 10초 재시도 → fail2ban IP 차단.

**작업** `_handle_register_response`(:472) 실패 분기에 지수 백오프를 넣는다.
인증 실패(401/403 반복, 407)는 별도로 더 공격적으로 백오프하고 상한(예: 30분)을 둔다.
연속 인증 실패 시 HA `repairs` 이슈를 띄워 사용자가 크리덴셜을 고치도록 유도한다.

**수용 기준** 403 연속 수신 시 재시도 간격이 증가하고 상한에서 멈춘다(테스트).
성공 시 간격이 초기화된다.

---

### [ ] P2-5. DND 상태 복원

**작업** `SipDndSwitch`를 `RestoreEntity`로 바꿔 HA 재시작 후 DND 상태를 유지한다.

**수용 기준** DND를 켠 뒤 HA를 재시작해도 켜진 상태로 복원된다.

---

## P3 — Assist 체감 품질 (차별화 영역)

### [ ] P3-1. TTS 스트리밍 재생

**근거** §1.3-7. `_play_tts_stream`(`assist.py:592`)이 청크를 전부 모은 뒤 ffmpeg에 넘겨
첫 음성까지 지연이 TTS 합성 전체 시간이 된다. 전화는 지연 체감이 가장 큰 매체다.

**작업** `FfmpegAudioSource`에 "스트리밍 stdin" 모드를 추가하고, TTS 청크가 도착하는
대로 ffmpeg stdin에 쓴다. `_tts_epoch` 무효화 로직은 유지해야 한다(중간에 barge-in이
들어오면 프로세스를 죽여야 함).

**수용 기준**
- 첫 PCM이 RTP TX에 들어가는 시점이 TTS 스트림 완료를 기다리지 않는다(테스트로 검증).
- barge-in 시 ffmpeg 프로세스가 정리되고 잔여 PCM이 flush된다.
- 기존 Assist 테스트 40여 개가 그대로 통과한다.

---

### [ ] P3-2. 리샘플러 품질 개선

**근거** §1.3-7. `_upsample_pcm`(`assist.py:59`)이 샘플 복제라 4~8 kHz 이미지 성분이
생겨 STT 인식률을 깎고, 순수 파이썬 루프가 이벤트 루프에서 20 ms마다 돈다.

**작업** 8k→16k 업샘플에 anti-imaging 필터를 적용한다. 선택지:
- 짧은 FIR 저역통과 + `array`/`memoryview` 기반 구현 (의존성 없음)
- HA에 이미 있는 의존성 활용 가능 여부 확인 (`audioop`은 Python 3.13에서 제거됨 — 사용 불가)
- 최후 수단: ffmpeg 경유 (프로세스 비용 때문에 비권장)

**수용 기준**
- 1 kHz / 3 kHz 사인파 업샘플 결과에서 이미지 성분이 기존 구현보다 측정 가능하게 감소한다.
- 20 ms 프레임 처리 시간이 예산 내다(`test_g722_perf_under_budget` 패턴 참고).
- G.722(16 kHz) 경로는 여전히 no-op으로 우회된다.

---

### [ ] P3-3. 턴 경계 발화 유실 제거

**근거** §1.3-7. 턴 사이 `_listening=False` 구간 + `asyncio.sleep(0.2)`(`assist.py:543`)
+ 톤 대기에서 발화가 잘린다. preroll은 실패 경로에서만 주입된다.

**작업** 턴 사이에도 RX를 링버퍼에 계속 담고, 다음 턴 시작 시 정상 경로에서도 preroll로
주입한다. 현재 barge-in/톤 타임아웃 전용인 `_ring_buffer` 로직을 상시화하는 방향.
TTS 재생 중 캡처한 오디오를 그대로 넣으면 자기 음성이 들어가므로, 재생 종료 이후
구간만 담도록 경계를 명확히 한다.

**수용 기준** TTS 종료 직후 100 ms 안에 시작된 발화가 다음 턴 STT 스트림 앞부분에 포함된다.

---

### [ ] P3-4. (조사) choppy live TTS #45 원인 규명

**근거** #46에서 송신 페이싱을 절대 데드라인으로 고쳤는데 이슈가 아직 열려 있다.
원인이 다른 층(수신 지터, TTS 공급, 네트워크)일 가능성이 남아 있다.

**작업** 코드를 먼저 바꾸지 말고 재현·측정부터 한다. P1-1의 RTP 요약 로깅과 P1-3의
바이트 카운터를 활용해 송신 간격 분포를 측정하고, 리포터에게 요청할 정보 목록을 정리한다.
P3-1 적용 후 재확인한다.

**수용 기준** 원인 구간이 특정되고, 수정이 필요하면 별도 카드로 분리된다.

---

## P4 — 보안 게이트

### [ ] P4-1. Assist 발신자 인가

**근거** §1.3-8. `sip.start_assist`에 발신자 제한이 전무하다. Caller ID는 스푸핑
가능하므로 화이트리스트만으로도 부족하다.

**작업**
1. `sip.start_assist`에 발신자 허용 목록 옵션을 추가한다(contacts 재사용 또는 명시 목록).
   목록에 없으면 Assist를 시작하지 않고 이벤트로 거절 사실을 알린다.
2. DTMF PIN 게이트를 선택 옵션으로 제공한다. IVR에 이미 `input: pin`이 있으므로
   내부적으로 재사용 가능하다.
3. **문서에 권장 경로를 명시한다**: 도어락/보안 관련 intent를 쓸 경우
   "화이트리스트 + PIN + 전용 pipeline"을 기본으로 안내. 기본값이 없으면 사용자가
   그냥 열어둔다.

**수용 기준** 허용 목록에 없는 발신자가 `start_assist`에 도달하면 세션이 시작되지 않고
거절 이벤트가 발생한다. PIN 옵션 활성 시 PIN 통과 전에는 intent가 실행되지 않는다.

---

### [ ] P4-2. Assist 실행 주체 부여

**근거** §1.3-8. `assist.py:388`, `:473`의 빈 `Context()` 때문에 전화 발신자가 모든
intent를 무제한 권한으로 실행하고 감사 로그에 주체가 없다.

**작업** `Context`에 주체를 부여하는 방식을 조사해 결정한다. 후보:
- config entry에 연결된 전용 HA 사용자를 지정하게 하고 그 `user_id`로 Context를 만든다
- 최소한 `Context(id=...)`에 통화 식별자를 넣어 logbook에서 추적 가능하게 한다

조사 결과를 이 문서에 기록하고, 권한 모델이 불가능하면 "이 조합은 권장하지 않음"을
문서에 명시하는 것으로 대체한다.

**수용 기준** Assist가 실행한 서비스 호출이 logbook에서 특정 통화로 귀속된다.

---

### [ ] P4-3. config_flow password 마스킹

**작업** `config_flow.py:49`의 `cv.string`을 `TextSelector(type=PASSWORD)`로 바꾼다.
reconfigure에서 기존 비밀번호가 평문으로 표시되지 않게 한다.

**수용 기준** 설정/재설정 폼에서 비밀번호가 마스킹된다. 기존 엔트리 편집이 계속 동작한다.

---

## P5 — 호환성 문서 + 포지셔닝

### [ ] P5-1. PBX 호환성 표 + 검증된 설정 예제 (P0와 병행 권장)

**근거** 외부 이슈 5건 중 3건이 PBX 설정/변종 문제. 현재 README는
"FreePBX, Asterisk, or any VoIP provider"라고 쓰는데 3CX에서 이미 이슈가 났다.

**작업**
1. **실제로 테스트한 PBX만 "지원"으로 표기한다.** 나머지는 `커뮤니티 보고` /
   `미검증`으로 구분한다. 최소 표 형태:

   | PBX | 상태 | 검증 버전 | 비고 |
   |---|---|---|---|
   | Asterisk (pjsip) | 지원 / 미검증 | | `direct_media` 설정 명시 |
   | FreePBX | 지원 / 미검증 | | 내선 생성 절차 |
   | 3CX | 커뮤니티 보고 | | 이슈 #39 (SBC Record-Route) |
   | Generic SIP / 사업자 트렁크 | 미검증 | | 이슈 #17 (407) |

2. FreePBX·Asterisk 각각에 대해 **검증된 설정값**을 명시한다:
   `direct_media`, DTMF 모드(RFC 2833 vs INFO), Session Timer, 코덱 순서(G.722/G.711),
   NAT 관련 설정. 이 목록 자체가 안정성 문서 역할을 한다.
3. 30초 Quick Start: 내선 생성 → 통합 추가 → 등록 확인 → 내선으로 전화.

**수용 기준** 신규 사용자가 README만 보고 FreePBX 내선을 만들어 등록까지 도달할 수 있다.
검증되지 않은 PBX가 "지원"으로 표기되어 있지 않다.

---

### [ ] P5-2. README 포지셔닝 재작성

**근거** §1.4, §4-1. 현재 첫 문단이 기술 설명("connect directly to a SIP server or PBX")
이고, core `voip`과의 차이를 말하지 않는다.

**작업**
1. 첫 화면에 core `voip` 대비 차별점을 명시한다:
   **기존 PBX에 REGISTER / G.711·G.722(Opus 불필요) / ATA 불필요 / 발신·IVR·녹음**
2. 대표 시나리오 3개를 결과 중심으로 보여준다: 전화로 HA 제어, 인터폰 자동응답,
   센서 이벤트 → 전화 + TTS.
3. README의 usage 배지가 현재 빈 값으로 렌더링된다(HA analytics에 `sip` 도메인 미집계).
   집계될 때까지 내리거나 조건을 조정한다.
4. **외부 노출 미지원을 명시한다**(§4-3). 보안 섹션에 도어락 연결 시 권장 구성(P4-1)을 링크한다.

---

### [ ] P5-3. 데모 자료

**작업** P0 완료 후에 만든다. GIF/영상 3개: 전화→Assist 왕복, 인터폰 자동응답,
센서 이벤트 알림 전화. HA Community 게시글은 데모가 준비된 다음에 올린다.

**의존성** P0 전체. 안정성이 확보되기 전 유입은 이탈로 이어진다.

---

## 6. 범위 외 / 장기 후보

### 하지 않기로 한 것

| 항목 | 이유 |
|---|---|
| **SIP over TLS / SRTP** | 전송 계층 재작업. `_open_socket`이 `create_datagram_endpoint` 고정이고 `Via: SIP/2.0/UDP`가 메시지 빌더 8곳에 문자열로 박혀 있다. SRTP는 `a=crypto` 협상·키교환·암복호 전부 신규. 대신 "외부 노출 공식 미지원"을 명시한다(§4-3). |
| **동시 다중 통화** | 단일 다이얼로그 전제를 유지한다(§4-5). 통화 중 486 응답은 엔드포인트로서 올바른 동작이다. |
| **비디오 / 영상 인터폰** | 범위 밖. 필요하면 카메라 엔티티와 조합하도록 문서로 안내. |
| **HA Core 통합 제출** | 도메인 중복으로 "`voip`에 흡수" 방향 가능성이 높다(§4-4). |
| **§10의 계층 분리 리팩터링** | 이미 완료되어 있다(§1.2). |

### 장기 후보 (우선순위 낮음, 근거 있음)

- **Session Timer (RFC 4028)**: `Supported: timer` / `Session-Expires` / 422 미지원.
  협상을 안 해서 대부분 Asterisk는 타이머를 끄지만, 강제하는 SBC·사업자 트렁크에서
  장시간 통화가 끊긴다. P0-1 이후에도 장시간 통화 단절 리포트가 남으면 착수한다.
- **digest SHA-256 (RFC 8760) + stale nonce 재시도**: 현재 `algorithm=MD5` 하드코딩이고
  챌린지의 `algorithm`을 읽지 않는다. 해당 레지스트라를 쓰는 사용자 리포트가 나오면 착수.
- **REGISTER Call-ID 유지**: `_do_register`(:369)가 갱신마다 Call-ID/tag를 새로 만든다.
  RFC 3261 §10.2는 동일 Call-ID 유지를 SHOULD로 두며, registrar에 따라 Contact가
  중복 쌓일 수 있다. 중복 등록 리포트가 나오면 착수.
- **수신 지터 버퍼 / 시퀀스 재정렬 / PLC**: 현재 시퀀스 번호를 읽지 않고 도착 순서대로
  즉시 디코드한다. G.722는 stateful ADPCM이라 재정렬에 더 취약하다. P3-4 조사 결과에 따라.
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
  재사용성·신뢰도 확보 수단이고 Core 흡수 논의와 무관하게 손해가 없다.

---

## 7. 결정이 필요한 열린 질문

작업 중 이 질문에 부딪히면 임의로 정하지 말고 저장소 소유자에게 확인한다.

1. **P0-3의 기본값**: media timeout 30초 / max duration 3600초가 적절한가?
   인터폰 용도에서는 더 짧아야 할 수 있다.
2. **P4-2의 권한 모델**: Assist 실행에 전용 HA 사용자를 요구하는 것이 UX상 받아들여지나?
   대안은 "권장하지 않음"을 문서화하는 것뿐이다.
3. **P2-2의 녹음 파일 경로 제한**: HA 설정 디렉터리 밖 쓰기를 막을 것인가?
   막으면 기존 사용자의 자동화가 깨질 수 있다.
4. **P5-1의 PBX 검증 범위**: 실제로 Asterisk/FreePBX를 컨테이너로 띄워 CI에서 E2E를
   돌릴 것인가, 수동 검증 결과만 문서화할 것인가?
5. **이 문서와 README의 언어**: 현재 계획 문서는 한국어다. 공개 저장소에 두면서
   외부 기여자를 받으려면 영어 병기 또는 전환이 필요할 수 있다.

---

## 8. 참고

- 검토 근거가 된 외부 이슈: #16(answer/hangup), #17(407), #20(423), #23(DTMF),
  #28(재설정), #39(3CX SBC Record-Route), #41·#42·#44(Assist), #45(choppy TTS, 열림)
- HA Core `voip` 통합: https://www.home-assistant.io/integrations/voip
- 코덱 지원 요청 스레드: https://community.home-assistant.io/t/support-for-other-codecs-in-voip-integration/568580
- Asterisk 지원 요청 스레드: https://community.home-assistant.io/t/add-asterisk-support-to-the-voip-integration/791436
- 인접 프로젝트: `TECH7Fox/sipcore-hass-integration`(WebRTC 소프트폰),
  `TECH7Fox/asterisk-hass-integration`(AMI 제어) — 경쟁이 아니라 보완재
