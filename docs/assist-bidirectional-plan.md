# `sip.start_assist` 양방향/연속 대화 구현 계획서

> 상태: 계획 (미구현) · 대상: `main` (v1.6.0) · 배경: GitHub 이슈 제보
> 이 문서만 읽고 바로 작업할 수 있도록, 검증된 사실과 코드 위치를 모두 적어둡니다.

---

## 1. 배경

### 제보 내용

> "start_assist는 한 번만 동작하는 것 같다. 그리고 기대했던 양방향 통화가 아니다.
> 명령 하나 말하면 응답하고 끝. 다음 명령을 하려면 끊고 다시 걸어야 한다.
> **애드온이 명령을 여러 번 받을 수 있어야 한다.**"

### 판정

**제보는 정확하며, 이는 미구현/버그다.** 원래 설계 의도는 통화 중 연속 대화(양방향)였고,
README도 그 의도대로 서술되어 있다(`Bridges the active call directly to Voice Assist`).
문서가 "1회 제한"을 명시한 적은 없으며 — 애초에 턴 모델을 기술한 적이 없다 —
코드가 의도에 도달하지 못한 상태다.

`git log -- custom_components/sip/assist.py` 결과, 초기 릴리스(`122e19a`) 이후
G.722 샘플레이트 대응(`47a9607`)과 TTS 포맷 수정(`b422b19`)만 있었고
파이프라인 실행 구조는 최초 작성 그대로다.

### 근본 원인 (코드 확인 완료)

`custom_components/sip/assist.py`

1. `_run_pipeline()` (L110) 이 `async_pipeline_from_audio_stream()` 을 **딱 한 번만 await** 한다.
   HA 파이프라인은 STT → intent → TTS 한 턴이 끝나면 리턴한다.
2. 리턴 직후 `finally` 블록 (L135) 에서 `self.is_active = False`.
3. 그 결과
   - `write()` (L88) 가 RTP 수신 오디오를 큐에 넣지 않음 → 2번째 발화는 STT에 도달조차 못 함
   - `_on_pipeline_event()` (L142) 도 조기 return
4. 재시작 로직이 없으므로 통화당 명령 1회로 끝난다.

턴이 끝나는 시점은 HA 기본 VAD가 결정한다(무음 0.7초 / 무발화 15초 타임아웃 — §2.2).

---

## 2. 사전 검증된 사실 (재조사 불필요)

아래는 이미 확인했다. **HA 2026.7.2 소스 기준.**

### 2.1 `async_pipeline_from_audio_stream` 시그니처

`homeassistant/components/assist_pipeline/__init__.py:94`

```python
async def async_pipeline_from_audio_stream(
    hass, *, context, event_callback, stt_metadata, stt_stream,
    wake_word_phrase=None, pipeline_id=None, conversation_id=None,
    tts_audio_output=None, wake_word_settings=None, audio_settings=None,
    device_id=None, satellite_id=None,
    start_stage=PipelineStage.STT, end_stage=PipelineStage.TTS,
    conversation_extra_system_prompt=None,
) -> None
```

→ **`conversation_id` 인자가 존재한다.** 멀티턴 문맥 유지에 그대로 쓸 수 있다.

### 2.2 VAD / 턴 종료 동작

- 현재 코드는 `audio_settings`를 넘기지 않으므로 `AudioSettings()` 기본값 적용
  (`pipeline.py:507`): `is_vad_enabled=True`, `silence_seconds=0.7`
- `VoiceCommandSegmenter` (`vad.py:70`): `timeout_seconds=15.0`
- 즉 **발화 후 0.7초 무음이면 턴 종료**, **아예 발화가 없으면 15초 후 타임아웃**
- 무발화 타임아웃 시 `pipeline.py:987` 에서 `code="stt-no-text-recognized"` 로
  `SpeechToTextError` → `PipelineEventType.ERROR` 이벤트 발생

→ **종료 조건 없이 루프를 돌리면 15초마다 영원히 재시작한다. §4.2를 반드시 구현할 것.**

