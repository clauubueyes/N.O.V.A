from __future__ import annotations

from nova.voice.base import AudioChunk, AudioSource, VoiceError


class SoundDeviceSource(AudioSource):
    """Microphone capture via sounddevice/PortAudio (bundled on Windows wheels).
    Blocks `block_s` seconds per read_chunk() and returns int16 mono PCM."""

    name = "sounddevice"

    def __init__(
        self,
        device: str | None = None,
        sample_rate: int = 16000,
        block_s: float = 3.0,
    ) -> None:
        self._device = device
        self.sample_rate = sample_rate
        self._block_s = block_s

    def available(self) -> bool:
        try:
            import sounddevice  # noqa: F401

            return True
        except ImportError:
            return False

    def read_chunk(self) -> AudioChunk:
        if not self.available():
            raise VoiceError("audio input not available (sounddevice)")
        import sounddevice as sd

        try:
            frames = max(1, int(round(self.sample_rate * self._block_s)))
            data = sd.rec(
                frames,
                samplerate=self.sample_rate,
                channels=1,
                dtype="int16",
                device=self._device,
            )
            sd.wait()
        except Exception as exc:
            raise VoiceError(f"audio capture failed: {exc}") from exc
        return AudioChunk(sample_rate=self.sample_rate, data=data.tobytes())

    def close(self) -> None:
        pass