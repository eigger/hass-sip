"""Home Assistant Assist Pipeline integration for SIP Client."""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterable, Callable

from homeassistant.components import tts
from homeassistant.components.assist_pipeline import (
    PipelineEvent,
    PipelineEventType,
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

from .const import LOGGER
from .helpers import get_ffmpeg_bin
from .sip_client.audio import AudioSink, AudioSource, FfmpegAudioSource

_SILENT_TURN_ERRORS = frozenset({"stt-no-text-recognized", "wake-word-timeout"})
_MAX_CONSECUTIVE_ERRORS = 3
_ERROR_TURN_BACKOFF_SECONDS = 1.0


class AssistAudioStream(AsyncIterable[bytes]):
    """Async iterable that yields 16kHz PCM audio chunks for Assist STT."""

    def __init__(self) -> None:
        """Initialize the audio stream."""
        self.queue: asyncio.Queue[bytes] = asyncio.Queue()

    def feed_audio(self, pcm_le: bytes, sample_rate: int) -> None:
        """Queue PCM for Assist, upsampling 8 kHz → 16 kHz when needed.

        When the SIP codec is already 16 kHz (G.722), PCM is passed through.
        For 8 kHz (G.711), each 2-byte sample is duplicated.
        """
        if sample_rate == 16000:
            self.queue.put_nowait(pcm_le)
            return
        resampled = bytearray(len(pcm_le) * 2)
        for i in range(0, len(pcm_le), 2):
            sample = pcm_le[i : i + 2]
            resampled[i * 2 : i * 2 + 2] = sample
            resampled[i * 2 + 2 : i * 2 + 4] = sample
        self.queue.put_nowait(bytes(resampled))

    def feed_audio_8khz(self, pcm_8khz: bytes) -> None:
        """Backward-compatible wrapper around :meth:`feed_audio`."""
        self.feed_audio(pcm_8khz, 8000)

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
        sample_rate: int = 8000,
        max_turns: int = 0,
        max_silent_turns: int = 2,
        stop_audio_fn: Callable[[], None] | None = None,
    ) -> None:
        """Initialize the Assist bridge."""
        self.hass = hass
        self.play_source = play_source_fn
        self.on_done = on_done_fn
        self.pipeline_id = pipeline_id
        self.sample_rate = sample_rate
        self.max_turns = max_turns
        self.max_silent_turns = max_silent_turns
        self.stop_audio_fn = stop_audio_fn

        self.audio_stream = AssistAudioStream()
        self.session_task: asyncio.Task | None = None
        self._running = True
        self._listening = False
        self._speaking = False
        self._playback_done = asyncio.Event()
        self._conversation_id: str | None = None
        self._turn_error: str | None = None
        self._continue_conversation = False
        self._background_tasks: set[asyncio.Task] = set()

    def start(self) -> None:
        """Start the Assist session loop in the background."""
        self.session_task = asyncio.create_task(self._run_session())

    def write(self, pcm_le: bytes) -> None:
        """Receive incoming PCM from SIP client and feed it to Assist."""
        if self._listening:
            self.audio_stream.feed_audio(pcm_le, self.sample_rate)

    def on_playback_done(self) -> None:
        """Signal that TX playback has finished (see IvrSession for the same pattern)."""
        self._playback_done.set()

    def close(self) -> None:
        """Stop the bridge and cancel running tasks."""
        self._running = False
        self._listening = False
        self._playback_done.set()
        if self.session_task:
            self.session_task.cancel()
            self.session_task = None
        for task in list(self._background_tasks):
            task.cancel()
        self._background_tasks.clear()

    async def _run_session(self) -> None:
        """Run consecutive Assist pipeline turns until a stop condition."""
        silent_streak = 0
        error_streak = 0
        turns = 0
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
            LOGGER.info(
                "Starting Voice Assist session (pipeline_id=%s)", pipeline.id
            )
            while self._running:
                self.audio_stream = AssistAudioStream()
                self._turn_error = None
                self._continue_conversation = False
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
                        start_stage=PipelineStage.STT,
                        end_stage=PipelineStage.TTS,
                    )
                finally:
                    self._listening = False

                turns += 1
                if self._continue_conversation:
                    silent_streak = 0
                    error_streak = 0
                elif self._turn_error in _SILENT_TURN_ERRORS:
                    silent_streak += 1
                    error_streak = 0
                elif self._turn_error:
                    silent_streak = 0
                    error_streak += 1
                else:
                    silent_streak = 0
                    error_streak = 0

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
            LOGGER.info("Voice Assist pipeline session ended")
            self.on_done()

    async def _wait_playback_done(self) -> None:
        """Wait for TTS playback to finish before starting the next turn."""
        if not self._speaking:
            return
        try:
            async with asyncio.timeout(30):
                await self._playback_done.wait()
        except TimeoutError:
            LOGGER.warning("Assist: playback-done timeout; continuing")
        self._playback_done.clear()
        self._speaking = False
        await asyncio.sleep(0.2)

    def _on_pipeline_event(self, event: PipelineEvent) -> None:
        """Handle events emitted by the Assist pipeline."""
        if not self._running:
            return

        LOGGER.debug("Assist pipeline event: %s", event.type)

        if event.type == PipelineEventType.RUN_START:
            if event.data:
                self._conversation_id = event.data.get("conversation_id")
        elif event.type == PipelineEventType.INTENT_END:
            if event.data:
                intent_output = event.data.get("intent_output") or {}
                self._continue_conversation = bool(
                    intent_output.get("continue_conversation")
                )
        elif event.type == PipelineEventType.TTS_END:
            if (
                event.data
                and (tts_output := event.data.get("tts_output"))
                and (stream := tts.async_get_stream(self.hass, tts_output["token"]))
            ):
                self._speaking = True
                self._playback_done.clear()
                task = asyncio.create_task(self._play_tts_stream(stream))
                self._background_tasks.add(task)
                task.add_done_callback(self._background_tasks.discard)
        elif event.type == PipelineEventType.ERROR:
            if event.data:
                self._turn_error = event.data.get("code")
            LOGGER.error("Assist pipeline error: %s", event.data)

    async def _play_tts_stream(self, stream: tts.ResultStream) -> None:
        """Fetch TTS stream WAV output and play it to the SIP caller."""
        try:
            chunks = []
            async for chunk in stream.async_stream_result():
                chunks.append(chunk)
            wav_data = b"".join(chunks)

            source = FfmpegAudioSource(data=wav_data, ffmpeg_bin=get_ffmpeg_bin(self.hass))
            self.play_source(source)
        except Exception as err:
            LOGGER.exception("Error playing Assist TTS response: %s", err)