### 2.3 파이프라인 이벤트 데이터

- `RUN_START` 의 `data` 에 `"conversation_id"` 포함 (`pipeline.py:646-657`)
- `TTS_END` 의 `data["tts_output"]["token"]` — 현재 코드가 이미 사용 중 (`assist.py:147`)
- `ERROR` 의 `data` 에 `code`, `message`

### 2.4 SIP 코어 API (`sip_client/sip_client.py`)

| 심볼 | 위치 | 비고 |
|---|---|---|
| `client.set_sink(sink)` | L204 | RX 오디오 싱크 교체 |
| `client.play_source(source)` | L1015 | TX 재생. 내부에서 `_cancel_source()` 선행 |
| `client.stop_audio()` | L1023 | 재생 중단 (barge-in용) |
| `client.media_playing` | L200 | TX 재생 중 여부 (property) |
| `client.codec.sample_rate` | L192 | 8000(G.711) / 16000(G.722) |
| `_run_source()` | L1031 | 재생이 끝나면 `on_playback_done` emit |

> ⚠️ **핵심 함정**: `_run_source()` 는 `except asyncio.CancelledError: raise` 로 빠져나가므로
> **취소(= `stop_audio()`, 또는 새 `play_source()`) 시에는 `on_playback_done` 이 emit 되지 않는다.**
> 재생완료 이벤트만 기다리는 루프는 barge-in에서 데드락된다.

- RTP는 asyncio DatagramProtocol 기반이라 `sink.write()` 는 **이벤트 루프 스레드에서 호출된다.**
  별도 스레드 동기화는 불필요.

### 2.5 기존 배선 (`custom_components/sip/__init__.py`)

| 위치 | 내용 |
|---|---|
| L290-296 | `on_call_ended` → `assist_bridge.close()` 후 `None` (이미 정상) |
| L305-311 | `on_playback_done` → **`ivr_session` 에만 전달. assist_bridge 미전달** |
| L383-397 | `trigger_assist_internal()` — 브리지 생성/시작 |
| L390 | `AssistBridge(...)` 호출 시 **`pipeline_id` 를 넘기지 않음** (인자는 있는데 죽어 있음) |
| L676 / L699 | 녹음은 `set_sink(recorder)` 후 `set_sink(NullSink())` 로 복원. **assist는 복원 안 함** |
| L131 | `SERVICE_GENERIC_SCHEMA = cv.make_entity_service_schema({})` |
| L713-728 | `handle_start_assist` / 서비스 등록 |

### 2.6 IVR 연동 제약

`ivr.py:127`, `ivr.py:209` 가 `await self.trigger_assist()` 를 **무인자로 호출**한다.
→ `trigger_assist_internal()` 에 인자를 추가할 때 **전부 기본값을 가져야 한다.**

`ivr.py:155` 에 `on_playback_done()` 훅이 이미 있다. AssistBridge도 같은 패턴을 따르면 된다.

### 2.7 barge-in용 VAD

- `assist_pipeline` 매니페스트 requirements: `["pymicro-vad==1.0.1", "pyspeex-noise==1.0.2"]`
- 사용법 (`audio_enhancer.py:88`): `MicroVad().Process10ms(audio)` → speech 확률 반환.
  입력은 **10ms 분량 16kHz s16le = 320 bytes**
- 우리 매니페스트는 `after_dependencies: ["assist_pipeline", "stt"]` 이므로
  **`pymicro_vad` 는 이미 설치되어 있다. requirements 추가 불필요.**
  단 방어적으로 `try/except ImportError` → RMS 에너지 폴백을 둘 것.

---

## 3. 목표

| # | 목표 | 단계 |
|---|---|---|
| G1 | 한 통화 안에서 명령을 **여러 번** 주고받는다 | Phase 1 |
| G2 | 후속 발화가 **대화 문맥을 잇는다** ("주방 불 켜줘" → "50%로") | Phase 1 |
| G3 | 무응답/통화종료 시 **깔끔히 끝난다** (무한 루프 없음) | Phase 1 |
| G4 | TTS 응답 **도중에 끼어들 수 있다** (진짜 양방향) | Phase 2 |
| G5 | 자동화에서 **파이프라인/동작을 선택**할 수 있다 | Phase 1 |

