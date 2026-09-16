from __future__ import annotations

import logging
from typing import Any

from nova.core.config import VoiceSTTSettings, VoiceTTSSettings
from nova.voice.base import STTProvider, TTSProvider

logger = logging.getLogger("nova.voice.registry")


def _availability(provider: Any) -> tuple[bool, str]:
    """Call the rich ``availability()`` when present, else fall back to the
    legacy ``available()`` boolean (keeps duck-typed fakes working)."""
    method = getattr(provider, "availability", None)
    if method is not None:
        return method()
    return provider.available(), ""


def _build_tts(settings: VoiceTTSSettings) -> TTSProvider:
    from nova.voice.piper import PiperTTS
    from nova.voice.tts import Pyttsx3TTS

    voice = settings.voice or PiperTTS.default_voice
    if settings.backend == "piper":
        return PiperTTS(
            voice=voice,
            voice_dir=settings.voice_dir,
            auto_download=settings.auto_download,
        )
    if settings.backend != "pyttsx3":
        logger.warning("voice: backend TTS '%s' desconocido; usando pyttsx3", settings.backend)
    return Pyttsx3TTS(voice=settings.voice, rate=settings.rate)


def _build_stt(settings: VoiceSTTSettings) -> STTProvider:
    from nova.voice.vosk import VoskSTT

    if settings.backend != "vosk":
        logger.warning("voice: backend STT '%s' desconocido; usando vosk", settings.backend)
    return VoskSTT(model_dir=settings.model_dir, language=settings.language)


def select_tts(settings: VoiceTTSSettings) -> TTSProvider:
    """Build the TTS provider declared in `settings.tts.backend`."""
    return _build_tts(settings)


def select_stt(settings: VoiceSTTSettings) -> STTProvider:
    """Build the STT provider declared in `settings.stt.backend`."""
    return _build_stt(settings)


def list_tts_backends(settings: VoiceTTSSettings | None = None) -> list[dict[str, object]]:
    """List the available TTS backends as ``{id, name, available, reason,
    install_hint}`` — the engine-registry contract from the voice research."""
    instances = [
        _build_tts(VoiceTTSSettings(backend="pyttsx3")),
        _build_tts(VoiceTTSSettings(backend="piper")),
    ]
    out = []
    for provider in instances:
        ok, reason = _availability(provider)
        out.append(
            {
                "id": provider.name,
                "name": f"TTS ({provider.name})",
                "available": ok,
                "reason": reason,
                "install_hint": _install_hint(provider.name),
            }
        )
    return out


def list_stt_backends() -> list[dict[str, object]]:
    from nova.voice.vosk import VoskSTT

    provider = VoskSTT()
    ok, reason = _availability(provider)
    return [
        {
            "id": provider.name,
            "name": "STT (vosk)",
            "available": ok,
            "reason": reason,
            "install_hint": "pip install vosk y descarga un modelo de https://alphacephei.com/vosk/models",
        }
    ]


def _install_hint(backend_id: str) -> str:
    if backend_id == "piper":
        return (
            "pip install piper-tts; la voz 'es_ES-davefx-medium' se descarga sola "
            "la primera vez (rhasspy/piper-voices)"
        )
    return "pip install pyttsx3 (viene con el extra de voz)"