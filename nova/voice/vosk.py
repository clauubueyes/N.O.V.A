from __future__ import annotations

import json
from pathlib import Path

from nova.voice.base import AudioChunk, STTProvider, VoiceError


class VoskSTT(STTProvider):
    """Offline speech-to-text via Vosk (https://alphacephei.com/vosk/).

    The model is downloaded once by the user and referenced by `model_dir`
    (small models are a few tens of MB). The `vosk` package is imported lazily
    so N.O.V.A. keeps working even when voice is not installed.
    """

    name = "vosk"

    def __init__(self, model_dir: str | None = None, language: str = "es") -> None:
        self._model_dir = Path(model_dir).expanduser() if model_dir else None
        self._language = language
        self._model = None  # type: ignore[assignment]

    def available(self) -> bool:
        if self._model is not None:
            return True
        try:
            import vosk  # noqa: F401
        except ImportError:
            return False
        if self._model_dir is None or not self._model_dir.exists():
            return False
        try:
            import vosk

            self._model = vosk.Model(str(self._model_dir))
            return True
        except Exception:
            return False

    def transcribe(self, audio: AudioChunk) -> str:
        if self._model is None and not self.available():
            raise VoiceError(f"STT not available (vosk + model at {self._model_dir})")
        import vosk

        recognizer = vosk.KaldiRecognizer(self._model, audio.sample_rate)
        if audio.data:
            recognizer.AcceptWaveform(audio.data)
        final = recognizer.FinalResult()
        try:
            text = json.loads(final).get("text", "")
        except ValueError:
            raise VoiceError("vosk returned an unexpected result")
        return str(text).strip()

    def close(self) -> None:
        self._model = None