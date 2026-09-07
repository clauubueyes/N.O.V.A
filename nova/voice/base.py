from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


class VoiceError(Exception):
    """Raised by a voice backend when it cannot fulfill an operation."""


@dataclass
class AudioChunk:
    """Raw PCM16 little-endian mono audio plus its sample rate."""

    sample_rate: int
    data: bytes


class STTProvider(ABC):
    """Speech-to-text backend. Must be safe to construct even when the
    underlying library (or model) is not installed; use available()."""

    name: str = "stt"

    @abstractmethod
    def available(self) -> bool:
        ...

    @abstractmethod
    def transcribe(self, audio: AudioChunk) -> str:
        """Return the transcribed text. Raise VoiceError on failure."""

    def close(self) -> None:
        ...


class TTSProvider(ABC):
    """Text-to-speech backend (plays through the system output devices)."""

    name: str = "tts"

    @abstractmethod
    def available(self) -> bool:
        ...

    @abstractmethod
    def speak(self, text: str) -> None:
        """Speak `text` aloud. Raise VoiceError on failure."""

    def close(self) -> None:
        ...


class AudioSource(ABC):
    """Microphone/input abstraction. start()/stop() bracket capture; each
    read_chunk() blocks up to the block duration and returns one PCM chunk
    (or None when capture is over)."""

    sample_rate: int = 16000

    @abstractmethod
    def available(self) -> bool:
        ...

    def start(self) -> None:
        ...

    @abstractmethod
    def read_chunk(self) -> AudioChunk | None:
        ...

    def stop(self) -> None:
        ...

    def close(self) -> None:
        ...


def _strip_punct(word: str) -> str:
    return "".join(ch for ch in word if ch.isalnum()).lower()


def detect_wake_word(text: str, wake_word: str) -> tuple[bool, str]:
    """Return (matched, remainder). True only when `text` STARTS with the wake
    word (case-insensitive, tolerant to punctuation); the wake word is stripped
    from the returned remainder. When wake_word is falsy, always matches with
    the original text."""
    if not wake_word:
        return True, text.strip()
    tokens = text.split()
    if not tokens:
        return False, text
    if _strip_punct(tokens[0]) == _strip_punct(wake_word.strip()):
        return True, " ".join(tokens[1:]).strip()
    return False, text