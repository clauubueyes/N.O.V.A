from __future__ import annotations

import os
import shutil
import subprocess
import threading
import tempfile
import urllib.request
from pathlib import Path

from nova.voice.base import TTSProvider, VoiceError

#: Piper voices come from the official rhasspy/piper-voices release repo.
_VOICES_URL = "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0"


def default_voices_dir() -> Path:
    """Where downloaded voices live (overridable with NOVA_VOICE_DIR)."""
    override = os.environ.get("NOVA_VOICE_DIR")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".nova" / "voices"


def _split_voice(voice: str) -> tuple[str, str, str]:
    """Split ``es_ES-davefx-medium`` into (lang, speaker, quality)."""
    lang, speaker, quality = voice.split("-", 2)
    return lang, speaker, quality


class PiperTTS(TTSProvider):
    """Natural, fully-offline TTS via ``piper-tts`` (Apache/MIT ecosystem).

    Two fallbacks keep this backend safe: the optional ``piper-tts`` package is
    imported lazily, and the first synthesis downloads the voice (a few tens of
    MB, once) from the official rhasspy/piper-voices repository into
    ``voice_dir``. When any of that is missing, ``availability()`` reports the
    exact reason instead of raising (registry pattern from the voice research).
    """

    name = "piper"
    default_voice = "es_ES-davefx-medium"

    def __init__(
        self,
        voice: str = default_voice,
        voice_dir: str | None = None,
        auto_download: bool = True,
    ) -> None:
        self._voice = voice
        self._voice_dir = Path(voice_dir).expanduser() if voice_dir else default_voices_dir()
        self._auto_download = auto_download
        self._lock = threading.Lock()

    def available(self) -> bool:
        return self.availability()[0]

    def availability(self) -> tuple[bool, str]:
        try:
            import piper  # noqa: F401
        except ImportError:
            return False, "piper-tts no está instalado (pip install piper-tts)"
        if self._cached():
            return True, ""
        if self._auto_download:
            return True, "la voz se descargará en la primera síntesis"
        return False, (
            f"la voz '{self._voice}' no está en {self._voice_dir} "
            "(descarga automática desactivada)"
        )

    def _cached(self) -> bool:
        return (self._voice_dir / (self._voice + ".onnx")).exists()

    def _voice_url(self, ext: str) -> str:
        lang, speaker, quality = _split_voice(self._voice)
        lang_dir = lang.split("_", 1)[0]  # es_ES -> es (carpeta del repo de voces)
        return f"{_VOICES_URL}/{lang_dir}/{lang}/{speaker}/{quality}/{self._voice}{ext}"

    def _download(self) -> tuple[Path, Path]:
        self._voice_dir.mkdir(parents=True, exist_ok=True)
        files = []
        for ext in (".onnx", ".onnx.json"):
            target = self._voice_dir / (self._voice + ext)
            if target.exists():
                files.append(target)
                continue
            tmp = self._voice_dir / (target.name + ".part")
            try:
                with urllib.request.urlopen(self._voice_url(ext), timeout=120) as resp:
                    with open(tmp, "wb") as fh:
                        shutil.copyfileobj(resp, fh)
                tmp.replace(target)
            except Exception as exc:
                tmp.unlink(missing_ok=True)
                raise VoiceError(
                    f"no se pudo descargar la voz '{self._voice}' de "
                    f"{_VOICES_URL}: {exc}"
                ) from exc
            files.append(target)
        return files[0], files[1]

    def speak(self, text: str) -> None:
        ok, reason = self.availability()
        if not ok:
            raise VoiceError(f"TTS not available (piper): {reason}")
        with self._lock:
            model_path, config_path = self._download() if not self._cached() else (
                self._voice_dir / (self._voice + ".onnx"),
                self._voice_dir / (self._voice + ".onnx.json"),
            )
            try:
                import wave

                from piper import PiperVoice
            except ImportError as exc:
                raise VoiceError("piper-tts no está instalado (pip install piper-tts)") from exc
            fd, name = tempfile.mkstemp(suffix=".wav")
            os.close(fd)
            path = Path(name)
            try:
                with wave.open(str(path), "wb") as wav:
                    PiperVoice.load(str(model_path), str(config_path)).synthesize_wav(text, wav)
                self._play_wav(path)
            except VoiceError:
                raise
            except Exception as exc:
                raise VoiceError(f"piper synthesis failed: {exc}") from exc
            finally:
                path.unlink(missing_ok=True)

    def _play_wav(self, path: Path) -> None:
        if os.name == "nt":
            import winsound

            winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_NODEFAULT)
            return
        for player in (
            shutil.which("aplay"),
            shutil.which("paplay"),
            shutil.which("afplay"),
        ):
            if player:
                subprocess.run([player, str(path)], check=True)
                return
        raise VoiceError("no hay reproductor de audio disponible (aplay/paplay/afplay)")

    def close(self) -> None:
        pass