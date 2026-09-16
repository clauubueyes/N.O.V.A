from __future__ import annotations

import asyncio
import hashlib
import struct
from array import array
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel

from nova.core.logging import get_logger
from nova.voice.base import AudioChunk, VoiceError
from nova.voice.registry import _availability, list_stt_backends, list_tts_backends
from nova.voice.registry import select_stt, select_tts
from nova.voice.textfilter import clean_for_tts

logger = get_logger("api.voice")

#: Browser recordings are tiny; 20 MB mirrors the common multipart ceiling.
MAX_AUDIO_BYTES = 20 * 1024 * 1024
SPEECH_CACHE_MAX = 32


class SpeakRequest(BaseModel):
    text: str


def _parse_wav(data: bytes) -> AudioChunk:
    """Parse a PCM16 WAV (mono or stereo) into a raw `AudioChunk` for Vosk."""
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise VoiceError("solo se admiten audios WAV (RIFF/WAVE)")
    off = 12
    fmt: dict[str, int] | None = None
    pcm = b""
    while off + 8 <= len(data):
        cid = data[off : off + 4]
        size = struct.unpack_from("<I", data, off + 4)[0]
        body = off + 8
        if cid == b"fmt ":
            aformat, channels, rate, _byte_rate, _align, bits = struct.unpack_from(
                "<HHIIHH", data, body
            )
            fmt = {"format": aformat, "channels": channels, "rate": rate, "bits": bits}
        elif cid == b"data":
            pcm += data[body : body + size]
        off = body + size + (size % 2)
    if fmt is None or not pcm:
        raise VoiceError("WAV sin bloque 'fmt ' o sin audio 'data'")
    if fmt["format"] != 1:
        raise VoiceError("solo se admiten WAV PCM sin compresión")
    if fmt["bits"] != 16:
        raise VoiceError("solo se admiten WAV con 16 bits por muestra")
    channels, rate = fmt["channels"], fmt["rate"]
    if channels == 2:
        pcm = pcm[: len(pcm) - (len(pcm) % 4)]
        stereo = array("h")
        stereo.frombytes(pcm)
        mono = array("h", ((stereo[i] + stereo[i + 1]) // 2 for i in range(0, len(stereo), 2)))
        pcm = mono.tobytes()
    elif channels != 1:
        raise VoiceError("solo se admiten WAV mono o estéreo")
    return AudioChunk(sample_rate=rate, data=pcm)


def build_voice_router(state: Any) -> APIRouter:
    """Web access to the local voice pipeline:

    - ``GET /v1/voice/status``  → engine availability (registry contract).
    - ``POST /v1/voice/transcribe`` → WAV upload → text (Vosk).
    - ``POST /v1/voice/speak``  → JSON text → WAV bytes (Piper).
    """
    router = APIRouter(prefix="/v1/voice", tags=["voice"])
    settings = state.settings

    def stt_provider():
        return state.voice_stt or select_stt(settings.voice.stt)

    def tts_provider():
        return state.voice_tts or select_tts(settings.voice.tts)

    def _require_voice() -> None:
        if not settings.voice.enabled:
            raise HTTPException(
                status_code=400,
                detail="La voz está desactivada en la configuración de N.O.V.A. "
                "(activa voice.enabled para usarla).",
            )

    @router.get("/status")
    def voice_status() -> dict[str, Any]:
        s = settings.voice
        stt, tts = select_stt(s.stt), select_tts(s.tts)
        stt_ok, stt_reason = _availability(stt)
        tts_ok, tts_reason = _availability(tts)
        return {
            "enabled": s.enabled,
            "stt": {"backend": s.stt.backend, "available": stt_ok, "reason": stt_reason},
            "tts": {"backend": s.tts.backend, "available": tts_ok, "reason": tts_reason},
            "backends": {"stt": list_stt_backends(), "tts": list_tts_backends(s.tts)},
            "max_audio_bytes": MAX_AUDIO_BYTES,
            "sample_rate": 16000,
        }

    @router.post("/transcribe")
    async def transcribe(request: Request) -> dict[str, str]:
        _require_voice()
        stt = stt_provider()
        ok, reason = _availability(stt)
        if not ok:
            raise HTTPException(status_code=400, detail=reason or "STT no disponible")
        data = await request.body()
        if len(data) > MAX_AUDIO_BYTES:
            raise HTTPException(status_code=413, detail="El audio supera los 20 MB.")
        try:
            audio = _parse_wav(data)
            if not audio.data:
                raise VoiceError("el audio está vacío")
            text = await asyncio.to_thread(stt.transcribe, audio)
        except VoiceError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"text": str(text).strip()}

    @router.post("/speak")
    async def speak(payload: SpeakRequest) -> Response:
        _require_voice()
        text = clean_for_tts(payload.text or "").strip()
        if not text:
            raise HTTPException(status_code=400, detail="No hay texto que leer.")
        tts = tts_provider()
        ok, reason = _availability(tts)
        if not ok:
            raise HTTPException(status_code=400, detail=reason or "TTS no disponible")
        if not hasattr(tts, "synthesize"):
            raise HTTPException(
                status_code=400,
                detail=(
                    "El backend TTS configurado no genera audio para el navegador. "
                    "Configura tts.backend: piper para leer las respuestas en la web."
                ),
            )
        key = hashlib.sha1(text.encode("utf-8")).hexdigest()
        cache = state.speech_cache
        wav = cache.get(key)
        if wav is None:
            try:
                wav = await asyncio.to_thread(tts.synthesize, text)
            except VoiceError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            cache[key] = wav
            if len(cache) > SPEECH_CACHE_MAX:
                cache.pop(next(iter(cache)))
        return Response(
            content=wav,
            media_type="audio/wav",
            headers={"Cache-Control": "private, max-age=3600"},
        )

    return router