---

## 4. Phase 1 — 연속 턴 세션 (half-duplex)

이 단계만으로 제보된 이슈는 해결된다. TTS 재생 중에는 수음하지 않는 half-duplex 방식.

### 4.1 `AssistBridge` 재작성 — `custom_components/sip/assist.py`

#### 상태 모델

```
IDLE → LISTENING → (STT/intent 처리) → SPEAKING(TTS 재생) → LISTENING → ...
                                                          ↘ 종료조건 → CLOSED
```

#### 필수 변경점

**(1) `is_active` 를 두 개로 분리한다.**
현재 하나가 "세션 생존"과 "수음 중" 두 역할을 겸하고 있어서, 턴 종료가 곧 세션 종료가 되는 것이 버그의 본질.

```python
self._running = True      # 세션 생존 (close() 또는 종료조건에서만 False)
self._listening = False   # 현재 턴에서 RX 오디오를 큐에 넣을지
```

`write()` 는 `self._listening` 만 본다.

**(2) 턴마다 `AssistAudioStream` 을 새로 만든다.**
큐를 재사용하면 TTS 재생 중/직후에 쌓인 잔여 오디오가 다음 명령으로 오인된다.
(`AssistAudioStream` 클래스 자체와 `feed_audio()` 리샘플링 로직은 그대로 유지 — 정상 동작 확인함)

**(3) 세션 루프 스켈레톤**

```python
async def _run_session(self) -> None:
    silent_streak = 0
    turns = 0
    try:
        pipeline = async_get_pipeline(self.hass, self.pipeline_id)
        while self._running:
            self.audio_stream = AssistAudioStream()   # 턴마다 새 큐
            self._turn_error = None
            self._listening = True
            try:
                await async_pipeline_from_audio_stream(
                    self.hass,
                    context=Context(),
                    event_callback=self._on_pipeline_event,
                    stt_metadata=...,                        # 기존과 동일
                    stt_stream=self.audio_stream,
                    pipeline_id=pipeline.id,
                    conversation_id=self._conversation_id,   # ← 문맥 유지
                    start_stage=PipelineStage.STT,
                    end_stage=PipelineStage.TTS,
                )
            finally:
                self._listening = False

            turns += 1
            if self._turn_error in ("stt-no-text-recognized", "wake-word-timeout"):
                silent_streak += 1
            else:
                silent_streak = 0

            if not self._running:
                break
            if silent_streak >= self.max_silent_turns:
                LOGGER.info("Assist session ending: %d silent turns", silent_streak)
                break
            if self.max_turns and turns >= self.max_turns:
                break

            await self._wait_playback_done()   # TTS 재생 완료까지 대기
    except asyncio.CancelledError:
        pass
    except Exception as err:
        LOGGER.exception("Assist session error: %s", err)
    finally:
        self._running = False
        self._listening = False
        self.on_done()
```

**(4) 재생 완료 대기** — `asyncio.Event` + **안전 타임아웃**

```python
async def _wait_playback_done(self) -> None:
    if not self._speaking:
        return
    try:
        async with asyncio.timeout(30):        # 이벤트 유실 시 데드락 방지
            await self._playback_done.wait()
    except TimeoutError:
        LOGGER.warning("Assist: playback-done timeout; continuing")
    self._playback_done.clear()
    self._speaking = False
    await asyncio.sleep(0.2)                   # 에코 꼬리 가드
```

- `_on_pipeline_event` 의 `TTS_END` 처리에서 `self._speaking = True`, `self._playback_done.clear()`
- 외부에서 `on_playback_done()` 이 호출되면 `self._playback_done.set()`

**(5) `on_playback_done()` 공개 훅 추가** (`ivr.py:155` 와 동일 패턴)

