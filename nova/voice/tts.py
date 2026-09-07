from __future__ import annotations

from nova.voice.base import TTSProvider, VoiceError


class Pyttsx3TTS(TTSProvider):
    """Offline text-to-speech with operating-system voices via pyttsx3
    (Windows SAPI5 / nsss / eSpeak). Imported lazily so the base install keeps
    working without voice dependencies."""

    name = "pyttsx3"

    def __init__(self, voice: str | None = None, rate: int | None = None) -> None:
        self._voice = voice
        self._rate = rate

    def available(self) -> bool:
        try:
            import pyttsx3  # noqa: F401

            return True
        except ImportError:
            return False

    def speak(self, text: str) -> None:
        if not self.available():
            raise VoiceError("TTS not available (pyttsx3)")
        import pyttsx3

        engine = pyttsx3.init()
        try:
            if self._voice:
                engine.setProperty("voice", self._voice)
            if self._rate:
                engine.setProperty("rate", self._rate)
            engine.say(text)
            engine.runAndWait()
        except Exception as exc:
            raise VoiceError(f"pyttsx3 failed: {exc}") from exc
        finally:
            engine.stop()

    def close(self) -> None:
        pass