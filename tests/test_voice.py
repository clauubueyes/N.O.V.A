from __future__ import annotations

import sys

from nova.core.config import VoiceSettings, load_settings
from nova.voice import (
    AudioChunk,
    Pyttsx3TTS,
    VoiceSession,
    VoskSTT,
    build_voice,
    detect_wake_word,
)


class FakeAudioSource:
    def __init__(self, chunks: list[AudioChunk] | None = None) -> None:
        self.chunks = list(chunks or [])
        self._started = False
        self._stopped = False
        self.sample_rate = 16000

    def available(self) -> bool:
        return True

    def start(self) -> None:
        self._started = True

    def read_chunk(self) -> AudioChunk | None:
        return self.chunks.pop(0) if self.chunks else None

    def stop(self) -> None:
        self._stopped = True

    def close(self) -> None:
        pass


class FakeSTT:
    def __init__(self, text: str = "hola mundo") -> None:
        self.text = text
        self.transcribed: list[AudioChunk] = []
        self.fail: Exception | None = None

    def available(self) -> bool:
        return True

    def transcribe(self, audio: AudioChunk) -> str:
        self.transcribed.append(audio)
        if self.fail is not None:
            raise self.fail
        return self.text

    def close(self) -> None:
        pass


class FakeTTS:
    def __init__(self) -> None:
        self.spoken: list[str] = []

    def available(self) -> bool:
        return True

    def speak(self, text: str) -> None:
        self.spoken.append(text)

    def close(self) -> None:
        pass


def _pcm(*values: int, rate: int = 16000) -> AudioChunk:
    return AudioChunk(sample_rate=rate, data=bytes(values))


def test_config_defaults() -> None:
    s = VoiceSettings()
    assert s.enabled is False
    assert s.stt.backend == "vosk"
    assert s.stt.model_dir is None
    assert s.tts.backend == "pyttsx3"
    assert s.tts.rate == 180
    assert s.wake_word is None
    assert s.device is None


def test_nested_config_file(tmp_path, monkeypatch) -> None:
    for var in list(__import__("os").environ):
        if var.startswith("NOVA_"):
            monkeypatch.delenv(var, raising=False)
    config = tmp_path / "config.yaml"
    config.write_text(
        "voice:\n  enabled: true\n  wake_word: nova\n"
        "  stt:\n    backend: vosk\n    model_dir: C:\\models\\vosk\n"
        "  tts:\n    rate: 200\n",
        encoding="utf-8",
    )
    s = load_settings(path=str(config))
    assert s.voice.enabled is True
    assert s.voice.wake_word == "nova"
    assert s.voice.stt.model_dir == "C:\\models\\vosk"
    assert s.voice.tts.rate == 200


def test_env_override_nested(tmp_path, monkeypatch) -> None:
    for var in list(__import__("os").environ):
        if var.startswith("NOVA_"):
            monkeypatch.delenv(var, raising=False)
    config = tmp_path / "config.yaml"
    config.write_text("voice:\n  enabled: false\n", encoding="utf-8")
    monkeypatch.setenv("NOVA_VOICE_ENABLED", "true")
    monkeypatch.setenv("NOVA_VOICE_WAKE_WORD", "hey nova")
    monkeypatch.setenv("NOVA_VOICE_STT_BACKEND", "vosk")
    s = load_settings(path=str(config))
    assert s.voice.enabled is True
    assert s.voice.wake_word == "hey nova"
    assert s.voice.stt.backend == "vosk"


def test_build_voice_disabled() -> None:
    assert build_voice(VoiceSettings()) is None


def test_build_voice_enabled_returns_session() -> None:
    s = VoiceSettings(enabled=True)
    session = build_voice(s)
    assert isinstance(session, VoiceSession)