```python
def on_playback_done(self) -> None:
    self._playback_done.set()
```

> 주의: 이 콜백은 assist 세션 중 자동화가 `sip.play_message` 를 호출한 경우에도 온다.
> "TX 유휴" 신호로 해석하면 되고 실사용상 문제 없음.

**(6) `_on_pipeline_event` 확장**

- `RUN_START` → `self._conversation_id = event.data.get("conversation_id")`
- `INTENT_END` → `continue_conversation` 플래그 저장 (되묻기 턴이면 무음 카운터 리셋에 활용)
- `TTS_END` → 기존 재생 로직 + `_speaking = True`
- `ERROR` → `self._turn_error = event.data.get("code")`
- 조기 return 조건을 `is_active` → `self._running` 으로 교체

**(7) `close()` 정리**

`_running=False`, `_listening=False`, 세션 태스크 + 재생 태스크 취소, `_playback_done.set()`
(대기 중인 루프가 즉시 깨어나 종료되도록)

**(8) 생성자 시그니처**

```python
def __init__(self, hass, play_source_fn, on_done_fn, *,
             pipeline_id=None, sample_rate=8000,
             max_turns=0,            # 0 = 무제한
             max_silent_turns=2,
             barge_in=False,         # Phase 2
             stop_audio_fn=None):    # Phase 2 (client.stop_audio)
```

기존 호출부 호환 유지(전부 기본값).

### 4.2 종료 조건 요약

| 조건 | 동작 |
|---|---|
| 무발화 턴 연속 `max_silent_turns` 회 (기본 2) | 세션 종료 |
| `max_turns` 도달 (기본 0 = 무제한) | 세션 종료 |
| 통화 종료 | `on_call_ended` → `close()` (`__init__.py:294`, 기존 동작) |
| `close()` 호출 | 즉시 종료 |
| `hangup_on_end: true` 옵션 | 세션 종료 시 `client.hangup()` 까지 |

### 4.3 배선 변경 — `custom_components/sip/__init__.py`

**(1) L305 `on_playback_done`** — assist_bridge에도 전달

```python
@callback
def on_playback_done() -> None:
    LOGGER.debug("[%s] Audio playback done", sip_config.username)
    fire_sip_event(EVENT_SIP_PLAYBACK_DONE)
    nonlocal ivr_session, assist_bridge
    if ivr_session is not None:
        ivr_session.on_playback_done()
    if assist_bridge is not None:
        assist_bridge.on_playback_done()
```

> `nonlocal` 선언에 `assist_bridge` 추가하는 것을 잊지 말 것.

**(2) L383 `trigger_assist_internal`** — 옵션 수용 (**전부 기본값 필수**, §2.6)

```python
async def trigger_assist_internal(
    pipeline_id: str | None = None,
    max_turns: int = 0,
    max_silent_turns: int = 2,
    barge_in: bool = False,
    hangup_on_end: bool = False,
) -> None:
```

- `AssistBridge(..., pipeline_id=pipeline_id, ...)` 로 **죽어 있던 인자를 실제로 전달**
- `on_assist_done` 에서
  - `client.set_sink(NullSink())` 로 싱크 복원 (녹음 경로 L699와 동일하게)
  - `hangup_on_end` 이면 `client.hangup()`

**(3)** `NullSink` 가 `__init__.py` 에 import 되어 있는지 확인 (`sip_client.audio`).

### 4.4 서비스 스키마 / 번역 / 문서

**(1)** `__init__.py` L131 인근에 추가:

```python
SERVICE_ASSIST_SCHEMA = cv.make_entity_service_schema(
    {
        vol.Optional("pipeline_id"): cv.string,
        vol.Optional("max_turns"): vol.All(vol.Coerce(int), vol.Range(min=0)),
        vol.Optional("max_silent_turns"): vol.All(vol.Coerce(int), vol.Range(min=1)),
        vol.Optional("barge_in"): cv.boolean,
        vol.Optional("hangup_on_end"): cv.boolean,
    }
)
```

