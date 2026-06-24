"""Home Assistant Assist Pipeline integration for SIP Client."""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterable, Callable, Coroutine
from typing import Any

from homeassistant.components import assist_pipeline, tts
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


class AssistAudioStream(AsyncIterable[bytes]):
    """Async iterable that yields 16kHz PCM audio chunks for Assist STT."""

    def __init__(self) -> None:
        """Initialize the audio stream."""
        self.queue: asyncio.Queue[bytes] = asyncio.Queue()

    def feed_audio_8khz(self, pcm_8khz: bytes) -> None:
        """Receive 8kHz s16le mono PCM, resample to 16kHz, and queue.

        Uses simple sample duplication (duplicating each 2-byte sample).
        """
        resampled = bytearray(len(pcm_8khz) * 2)
        for i in range(0, len(pcm_8khz), 2):
            sample = pcm_8khz[i : i + 2]
            resampled[i * 2 : i * 2 + 2] = sample
            resampled[i * 2 + 2 : i * 2 + 4] = sample
        self.queue.put_nowait(bytes(resampled))

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
        pipeline_id: str | None = None,
    ) -> None:
        """Initialize the Assist bridge."""
        self.hass = hass
        self.play_source = play_source_fn
        self.on_done = on_done_fn
        self.pipeline_id = pipeline_id

        self.audio_stream = AssistAudioStream()
        self.pipeline_task: asyncio.Task | None = None
        self.is_active = True
        self._background_tasks: set[asyncio.Task] = set()

    def start(self) -> None:
        """Start the Assist pipeline execution in the background."""
        self.pipeline_task = asyncio.create_task(self._run_pipeline())

    def write(self, pcm_le: bytes) -> None:
        """Receive incoming 8kHz PCM from SIP client and feed it to Assist."""
        if self.is_active:
            self.audio_stream.feed_audio_8khz(pcm_le)

    def close(self) -> None:
        """Stop the bridge and cancel running tasks."""
        self.is_active = False
        if self.pipeline_task:
            self.pipeline_task.cancel()
            self.pipeline_task = None
        for task in list(self._background_tasks):
            task.cancel()
        self._background_tasks.clear()

    async def _run_pipeline(self) -> None:
        """Execute the pipeline stream in Home Assistant."""
        try:
            pref_pipeline = async_get_pipeline(self.hass, self.pipeline_id)
            LOGGER.info(
                "Starting Voice Assist pipeline session (pipeline_id=%s)",
                pref_pipeline.id,
            )

            await async_pipeline_from_audio_stream(
                self.hass,
                context=Context(),
                event_callback=self._on_pipeline_event,
                stt_metadata=SpeechMetadata(
                    language="",  # set by pipeline
                    format=AudioFormats.WAV,
                    codec=AudioCodecs.PCM,
                    bit_rate=AudioBitRates.BITRATE_16,
                    sample_rate=AudioSampleRates.SAMPLERATE_16000,
                    channel=AudioChannels.CHANNEL_MONO,
                ),
                stt_stream=self.audio_stream,
                pipeline_id=pref_pipeline.id,
                start_stage=PipelineStage.STT,
                end_stage=PipelineStage.TTS,
            )
        except asyncio.CancelledError:
            pass
        except Exception as err:
            LOGGER.exception("Error running Assist pipeline bridge: %s", err)
        finally:
            self.is_active = False
            LOGGER.info("Voice Assist pipeline session ended")
            self.on_done()

    def _on_pipeline_event(self, event: PipelineEvent) -> None:
        """Handle events emitted by the Assist pipeline."""
        if not self.is_active:
            return

        LOGGER.debug("Assist pipeline event: %s", event.type)

        if event.type == PipelineEventType.TTS_END:
            if (
                event.data
                and (tts_output := event.data.get("tts_output"))
                and (stream := tts.async_get_stream(self.hass, tts_output["token"]))
            ):
                task = asyncio.create_task(self._play_tts_stream(stream))
                self._background_tasks.add(task)
                task.add_done_callback(self._background_tasks.discard)
        elif event.type == PipelineEventType.ERROR:
            LOGGER.error("Assist pipeline error: %s", event.data)

    async def _play_tts_stream(self, stream: tts.ResultStream) -> None:
        """Fetch TTS stream WAV output and play it to the SIP caller."""
        try:
            chunks = []
            async for chunk in stream.async_stream_result():
                chunks.append(chunk)
            wav_data = b"".join(chunks)

            # Play the TTS bytes over the SIP RTP stream using FfmpegAudioSource
            source = FfmpegAudioSource(data=wav_data, ffmpeg_bin=get_ffmpeg_bin(self.hass))
            self.play_source(source)
        except Exception as err:
            LOGGER.exception("Error playing Assist TTS response: %s", err)
