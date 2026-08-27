"""Home Assistant Assist Pipeline integration for SIP Client."""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterable, Callable
from typing import Literal

from homeassistant.components import tts
from homeassistant.components.assist_pipeline import (
    AudioSettings,
    PipelineEvent,
    PipelineEventType,
    PipelineInput,
    PipelineRun,
    PipelineStage,
    async_get_pipeline,
    async_pipeline_from_audio_stream,
)
from homeassistant.components.stt import (
    AudioBitRates,
    AudioChannels,
    AudioCodecs,
    AudioFormats,
    AudioSampleRates,
    SpeechMetadata,
)
from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers import chat_session

from .const import LOGGER
from .helpers import get_ffmpeg_bin
from .sip_client.audio import AudioSink, AudioSource, FfmpegAudioSource, ToneAudioSource

try:
    from pymicro_vad import MicroVad
except ImportError:
    MicroVad = None  # type: ignore[misc, assignment]

_SILENT_TURN_ERRORS = frozenset({"stt-no-text-recognized", "wake-word-timeout"})
_MAX_CONSECUTIVE_ERRORS = 3
# hangup_on_end callers want the call to end promptly once the caller has
# stopped responding, rather than waiting through the full silent-turn
# budget of a normal session.
_HANGUP_ON_END_MAX_SILENT_TURNS = 1
_ERROR_TURN_BACKOFF_SECONDS = 1.0
_VAD_FRAME_BYTES = 320  # 10 ms @ 16 kHz s16le mono
_VAD_SPEECH_THRESHOLD = 0.5
_VAD_MIN_SPEECH_FRAMES = 30  # 300 ms consecutive speech
_PREROLL_MAX_BYTES = 16000  # 500 ms @ 16 kHz s16le mono
_TX_IDLE_TIMEOUT_SECONDS = 60.0
_TONE_WAIT_TIMEOUT_SECONDS = 3
# Real-time playback of an LLM-generated response can legitimately run past a
# minute; this is a safety net for a lost on_playback_done signal, not a
# normal-case ceiling, so it stays generous.
_TTS_WAIT_TIMEOUT_SECONDS = 300
_TxWaitKind = Literal["tts", "tone"]


def _upsample_pcm(pcm_le: bytes, sample_rate: int) -> bytes:
    """Return 16 kHz s16le mono PCM for Assist STT / VAD."""
    if sample_rate == 16000:
        return pcm_le
    resampled = bytearray(len(pcm_le) * 2)
    for i in range(0, len(pcm_le), 2):
        sample = pcm_le[i : i + 2]
        resampled[i * 2 : i * 2 + 2] = sample
        resampled[i * 2 + 2 : i * 2 + 4] = sample
    return bytes(resampled)


def _stt_text(data: dict | None) -> str:
    """Return the transcript from an STT_END event, or "" when absent."""
    if not data:
        return ""
    return (data.get("stt_output") or {}).get("text", "")


def _intent_speech(intent_output: dict) -> str:
    """Return the spoken response text from an INTENT_END payload."""
    response = intent_output.get("response") or {}
    speech = (response.get("speech") or {}).get("plain") or {}
    return speech.get("speech", "")


class AssistAudioStream(AsyncIterable[bytes]):
    """Async iterable that yields 16kHz PCM audio chunks for Assist STT."""

    def __init__(self) -> None:
        """Initialize the audio stream."""
        self.queue: asyncio.Queue[bytes] = asyncio.Queue()

    def feed_audio(self, pcm_le: bytes, sample_rate: int) -> None:
        """Queue PCM for Assist, upsampling 8 kHz → 16 kHz when needed."""
        self.queue.put_nowait(_upsample_pcm(pcm_le, sample_rate))

    def feed_audio_8khz(self, pcm_8khz: bytes) -> None:
        """Backward-compatible wrapper around :meth:`feed_audio`."""
        self.feed_audio(pcm_8khz, 8000)

    def inject_preroll(self, pcm_16k: bytes) -> None:
        """Pre-fill the queue with buffered RX audio (barge-in preroll)."""
        if not pcm_16k:
            return
        chunk_size = 640  # 20 ms @ 16 kHz
        for offset in range(0, len(pcm_16k), chunk_size):
            self.queue.put_nowait(pcm_16k[offset : offset + chunk_size])

    def __aiter__(self) -> AssistAudioStream:
        """Return the iterator."""
        return self

    async def __anext__(self) -> bytes:
        """Return the next chunk from the queue."""
        return await self.queue.get()