def test_detect_wake_word_matcher() -> None:
    assert detect_wake_word("hola mundo", "") == (True, "hola mundo")
    assert detect_wake_word("Nova dime la hora", "nova") == (True, "dime la hora")
    assert detect_wake_word("nova, dime la hora", "nova") == (True, "dime la hora")
    assert detect_wake_word("nova", "nova") == (True, "")
    assert detect_wake_word("hola", "nova") == (False, "hola")
    assert detect_wake_word("", "nova") == (False, "")


def test_listen_returns_transcript() -> None:
    source = FakeAudioSource([_pcm(1, 2, 3)])
    stt = FakeSTT("hola mundo")
    tts = FakeTTS()
    session = VoiceSession(source, stt, tts)
    result = session.listen_once(wait_fn=lambda: True)
    assert result == "hola mundo"
    assert len(stt.transcribed) == 1
    assert stt.transcribed[0].sample_rate == 16000
    assert source._stopped is True


def test_listen_no_audio_returns_none() -> None:
    source = FakeAudioSource([])
    stt = FakeSTT()
    session = VoiceSession(source, stt, FakeTTS())
    result = session.listen_once(wait_fn=lambda: True)
    assert result is None
    assert stt.transcribed == []


def test_wake_word_filters() -> None:
    source = FakeAudioSource([_pcm(1)])
    stt = FakeSTT("hola mundo")
    session = VoiceSession(source, stt, FakeTTS(), wake_word="nova")
    assert session.listen_once(wait_fn=lambda: True) == ""


def test_wake_word_strips() -> None:
    source = FakeAudioSource([_pcm(1)])
    stt = FakeSTT("nova hola mundo")
    session = VoiceSession(source, stt, FakeTTS(), wake_word="nova")
    assert session.listen_once(wait_fn=lambda: True) == "hola mundo"


def test_stt_error_returns_none() -> None:
    from nova.voice import VoiceError

    source = FakeAudioSource([_pcm(1)])
    stt = FakeSTT()
    stt.fail = VoiceError("broken")
    session = VoiceSession(source, stt, FakeTTS())
    assert session.listen_once(wait_fn=lambda: True) is None


def test_empty_transcript_returns_none() -> None:
    source = FakeAudioSource([_pcm(1)])
    session = VoiceSession(source, FakeSTT("   "), FakeTTS())
    assert session.listen_once(wait_fn=lambda: True) is None


def test_say_and_available() -> None:
    source = FakeAudioSource()
    stt = FakeSTT()
    tts = FakeTTS()
    session = VoiceSession(source, stt, tts)
    assert session.available() is True
    assert session.say("hola") is True
    assert tts.spoken == ["hola"]
    assert session.status() == []


def test_concat_multiple_chunks() -> None:
    source = FakeAudioSource([_pcm(1), _pcm(2, 3)])
    stt = FakeSTT("junta")
    session = VoiceSession(source, stt, FakeTTS())
    assert session.listen_once(wait_fn=lambda: True) == "junta"
    assert stt.transcribed[0].data == bytes([1, 2, 3])


def test_vosk_available_without_package(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "vosk", None)
    assert VoskSTT(model_dir=None).available() is False
    assert VoskSTT(model_dir="C:\\missing\\model").available() is False


def test_vosk_can_signal_unavailable() -> None:
    from nova.voice import VoiceError

    stt = VoskSTT(model_dir=None)
    stt._model = None
    try:
        stt.transcribe(_pcm())
    except VoiceError as exc:
        assert "not available" in str(exc)
    else:
        raise AssertionError("expected VoiceError")


def test_pyttsx3_available_depends_on_import(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "pyttsx3", None)
    assert Pyttsx3TTS().available() is False


def test_missing_status_reports_pieces(monkeypatch) -> None:
    from nova.voice import SoundDeviceSource

    monkeypatch.setitem(sys.modules, "sounddevice", None)
    source_ok = FakeAudioSource()
    stt_ok = FakeSTT()
    tts = FakeTTS()
    session = VoiceSession(source_ok, stt_ok, tts)
    assert session.available() is True
    session2 = VoiceSession(SoundDeviceSource(device=None), stt_ok, tts)
    assert "mic (sounddevice)" in session2.status()