L728 등록부의 `SERVICE_GENERIC_SCHEMA` → `SERVICE_ASSIST_SCHEMA` 로 교체하고,
`handle_start_assist` (L713) 에서 `call.data` 를 그대로 전달.

**(2)** `services.yaml` L190 `start_assist` 에 `fields:` 추가.
`pipeline_id` 는 **`selector: assist_pipeline:` 사용 가능**
(`homeassistant/helpers/selector.py:377` 에 `AssistPipelineSelector` 존재 확인).

**(3)** `strings.json` + `translations/{en,ko,de,ru}.json` **4개 모두** 동기화.
각 파일 L204 인근 `start_assist` 블록에 `fields` 추가.

**(4)** `README.md`
- L91 `sip.start_assist` 절: 연속 대화 동작, 종료 조건, 신규 옵션 표 추가
- L407 Voice Assist 자동화 예제: 연속 명령이 된다는 점 명시
- L18 / L20 기능 목록에 한 줄 반영 검토

---

## 5. Phase 2 — barge-in (진짜 양방향)

Phase 1은 TTS 재생 중 수음을 막으므로 "무전기"에 가깝다. 응답 도중 끼어들 수 있어야 의도한 양방향이 된다.
**별도 PR 권장** (롤백 가능하도록).

### 5.1 재생 중 발화 감지

```python
try:
    from pymicro_vad import MicroVad          # assist_pipeline이 이미 설치 (§2.7)
except ImportError:
    MicroVad = None                            # RMS 에너지 폴백
```

- `Process10ms()` 는 **16kHz 320바이트** 단위 입력. G.711(8kHz) 통화에서는
  `AssistAudioStream.feed_audio()` 와 동일한 업샘플 결과를 먹여야 한다.
  누적 버퍼를 두고 320바이트씩 잘라 공급할 것.
- **오탐 방지: 연속 300ms 이상** speech 확률이 임계값을 넘을 때만 트리거.
  단발 에코로 자기 트리거되면 무한 루프가 된다.

### 5.2 끼어들기 처리

```python
def _on_barge_in(self) -> None:
    self.stop_audio()          # client.stop_audio()
    self._playback_done.set()  # ★ 취소 경로에선 on_playback_done이 안 온다 (§2.4)
```

> ⚠️ **가장 빠지기 쉬운 함정**: `sip_client.py:1031 _run_source()` 는 `CancelledError` 를 re-raise 하므로
> `stop_audio()` 로 중단하면 `on_playback_done` 이 **emit 되지 않는다.**
> 브리지가 직접 이벤트를 set 하지 않으면 세션 루프가 30초 타임아웃까지 멈춘다.

### 5.3 프리롤 링버퍼

끼어든 발화의 첫음절이 잘리지 않도록, 감지 직전 **약 500ms 분량**의 RX 프레임을 링버퍼에 보관했다가
새 턴의 `AssistAudioStream` 에 먼저 밀어넣는다.

### 5.4 에코 / 기본값에 대한 판단

- 원격이 **핸드셋**이면 단말 자체 AEC가 있어 안전
- **스피커폰 / 인터콤**은 AEC가 없어 TTS가 마이크로 되돌아옴 → 자기 트리거 위험
- 우리 쪽에는 AEC가 없다. 구현하려면 TX 레퍼런스 신호 기반 적응 필터가 필요 — **본 계획 범위 밖**

→ **`barge_in` 기본값은 `false` 로 출시**하고, 인터콤 실사용 피드백 후 기본값 전환을 검토한다.

---

## 6. 테스트

`tests/conftest.py` 가 HA 모듈을 mock 하고 `tests/test_pure.py` 의 `_load_component_module()` 이
컴포넌트 모듈을 직접 로드하는 구조라, `assist.py` 상태머신은 HA 없이 순수 테스트가 가능하다.
(기존 스타일: 평범한 `def test_*()` 함수, `py -m pytest tests/test_pure.py`)

