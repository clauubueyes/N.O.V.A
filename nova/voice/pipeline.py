from __future__ import annotations

import threading
from collections.abc import Callable
from typing import TYPE_CHECKING

from nova.core.logging import get_logger
from nova.voice.base import AudioChunk, AudioSource, STTProvider, TTSProvider, VoiceError
from nova.voice.base import detect_wake_word

if TYPE_CHECKING:
    from nova.core.config import VoiceSettings


class VoiceSession:
    """Local voice loop: capture -> STT -> (wake word) -> chat -> TTS.

    `listen_once(wait_fn)` records audio on a background thread while `wait_fn`
    blocks (e.g. "press ENTER when you are done") and returns the transcript
    (with the wake word stripped) when it matches, "" when a wake word filtered
    it out, or None when no audio was captured.
    """

    def __init__(
        self,
        source: AudioSource,
        stt: STTProvider,
        tts: TTSProvider,
        wake_word: str | None = None,
        block_s: float = 3.0,
    ) -> None:
        self.source = source
        self.stt = stt
        self.tts = tts
        self.wake_word = wake_word
        self.block_s = block_s
        self._logger = get_logger("voice.session")

    def available(self) -> bool:
        return self.source.available() and self.stt.available() and self.tts.available()

    def status(self) -> list[str]:
        """List of missing pieces (empty when fully usable)."""
        missing = []
        if not self.source.available():
            missing.append("mic (sounddevice)")
        if not self.stt.available():
            missing.append("stt (vosk + model)")
        if not self.tts.available():
            missing.append("tts (pyttsx3)")
        return missing

    def listen_once(
        self,
        wait_fn: Callable[[], bool] | None = None,
    ) -> str | None:
        if wait_fn is None:
            wait_fn = self._default_wait
        chunks: list[AudioChunk] = []
        stop_flag = threading.Event()

        def capture() -> None:
            self.source.start()
            try:
                while not stop_flag.is_set():
                    chunk = self.source.read_chunk()
                    if chunk is None:
                        break
                    chunks.append(chunk)
            finally:
                self.source.stop()

        thread = threading.Thread(target=capture, daemon=True)
        thread.start()
        try:
            wait_fn()
        except (EOFError, KeyboardInterrupt):
            pass
        finally:
            stop_flag.set()
            thread.join(timeout=max(0.1, self.block_s + 0.2))

        if not chunks:
            return None
        audio = AudioChunk(
            sample_rate=chunks[0].sample_rate,
            data=b"".join(chunk.data for chunk in chunks),
        )
        try:
            text = self.stt.transcribe(audio)
        except VoiceError as exc:
            self._logger.warning("voice: stt failed: %s", exc)
            return None
        text = text.strip()
        if not text:
            return None
        if self.wake_word:
            matched, rest = detect_wake_word(text, self.wake_word)
            if not matched:
                return ""
            return rest
        return text

    def say(self, text: str) -> bool:
        """Speak `text` aloud. Returns True when spoken, False on failure."""
        try:
            self.tts.speak(text)
            return True
        except VoiceError as exc:
            self._logger.warning("voice: tts failed: %s", exc)
            return False

    def _default_wait(self) -> bool:
        input("[voice] speak now, press ENTER when you finish speaking... ")
        return True

    def close(self) -> None:
        self.source.close()
        self.stt.close()
        self.tts.close()


def _build_source(settings: "VoiceSettings") -> AudioSource:
    # Only the sounddevice-based mic is provided; it is used when available.
    from nova.voice.audiosource import SoundDeviceSource

    return SoundDeviceSource(device=settings.device)


def _build_stt(settings: "VoiceSettings") -> STTProvider:
    from nova.voice.vosk import VoskSTT

    return VoskSTT(model_dir=settings.stt.model_dir, language=settings.stt.language)


def _build_tts(settings: "VoiceSettings") -> TTSProvider:
    from nova.voice.tts import Pyttsx3TTS

    return Pyttsx3TTS(voice=settings.tts.voice, rate=settings.tts.rate)


def build_voice(settings: "VoiceSettings") -> VoiceSession | None:
    """Build the voice session when voice is enabled (off by default). Returns
    None when disabled; the session itself always starts usable==available even
    if the backend libs/models are missing (check available()/status())."""
    if not settings.enabled:
        return None
    return VoiceSession(
        source=_build_source(settings),
        stt=_build_stt(settings),
        tts=_build_tts(settings),
        wake_word=settings.wake_word,
    )