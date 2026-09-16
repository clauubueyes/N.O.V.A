from __future__ import annotations

import struct

from fastapi import FastAPI
from fastapi.testclient import TestClient

from nova.api.app import create_app
from nova.core.config import load_settings
from nova.voice.base import VoiceError
from tests.test_api import FakeProvider


class FakeSTT:
    name = "stt"

    def __init__(self, text: str = "hola mundo") -> None:
        self.text = text
        self.calls: list = []

    def available(self) -> bool:
        return True

    def availability(self) -> tuple[bool, str]:
        return True, ""

    def transcribe(self, audio) -> str:
        self.calls.append(audio)
        return self.text

    def close(self) -> None:
        pass


class FakeTTS:
    name = "tts"

    def __init__(self, payload: bytes = b"wav-fake") -> None:
        self.payload = payload
        self.synthesized: list[str] = []

    def available(self) -> bool:
        return True

    def availability(self) -> tuple[bool, str]:
        return True, ""

    def synthesize(self, text: str) -> bytes:
        self.synthesized.append(text)
        return self.payload

    def close(self) -> None:
        pass


class PlayOnlyTTS:
    """Duck like pyttsx3: speaks locally, no synthesize bytes for the web."""

    name = "tts"

    def available(self) -> bool:
        return True

    def availability(self) -> tuple[bool, str]:
        return True, ""

    def close(self) -> None:
        pass


def _wav(pcm: bytes, *, rate: int = 16000, channels: int = 1) -> bytes:
    bits = 16
    block = channels * bits // 8
    byte_rate = rate * block
    return (
        struct.pack(
            "<4sI4s4sIHHIIHH4sI",
            b"RIFF",
            36 + len(pcm),
            b"WAVE",
            b"fmt ",
            16,
            1,
            channels,
            rate,
            byte_rate,
            block,
            bits,
            b"data",
            len(pcm),
        )
        + pcm
    )


def _upload(wav: bytes):
    return {"content": wav, "headers": {"Content-Type": "audio/wav"}}


def _make(
    tmp_path,
    *,
    enabled: bool = True,
    token: str = "",
    stt: FakeSTT | None = None,
    tts: FakeTTS | None = None,
) -> tuple[FastAPI, FakeSTT, FakeTTS]:
    settings = load_settings()
    settings.memory.db_file = str(tmp_path / "memory.db")
    settings.audit.file = str(tmp_path / "audit.jsonl")
    settings.api.token = token
    settings.voice.enabled = enabled
    stt = stt or FakeSTT()
    tts = tts or FakeTTS()
    app = create_app(settings, provider=FakeProvider())
    app.state.nova.voice_stt = stt
    app.state.nova.voice_tts = tts
    app.state.nova.speech_cache.clear()
    return app, stt, tts


class TestVoiceStatus:
    def test_reports_availability(self, tmp_path):
        app, _, _ = _make(tmp_path)
        with TestClient(app) as client:
            data = client.get("/v1/voice/status").json()
        assert data["enabled"] is True
        assert data["stt"]["backend"] == "vosk"
        assert "available" in data["stt"]

    def test_disabled_flag(self, tmp_path):
        app, _, _ = _make(tmp_path, enabled=False)
        with TestClient(app) as client:
            data = client.get("/v1/voice/status").json()
        assert data["enabled"] is False


class TestTranscribe:
    def test_disabled_returns_400(self, tmp_path):
        app, _, _ = _make(tmp_path, enabled=False)
        with TestClient(app) as client:
            body = client.post(
                "/v1/voice/transcribe",
                **_upload(_wav(b"\x00\x00")),
            )
        assert body.status_code == 400

    def test_invalid_wav(self, tmp_path):
        app, _, _ = _make(tmp_path)
        with TestClient(app) as client:
            body = client.post(
                "/v1/voice/transcribe",
                **_upload(b"not wav"),
            )
        assert body.status_code == 400
        assert "WAV" in body.json()["detail"]

    def test_successful_transcription(self, tmp_path):
        app, stt, _ = _make(tmp_path)
        with TestClient(app) as client:
            body = client.post(
                "/v1/voice/transcribe",
                **_upload(_wav(b"\x00\x00" * 8000)),
            )
        assert body.status_code == 200
        assert body.json()["text"] == "hola mundo"
        assert len(stt.calls) == 1

    def test_stereo_wav_downmix(self, tmp_path):
        app, stt, _ = _make(tmp_path)
        with TestClient(app) as client:
            body = client.post(
                "/v1/voice/transcribe",
                **_upload(_wav(b"\x00\x00\x00\x00" * 4000, channels=2)),
            )
        assert body.status_code == 200
        assert stt.calls[0].sample_rate == 16000

    def test_stt_error_returns_400(self, tmp_path):
        class BoomSTT(FakeSTT):
            def transcribe(self, audio):
                raise VoiceError("boom")

        app, _, _ = _make(tmp_path, stt=BoomSTT())
        with TestClient(app) as client:
            body = client.post(
                "/v1/voice/transcribe",
                **_upload(_wav(b"\x00\x00")),
            )
        assert body.status_code == 400


class TestSpeak:
    def test_disabled_returns_400(self, tmp_path):
        app, _, _ = _make(tmp_path, enabled=False)
        with TestClient(app) as client:
            body = client.post("/v1/voice/speak", json={"text": "hola"})
        assert body.status_code == 400

    def test_empty_text(self, tmp_path):
        app, _, _ = _make(tmp_path)
        with TestClient(app) as client:
            body = client.post("/v1/voice/speak", json={"text": ""})
        assert body.status_code == 400

    def test_missing_synthesize_returns_400(self, tmp_path):
        app, _, _ = _make(tmp_path, tts=PlayOnlyTTS())
        with TestClient(app) as client:
            body = client.post("/v1/voice/speak", json={"text": "hola"})
        assert body.status_code == 400
        assert "piper" in body.json()["detail"]

    def test_successful_speech(self, tmp_path):
        app, _, tts = _make(tmp_path)
        with TestClient(app) as client:
            body = client.post("/v1/voice/speak", json={"text": "Hola, ¿cómo estás?"})
        assert body.status_code == 200
        assert body.headers["content-type"] == "audio/wav"
        assert body.content == b"wav-fake"
        assert len(tts.synthesized) == 1
        assert "Hola" in tts.synthesized[0]

    def test_caches_repeated_identical_text(self, tmp_path):
        app, _, tts = _make(tmp_path)
        with TestClient(app) as client:
            client.post("/v1/voice/speak", json={"text": "igual"})
            client.post("/v1/voice/speak", json={"text": "igual"})
        assert tts.synthesized == ["igual"]

    def test_cleans_markdown_for_tts(self, tmp_path):
        app, _, tts = _make(tmp_path)
        with TestClient(app) as client:
            client.post("/v1/voice/speak", json={"text": "**hola** `mundo`"})
        assert tts.synthesized[0] == "hola mundo"


class TestAuth:
    def test_voice_endpoints_need_token(self, tmp_path):
        app, _, _ = _make(tmp_path, token="secret")
        with TestClient(app) as client:
            assert client.get("/v1/voice/status").status_code == 401
            assert client.post(
                "/v1/voice/transcribe",
                **_upload(_wav(b"\x00\x00")),
            ).status_code == 401
            assert client.post("/v1/voice/speak", json={"text": "hola"}).status_code == 401
            hdr = {"Authorization": "Bearer secret"}
            assert client.get("/v1/voice/status", headers=hdr).status_code == 200
            assert client.post(
                "/v1/voice/speak", json={"text": "hola"}, headers=hdr
            ).status_code == 200