| 케이스 | 검증 내용 |
|---|---|
| 연속 턴 | 파이프라인 mock이 리턴 → 재생완료 → 다음 턴이 시작된다 |
| 수음 게이팅 | `_listening=False` 동안 `write()` 가 큐에 넣지 않는다 |
| 큐 격리 | 턴 시작 시 새 `AssistAudioStream` 이 만들어진다 |
| 무응답 종료 | `stt-no-text-recognized` 가 `max_silent_turns` 회 연속 → 세션 종료 + `on_done` 1회 호출 |
| 문맥 유지 | `RUN_START` 의 `conversation_id` 가 다음 턴 호출 인자로 전달된다 |
| 재생 타임아웃 | `on_playback_done` 이 안 와도 30초 후 루프가 진행된다 |
| close 경합 | 재생 대기 중 `close()` → 즉시 종료, 태스크 leak 없음 |
| barge-in (P2) | VAD 트리거 → `stop_audio` 호출 + `_playback_done` set + 프리롤 보존 |

**실기 검증 체크리스트** (자동화 테스트로 못 잡는 것):

1. 통화 → "주방 불 켜줘" → 응답 → **끊지 않고** "꺼줘" 가 동작하는가
2. 응답 직후 TTS 꼬리가 다음 명령으로 오인되지 않는가 (에코)
3. 아무 말 없이 30~40초 → 세션이 조용히 끝나는가 (무한 재시작 없음)
4. 통화 중간 hangup → 태스크/싱크 정리, HA 로그에 예외 없음
5. G.711 / G.722 양쪽에서 동작 (샘플레이트 8k/16k 경로)
6. IVR `assist: true` 경로 (`ivr.py:127`) 가 여전히 동작 (무인자 호출 회귀)

---

## 7. 작업 순서

1. **Phase 1-A/B** — `assist.py` 세션 루프 + 종료 조건
2. **Phase 1-C** — `__init__.py` 배선 (playback_done 전달, pipeline_id 전달, 싱크 복원)
3. 실기 통화 테스트 (§6 체크리스트 1~6)
4. **Phase 1-D** — 서비스 스키마 / services.yaml / strings + 번역 4종 / README
5. **Phase 2** — barge-in (별도 PR)

---

## 8. 변경 파일 요약

| 파일 | 변경 |
|---|---|
| `custom_components/sip/assist.py` | 핵심. 세션 루프 재작성, `on_playback_done` 훅, 상태 분리, (P2) VAD |
| `custom_components/sip/__init__.py` | `on_playback_done` 배선(L305), `trigger_assist_internal` 옵션(L383), `pipeline_id` 전달(L390), 싱크 복원, `SERVICE_ASSIST_SCHEMA`(L131/L713/L728) |
| `custom_components/sip/services.yaml` | `start_assist` fields (L190) |
| `custom_components/sip/strings.json` | `start_assist` fields (L204) |
| `custom_components/sip/translations/{en,ko,de,ru}.json` | 동일 (각 L204) |
| `tests/test_pure.py` | §6 테스트 추가 |
| `README.md` | L18 / L20 / L91 / L407 |
| `custom_components/sip/manifest.json` | **버전 범프만.** requirements 추가 불필요 (§2.7) |

---

## 9. 부수 수정 (같이 처리 권장)

조사 중 발견한 별개 결함:

1. **죽어 있는 `pipeline_id`** — `AssistBridge.__init__` 은 `pipeline_id` 를 받는데(`assist.py:76`)
   `trigger_assist_internal`(`__init__.py:390`)이 넘기지 않아 항상 기본 파이프라인만 사용된다.
   → §4.3-(2) 에서 함께 해결.
2. **싱크 미복원** — `stop_recording` 은 `NullSink()` 로 되돌리는데(`__init__.py:699`)
   assist 종료 시엔 죽은 브리지가 싱크로 남는다. 현재는 `write()` 가 no-op 이라 무해하지만 정리 대상.
   → §4.3-(2) 에서 함께 해결.