class AssistBridge(AudioSink):
    """Bridges RTP incoming audio to Assist pipeline and plays back responses."""

    def __init__(
        self,
        hass: HomeAssistant,
        play_source_fn: Callable[[AudioSource], None],
        on_done_fn: Callable[[], None],
        *,
        pipeline_id: str | None = None,
        conversation_id: str | None = None,
        initial_prompt: str | None = None,
        system_prompt: str | None = None,
        sample_rate: int = 8000,
        max_turns: int = 0,
        max_silent_turns: int = 2,
        barge_in: bool = False,
        silence_seconds: float | None = None,
        noise_suppression: int = 0,
        turn_tone: bool = False,
        hangup_on_end: bool = False,
        stop_audio_fn: Callable[..., None] | None = None,
        media_playing_fn: Callable[[], bool] | None = None,
    ) -> None:
        """Initialize the Assist bridge."""
        self.hass = hass
        self.play_source = play_source_fn
        self.on_done = on_done_fn
        self.pipeline_id = pipeline_id
        self._conversation_id = conversation_id
        self.initial_prompt = initial_prompt
        self.system_prompt = system_prompt
        self.sample_rate = sample_rate
        self.max_turns = max_turns
        self.max_silent_turns = max_silent_turns
        if hangup_on_end:
            self.max_silent_turns = min(
                self.max_silent_turns, _HANGUP_ON_END_MAX_SILENT_TURNS
            )
        self.barge_in = barge_in
        self.silence_seconds = silence_seconds
        self.noise_suppression = noise_suppression
        self.turn_tone = turn_tone
        self.stop_audio_fn = stop_audio_fn
        self.media_playing_fn = media_playing_fn

        if barge_in and MicroVad is None:
            LOGGER.warning(
                "pymicro_vad unavailable; barge-in disabled for this session"
            )
            self.barge_in = False

        self.audio_stream = AssistAudioStream()
        self.session_task: asyncio.Task | None = None
        self._running = True
        self._listening = False
        self._speaking = False
        self._tx_done = asyncio.Event()
        self._tx_wait: _TxWaitKind | None = None
        self._turn_error: str | None = None
        self._turn_index = 0
        self._continue_conversation = False
        self._background_tasks: set[asyncio.Task] = set()
        self._barge_in_preroll = b""
        self._ring_buffer = bytearray()
        self._vad_pending = bytearray()
        self._vad_speech_frames = 0
        self._post_barge_in_capture = False
        self._tts_epoch = 0
        self._tone_capture = bytearray()
        self._micro_vad = MicroVad() if self.barge_in else None

    def start(self) -> None:
        """Start the Assist session loop in the background."""
        self.session_task = asyncio.create_task(self._run_session())

    def write(self, pcm_le: bytes) -> None:
        """Receive incoming PCM from SIP client and feed it to Assist."""
        if self._listening:
            self.audio_stream.feed_audio(pcm_le, self.sample_rate)
        elif self._tx_wait == "tone":
            self._append_tone_capture(_upsample_pcm(pcm_le, self.sample_rate))
        elif self._post_barge_in_capture:
            self._append_rx_to_ring(_upsample_pcm(pcm_le, self.sample_rate))
        elif self.barge_in and self._speaking:
            self._monitor_barge_in(pcm_le)

    def on_playback_done(self) -> None:
        """Signal that TX playback has finished (see IvrSession for the same pattern).

        Only the waiter registered in ``_tx_wait`` is released so turn-start tone
        completion cannot unblock a TTS wait (and vice versa).
        """
        if self._tx_wait is not None:
            self._tx_done.set()

    def close(self) -> None:
        """Stop the bridge and cancel running tasks."""
        self._running = False
        self._listening = False
        self._speaking = False
        self._post_barge_in_capture = False
        self._cancel_inflight_tts()
        self._tx_wait = None
        self._tx_done.set()
        if self.session_task:
            self.session_task.cancel()
            self.session_task = None
        for task in list(self._background_tasks):
            task.cancel()
        self._background_tasks.clear()

    def _append_rx_to_ring(self, pcm_16k: bytes) -> None:
        self._ring_buffer.extend(pcm_16k)
        if len(self._ring_buffer) > _PREROLL_MAX_BYTES:
            del self._ring_buffer[: len(self._ring_buffer) - _PREROLL_MAX_BYTES]

    def _append_tone_capture(self, pcm_16k: bytes) -> None:
        self._tone_capture.extend(pcm_16k)
        if len(self._tone_capture) > _PREROLL_MAX_BYTES:
            del self._tone_capture[: len(self._tone_capture) - _PREROLL_MAX_BYTES]

    def _cancel_inflight_tts(self, *, stop_audio: bool = False) -> None:
        """Drop in-flight TTS fetch/play tasks (barge-in, timeout, close)."""
        self._tts_epoch += 1
        for task in list(self._background_tasks):
            task.cancel()
        self._background_tasks.clear()
        if stop_audio and self.stop_audio_fn:
            self.stop_audio_fn(flush=True)

    async def _wait_for_tx_idle(self) -> None:
        """Wait until the SIP client is no longer transmitting audio.

        ``play_source()`` cancels any in-flight TX, so callers must not start
        a new source (turn tone, TTS, etc.) while the previous response is
        still draining through RTP.
        """
        if self.media_playing_fn is None:
            return
        try:
            async with asyncio.timeout(_TX_IDLE_TIMEOUT_SECONDS):
                while self._running and self.media_playing_fn():
                    await asyncio.sleep(0.02)
        except TimeoutError:
            LOGGER.warning(
                "Assist: TX idle wait timeout after %.0fs; continuing",
                _TX_IDLE_TIMEOUT_SECONDS,
            )

    def _monitor_barge_in(self, pcm_le: bytes) -> None:
        """Detect caller speech during TTS playback and trigger barge-in."""
        pcm_16k = _upsample_pcm(pcm_le, self.sample_rate)
        self._append_rx_to_ring(pcm_16k)

        self._vad_pending.extend(pcm_16k)
        while len(self._vad_pending) >= _VAD_FRAME_BYTES:
            frame = bytes(self._vad_pending[:_VAD_FRAME_BYTES])
            del self._vad_pending[:_VAD_FRAME_BYTES]
            if self._micro_vad.Process10ms(frame) >= _VAD_SPEECH_THRESHOLD:
                self._vad_speech_frames += 1
                if self._vad_speech_frames >= _VAD_MIN_SPEECH_FRAMES:
                    self._on_barge_in()
                    return
            else:
                self._vad_speech_frames = 0

    def _on_barge_in(self) -> None:
        """Interrupt TTS playback and preserve preroll for the next STT turn."""
        LOGGER.info("Assist barge-in detected")
        self._cancel_inflight_tts(stop_audio=True)
        self._barge_in_preroll = bytes(self._ring_buffer)
        self._vad_pending.clear()
        self._vad_speech_frames = 0
        self._ring_buffer.clear()
        self._post_barge_in_capture = True
        self._tx_done.set()

    def _build_audio_settings(self) -> AudioSettings | None:
        """Return pipeline audio settings, or None to keep Assist defaults.

        The Assist defaults (0.7 s of silence ends a command) are tuned for
        near-field microphones. Telephone lines carry line noise and codec
        artefacts that the VAD readily mistakes for speech, so callers can
        lengthen the silence window and enable noise suppression per call.
        """
        if self.silence_seconds is None and not self.noise_suppression:
            return None
        kwargs: dict[str, float | int] = {}
        if self.silence_seconds is not None:
            kwargs["silence_seconds"] = self.silence_seconds
        if self.noise_suppression:
            kwargs["noise_suppression_level"] = self.noise_suppression
        return AudioSettings(**kwargs)

    def _reset_barge_in_state(self) -> None:
        self._ring_buffer.clear()
        self._vad_pending.clear()
        self._vad_speech_frames = 0

    async def _run_session(self) -> None:
        """Run consecutive Assist pipeline turns until a stop condition."""
        silent_streak = 0
        error_streak = 0
        turns = 0
        silent_turns = 0
        error_turns = 0
        try:
            pipeline = async_get_pipeline(self.hass, self.pipeline_id)
            stt_metadata = SpeechMetadata(
                language="",  # set by pipeline
                format=AudioFormats.WAV,
                codec=AudioCodecs.PCM,
                bit_rate=AudioBitRates.BITRATE_16,
                sample_rate=AudioSampleRates.SAMPLERATE_16000,
                channel=AudioChannels.CHANNEL_MONO,
            )
            audio_settings = self._build_audio_settings()
            LOGGER.info(
                "Starting Voice Assist session (pipeline_id=%s, sample_rate=%d, "
                "barge_in=%s, silence_seconds=%s, noise_suppression=%d, "
                "turn_tone=%s, initial_prompt=%s, system_prompt=%s)",
                pipeline.id,
                self.sample_rate,
                self.barge_in,
                self.silence_seconds if self.silence_seconds is not None else "default",
                self.noise_suppression,
                self.turn_tone,
                bool(self.initial_prompt),
                bool(self.system_prompt),
            )
            if self.initial_prompt:
                self._turn_error = None
                self._continue_conversation = False
                self._turn_index = 0
                await self._run_initial_prompt(pipeline)
                await self._wait_playback_done()
                if not self._running:
                    return

            while self._running:
                self.audio_stream = AssistAudioStream()
                preroll = self._barge_in_preroll
                self._barge_in_preroll = b""
                if self._post_barge_in_capture:
                    if self._ring_buffer:
                        preroll = preroll + bytes(self._ring_buffer)
                    self._post_barge_in_capture = False
                if preroll:
                    self.audio_stream.inject_preroll(preroll)
                self._turn_error = None
                self._continue_conversation = False
                self._turn_index = turns + 1
                self._reset_barge_in_state()
                tone_preroll = b""
                if not preroll:
                    tone_preroll = await self._play_turn_tone()
                    if not self._running:
                        break
                if tone_preroll:
                    self.audio_stream.inject_preroll(tone_preroll)
                LOGGER.debug(
                    "Assist turn %d listening (conversation_id=%s, preroll=%d B)",
                    self._turn_index,
                    self._conversation_id,
                    len(preroll),
                )
                self._listening = True
                try:
                    await async_pipeline_from_audio_stream(
                        self.hass,
                        context=Context(),
                        event_callback=self._on_pipeline_event,
                        stt_metadata=stt_metadata,
                        stt_stream=self.audio_stream,
                        pipeline_id=pipeline.id,
                        conversation_id=self._conversation_id,
                        audio_settings=audio_settings,
                        start_stage=PipelineStage.STT,
                        end_stage=PipelineStage.TTS,
                        conversation_extra_system_prompt=self.system_prompt,
                    )
                finally:
                    self._listening = False

                turns += 1
                if self._continue_conversation:
                    silent_streak = 0
                    error_streak = 0
                elif self._turn_error in _SILENT_TURN_ERRORS:
                    silent_streak += 1
                    silent_turns += 1
                    error_streak = 0
                elif self._turn_error:
                    silent_streak = 0
                    error_streak += 1
                    error_turns += 1
                else:
                    silent_streak = 0
                    error_streak = 0
                LOGGER.debug(
                    "Assist turn %d ended (error=%s, continue=%s, "
                    "silent_streak=%d, error_streak=%d)",
                    turns,
                    self._turn_error,
                    self._continue_conversation,
                    silent_streak,
                    error_streak,
                )

                await self._wait_playback_done()

                if not self._running:
                    break
                if silent_streak >= self.max_silent_turns:
                    LOGGER.info(
                        "Assist session ending after %d silent turns", silent_streak
                    )
                    break
                if self.max_turns and turns >= self.max_turns:
                    LOGGER.info("Assist session ending after %d turns", turns)
                    break
                if error_streak >= _MAX_CONSECUTIVE_ERRORS:
                    LOGGER.info(
                        "Assist session ending after %d consecutive pipeline errors",
                        error_streak,
                    )
                    break

                if self._turn_error and self._turn_error not in _SILENT_TURN_ERRORS:
                    await asyncio.sleep(_ERROR_TURN_BACKOFF_SECONDS)
        except asyncio.CancelledError:
            pass
        except Exception as err:
            LOGGER.exception("Assist session error: %s", err)
        finally:
            self._running = False
            self._listening = False
            self._speaking = False
            self._post_barge_in_capture = False
            LOGGER.info(
                "Voice Assist session ended: %d turns (%d silent, %d errored)",
                turns,
                silent_turns,
                error_turns,
            )
            self.on_done()

    async def _run_initial_prompt(self, pipeline) -> None:
        """Run an opening text turn through intent and TTS."""
        with chat_session.async_get_chat_session(
            self.hass, self._conversation_id
        ) as session:
            pipeline_input = PipelineInput(
                run=PipelineRun(
                    self.hass,
                    context=Context(),
                    pipeline=pipeline,
                    start_stage=PipelineStage.INTENT,
                    end_stage=PipelineStage.TTS,
                    event_callback=self._on_pipeline_event,
                ),
                session=session,
                intent_input=self.initial_prompt,
                conversation_extra_system_prompt=self.system_prompt,
            )
            await pipeline_input.validate()
            await pipeline_input.execute()

    async def _play_turn_tone(self) -> bytes:
        """Play the turn-start beep and wait until it finishes.

        Failures and timeouts must not block the listening turn. The wait uses
        a dedicated event so tone completion cannot unblock TTS playback wait.
        On timeout, any caller audio captured during the wait is returned as
        preroll so speech is not lost on failure paths.
        """
        if not self.turn_tone or self.play_source is None:
            return b""
        self._tx_wait = "tone"
        self._tx_done.clear()
        self._tone_capture = bytearray()
        timed_out = False
        try:
            await self._wait_for_tx_idle()
            if not self._running:
                return b""
            self.play_source(ToneAudioSource())
            try:
                async with asyncio.timeout(_TONE_WAIT_TIMEOUT_SECONDS):
                    await self._tx_done.wait()
            except TimeoutError:
                timed_out = True
                LOGGER.debug("Assist: turn tone playback-done timeout")
                if self.stop_audio_fn:
                    self.stop_audio_fn(flush=True)
        except Exception:
            LOGGER.exception("Assist: failed to play turn tone")
            timed_out = True
        finally:
            self._tx_wait = None
        if timed_out and self._tone_capture:
            return bytes(self._tone_capture)
        return b""

    async def _wait_playback_done(self) -> None:
        """Wait for TTS playback to finish before starting the next turn."""
        if not self._speaking:
            return
        try:
            async with asyncio.timeout(_TTS_WAIT_TIMEOUT_SECONDS):
                await self._tx_done.wait()
        except TimeoutError:
            LOGGER.warning("Assist: playback-done timeout; continuing")
            # Only cancel/stop when a TTS fetch task is still in flight. Once
            # play_source() has been scheduled the background task exits while
            # RTP keeps playing — flushing there would cut long responses.
            if self._background_tasks:
                self._cancel_inflight_tts(stop_audio=True)
        await self._wait_for_tx_idle()
        ended_by_barge_in = self._post_barge_in_capture
        self._tx_done.clear()
        self._tx_wait = None
        self._speaking = False
        if not ended_by_barge_in:
            self._reset_barge_in_state()
            await asyncio.sleep(0.2)

    def _on_pipeline_event(self, event: PipelineEvent) -> None:
        """Handle events emitted by the Assist pipeline."""
        if not self._running:
            return

        LOGGER.debug("Assist pipeline event: %s", event.type)

        if event.type == PipelineEventType.RUN_START:
            if event.data:
                self._conversation_id = event.data.get("conversation_id")
        elif event.type == PipelineEventType.STT_END:
            LOGGER.debug(
                "Assist turn %d recognized: %r",
                self._turn_index,
                _stt_text(event.data),
            )
        elif event.type == PipelineEventType.INTENT_END:
            if event.data:
                intent_output = event.data.get("intent_output") or {}
                self._continue_conversation = bool(
                    intent_output.get("continue_conversation")
                )
                LOGGER.debug(
                    "Assist turn %d response: %r (continue=%s)",
                    self._turn_index,
                    _intent_speech(intent_output),
                    self._continue_conversation,
                )
        elif event.type == PipelineEventType.TTS_END:
            if (
                event.data
                and (tts_output := event.data.get("tts_output"))
                and (stream := tts.async_get_stream(self.hass, tts_output["token"]))
            ):
                self._speaking = True
                self._tx_done.clear()
                self._reset_barge_in_state()
                self._tts_epoch += 1
                epoch = self._tts_epoch
                task = asyncio.create_task(self._play_tts_stream(stream, epoch))
                self._background_tasks.add(task)
                task.add_done_callback(self._background_tasks.discard)
        elif event.type == PipelineEventType.ERROR:
            if event.data:
                self._turn_error = event.data.get("code")
            LOGGER.error("Assist pipeline error: %s", event.data)

    async def _play_tts_stream(self, stream: tts.ResultStream, epoch: int) -> None:
        """Fetch TTS stream WAV output and play it to the SIP caller."""
        try:
            chunks = []
            async for chunk in stream.async_stream_result():
                if epoch != self._tts_epoch:
                    return
                chunks.append(chunk)
            if epoch != self._tts_epoch:
                return

            wav_data = b"".join(chunks)
            source = FfmpegAudioSource(data=wav_data, ffmpeg_bin=get_ffmpeg_bin(self.hass))
            await self._wait_for_tx_idle()
            if epoch != self._tts_epoch:
                return
            self.play_source(source)
            self._tx_wait = "tts"
        except Exception as err:
            LOGGER.exception("Error playing Assist TTS response: %s", err)
            if epoch == self._tts_epoch:
                self._tx_done.set()
