# Investigación técnica: VoiceStudio (debpalash/VoiceStudio)

Informe estructurado generado a partir del análisis del repositorio
`https://github.com/debpalash/VoiceStudio` (branch `main`).

Objetivo: entender cómo el proyecto resuelve el registro de engines (TTS/ASR/LLM),
la detección de hardware, el diagnóstico, la seguridad local, las colas de trabajo,
la configuración y el instalador — para poder aplicar lo que valga la pena a un
asistente local-first en Python + Ollama.

Se distingue en cada área entre **IDEAS/PATRONES** y **CÓDIGO COPIABLE**, citando
rutas exactas. Las licencias van al final: no todo lo que ves aquí es copiable
libremente.

---

## A. Resumen ejecutivo y arquitectura global

VoiceStudio es (por README) "una alternativa local a ElevenLabs": clonación de voz
zero-shot, dictado, doblaje de vídeo y transcripción, todo on-device, en 646
idiomas. La arquitectura, confirmada también por código y por la UI, es:

```
Tauri v2 desktop shell (Rust)            ← "desktop/" en el repo
        │ IPC
React + Vite UI                          ← "frontend/"
        │ HTTP · SSE · WebSocket en localhost:3900
FastAPI backend (Python)                 ← "backend/"
        ├── registros de engines TTS/ASR (+ routers LLM/plugins)
        ├── pipelines de doblaje / audio / long-form
        ├── API compatible OpenAI (/v1) y servidor MCP
        └── SQLite + Alembic → omnivoice_data/
```

Puntos estructurales que importan de verdad:

- El backend es un *sidecar*: un proceso Python local que el shell de escritorio
  (Rust/Tauri) lanza, supervisa y mata. Todo sobre `localhost:3900`.
- Hay dos modos de despliegue del mismo backend:
  - **Desktop**: el shell Rust posee el backend con un Job de proceso (grupo de
    proceso en POSIX / Job Object de Windows) y se comunica por loopback sin
    credenciales (confianza de loopback).
  - **Server/Docker**: mismo backend, expuesto a red opcionalmente, protegido por
    API key larga (`OMNIVOICE_API_KEY`) y con mutaciones tras `require_admin`.
- Los engines (TTS/ASR) son *plugins* registrados en diccionarios módulo-nivel:
  hay un contrato ABC mínimo y un sistema de selección con auto-detección, sin
  recurso silencioso a fallbacks (regla "no silent behavior divergence").
- El código está lleno de comentarios enlazando issues (#NNN): es un proyecto en
  producción con lecciones caras aprendidas. Eso es oro técnico, pero copiar al
  pie de la letra no escala a un proyecto pequeño.

Stack y versiones: Python + FastAPI + uvicorn, PyTorch 2.8.0 (Docker base
`pytorch/pytorch:2.8.0-cuda12.8-cudnn9-runtime`), SQLite, Rust/Tauri v2 para el
shell, Bún/React/Vite para la UI.

---

## B. Stack técnico y árbol de carpetas (lo que existe)

Estructura de alto nivel (rama principal de GitHub):

```
.github/workflows/        # build/instalador (release.yml, ci.yml) — no disecado
backend/
  main.py                 # entrypoint: args --health-check / --diagnose / --deep / --supervise
  core/                   # núcleo sin dependencias pesadas
    config.py             # DATA_DIR (omnivoice_data) + redirección de HF cache Windows
    auth.py               # AuthPrincipal, PrincipalKind, capabilities, API key remota
    csrf.py               # checks de origin exacto, header x-voicestudio-csrf
    device_caps.py        # detect_host_caps() — única fuente de verdad hardware
    diagnose.py           # self-checks con shape {id,label,status,detail,hint}
    job_queue.py          # Job + JobQueue serial con cancelación
    job_store.py          # persistencia de jobs/eventos (cap 500 eventos/job)
    db.py                 # SQLite: schema base + remote_workers + enrollments
    contained_subprocess.py  # OwnedPopen / WindowsJobPopen (árbol terminable)
    path_security.py, _secret_key.py, tasks.py (SSE), win_subprocess.py
  services/               # la capa gorda: lógica de engines y modelo
    tts_backend.py        # registro TTS (TTSBackend, _LazyRegistry, list_backends)
    asr_backend.py        # registro ASR (ASRBackend + 10 engines)
    llm_backend.py        # OpenAICompatBackend (único, configurable)
    llm_providers.py      # Provider defaults (Ollama/LM Studio/remote)
    engine_routing.py     # resolve_routing: gpu_compat + HostCaps → verdict
    engine_env.py         # inyecta entorno/requirements por engine
    engine_evidence.py    # evidencia de ejecución real (no solo "torch.cuda")
    model_manager.py      # pool GPU del proceso (GPU/CPU job timeouts)
    model_lifecycle.py    # facade unload/unload_all (MM2-04)
    settings_store.py     # secretos cifrados ("secret_*" Fernet)
    prefs.py              # preferencias planas con precendencia + is_env_shadowed()
    ffmpeg_utils.py       # find_ffmpeg() validado (probe -version)
    sidecar_install.py    # dirs de instalar engines sidecar
    binary_preflight.py, gpu_gateway.py, gpu_sandbox.py, fit_planner.py, ...
  api/
    routers/system.py     # /system/info, /sysinfo, /system/logs*, /system/flush-memory, /system/set-env
    routers/engines.py    # instalación/estado de engines (UI Model Catalogue)
    routers/setup/wizard.py  # /setup/status, preflight, warmup
    public_engine_metadata.py
  worker/                 # trabajadores remotos (protocolo protobuf worker_v1)
    registry.py, pool.py, executor.py, capabilities.py, identity.py, breaker.py
    protocol/gen/worker_v1_pb2.py
  engines/                # 1 carpeta por engine pesado con __init__.py, bootstrap.py, main.py
    omnivoice_subprocess/, cosyvoice_subprocess/, indextts/, moss_tts_nano_subprocess/, ...
frontend/  →  React + Vite (Zustand, event bus WebSocket)
desktop/   →  Tauri v2 (Rust): src-tauri (backend.rs, orchestrator/, win_utils/, ...)
scripts/setup.py          # postinstalador: VC++/cuDNN8/ROCm
deploy/Dockerfile, docker-compose.yml
backend.spec              # PyInstaller (binario backend embebido)
tests/                    # unit/integration (p.ej. test_lazy_registry_concurrency.py)
docs/                     # guías por engine y por instalación
```

Notas sueltas de interés:

- En el tree persisten carpetas con nombres antiguos del proyecto
  (`desktop/orchestrator`, `desktop/model_manager`): son legado de nomenclatura,
  el código real de esos lugares está en `services/model_manager.py` y en Rust.
- El shell desktop usa `com.debpalash.omnivoice-studio` como bundle id; escribe
  `tauri.log` (plugin-log) y redirige stdout/stderr del backend a
  `backend.log`/`backend_err.log` (ver `backend.rs::backend_log_path()`).
- Sentencia de diseño repetida en el README: "Local-first: lo esencial se queda
  local; lo que toca red es opt-in explícito".

---

## C. Engine Registry — el patrón central (TTS y ASR)

Este es el corazón del proyecto y el patrón más valioso para un asistente que deba
soportar "estos engines, con estos requisitos hardware, de forma extensible".

### C.1 Contrato del engine (ABC)

`backend/services/tts_backend.py`:

```
class TTSBackend(ABC):                        # línea 205
    id = "base"
    display_name = "Base TTS"
    # Metadatos de selección/requisitos:
    #   gpu_compat (tuple)  — familias de aceleración usables, en orden de preferencia
    #   min_vram_gb (float) — floor de VRAM para el perfil acelerado
    @classmethod
    def is_available(cls) -> tuple[bool, str]:  # (ok, reason) — NUNCA lanza
    def synthesize(self, text, voice, ...) -> Iterator[bytes]: ...
    def unload(self) -> None: ...
```

`backend/services/asr_backend.py` moderniza el contrato (líneas 345+):

```
class ASRBackend(ABC):
    id: str = "base"
    display_name: str = "Base ASR"
    requires_full_audio_for_speaker_consistency: bool = False
    # Subset de {cuda, rocm, mps, xpu, cpu}; default conservador ("cpu",)
    # → cada engine declara realmente dónde corre; un claim rocm sin verificar es
    #   PEOR que un cpu_fallback honesto (comentario literal del código, ver C.3)
    gpu_compat: tuple[str, ...] = ("cpu",)

    @classmethod
    @abstractmethod
    def is_available(cls) -> tuple[bool, str]: ...

    @abstractmethod
    def transcribe(self, audio_path, *, word_timestamps=True) -> dict:
        # Devuelve EL SHAPE de Whisper sin tipar a propósito
        # {"chunks": [{"text", "timestamp": (start, end)}], ...}
        # así engines nuevos que ya hablan ese shape entran sin adaptador.
    def ensure_loaded(self) -> None: ...   # carga eager; RAISE la causa real una vez
    def unload(self) -> None: ...
    def execution_evidence_loaded(self) -> bool: ...
```

La idea clave de `execution_evidence_loaded()` es apuntar a *estado vivo real*
(última parte de `asr_backend.py`): para engines out-of-process comprueba que el
subproceso siga vivo (`runs_out_of_process` + `proc.poll() is None`); para los
in-process comprueba `_model/_asr/_pipeline/_pipe/_transcriber/_rec`. Es la base
del *execution evidence* que exige la lista WSL2 del README (ver Docker): "no
basta `/dev/dxg`, hace falta runtime probe + routing + workload + util GPU".

### C.2 Registro perezoso y selección

`tsT_backend.py` implementa `_LazyRegistry` como un dict con resolución diferida:
`_LAZY_REGISTRY: dict[str, tuple[str, str]]` mapea id → `(módulo, atributo)`;
`__missing__` importa el módulo la primera vez que se accede (bajo demanda), y
`__contains__`/`list`/`getitem` funcionan sin importar nada. Los engines pesados
(IndexTTS2, OmniVoice-GGUF/AudioCpp…) se resuelven así, y los ligeros se registran
directamente:

```
_REGISTRY: dict[str, type[TTSBackend]] = _LazyRegistry({ ... })   # línea 2394
# ids: "omnivoice", "cosyvoice", "kittentts", "mlx-audio", "voxcpm2",
#      "moss-tts-nano", "gpt-sovits", "sherpa-onnx" (+ lazy "indextts2", "audiocpp")
```

Selectores (mismo patrón en ASR):

- `backend/services/tts_backend.py`: `active_backend_id()` resuelve
  `prefs.resolve("tts_backend", env="OMNIVOICE_TTS_BACKEND", default="omnivoice")`
  → jerarquía **preferencia persistida → variable de entorno → default**.
- `list_backends(*, include_hidden=False) -> list[dict]`: devuelve por id:
  `id, name, display_name, available, unavailable_reason, last_error, install_hint,
  hidden` y método de instalación/hardware declarado. La UI "Model Catalogue" pinta
  exactamente esto.
- Caché de errores por engine: `_LAST_ERRORS[bid]` + `_INSTALL_HINTS[bid]` para
  mostrar en pantalla "instala tal paquete / descarga tal modelo" (ver primero en
  `include_omni` y después en `list_backends`).
- `_effective_backend_class()` para los perezosos: `get_backend_class(bid)` valida
  y devuelve la clase (`raise ValueError(f"Unknown TTS backend: {backend_id!r}...")`
  en `tts_backend.py:2774`).
- Los 8 engines ligeros del registro TTS llevan su `display_name` con detalle útil:
  `VoiceStudio (k2-fsa/OmniVoice, 600+ languages)`, `CosyVoice 3 (9 langs, zero-shot,
  instruct, Apache-2.0)`, `MOSS-TTS-Nano (20 langs, CPU realtime, 48 kHz)`,
  `KittenTTS (English, 8 preset voices, CPU realtime)`, `GPT-SoVITS (5 langs, RTF 0.014, MIT)`,
  `Sherpa-ONNX (20+ engines, WASM-ready)`, `MLX-Audio (mac-ARM, 14+ engines)`,
  `VoxCPM2 (30 langs, 48 kHz, voice design)`.

Registro ASR (id → clase, `asr_backend.py`): `whisperx` (default multiplataforma,
faster-whisper + wav2vec2 forced alignment), `faster-whisper` (CTranslate2),
`mlx-whisper` (Apple Silicon), `pytorch-whisper` (transformers, último recurso),
`nemo-parakeet` (NeMo TDT, CUDA/CPU), `parakeet-mlx` (MLX), `moonshine`
(edge/ONNX), `sherpa-onnx-asr` (dictado streaming), `funasr` (SenseVoice, VAD +
diarización inline), `openai-compat-asr` (cliente REMOTO opcional para
Qwen3-ASR/gigastt u OpenAI). README redondea a 11 con una variante adicional.

### C.3 Código copiable / fragmentos con valor

1) **Disponibilidad sin excepciones** — `is_available()` nunca lanza; devuelve
causa legible por el usuario. Galletita de los dos registros:

```python
@classmethod
def is_available(cls) -> tuple[bool, str]:
    try:
        import whisperx  # noqa: F401
    except ImportError as e:
        return False, f"whisperx not installed: {e}"
    except Exception as e:
        # #692: una lib nativa (CTranslate2 .so) rechazada por kernel endurecido
        # MORIRÍA de OSError, no ImportError. Reportar 'unusable here', nunca
        # lanzar: la selección cae al siguiente engine en vez de petar el preflight.
        return False, f"whisperx failed to load ({type(e).__name__}): {e}"
    return _ctranslate2_cudnn_ok()
```

2) **Degradación honesta, nunca silenciosa** — dos facetas:

   - `engine_routing.resolve_routing(gpu_compat, caps)` devuelve
     `RoutingStatus = Literal["accelerated", "cpu_fallback", "cpu_only",
     "unavailable", "n/a"]` con `routing_reason` explicito. En la log: la línea
     `Falling back to CPU:` nombra el mismatch de arquitectura (README WSL2).
   - Los promedios de compute tipo son cadenas explícitas, no intentos ciegos:
     `_compute_type_candidates("cuda")` → `["float16", "int8_float16", "int8"]`,
     CPU → `["int8", "float32"]` (override por `ASR_COMPUTE_TYPE`). El loader
     recorre la cadena y degrada: GPU sin fp16 eficiente (Maxwell/Pascal/GTX 16xx)
     lanza `ValueError("Requested float16 compute type...")` → reintentar el
     siguiente candidato en el MISMO device antes de tocar el camino OOM→CPU.
   - Regla explicitada en el código (#730): "VoiceStudio never switches engines
     automatically" — recomienda, no decide por ti.

3) **Caché de VRAM como preflight, no como postmortem** — `_degrade_for_vram()`
en `WhisperXBackend` (asr_backend.py#723): se comprueba VRAM *libre real*
(`torch.cuda.mem_get_info()`) ANTES de cargar; con presupuestos por compute type:

```python
_CUDA_VRAM_BUDGET_GB = {"float16": 5.0, "int8_float16": 3.5, "int8": 3.0}
_MODEL_VRAM_SCALE = (("large", 1.0), ("turbo", .55), ("medium", .5),
                     ("small", .25), ("base", .15), ("tiny", .1))
```

   Motivo documentado: cargar `large-v3` fp16 con el modelo TTS residente en una
   GPU de 8 GB muere como **abort nativo CUDA OOM** (sin excepción Python); la
   única defensa es no iniciar la carga. Opt-out: `OMNIVOICE_ASR_VRAM_PREFLIGHT=0`.

4) **Gate de librería nativa antes de elegir engine**: `_ctranslate2_cudnn_ok()`
en asr_backend.py (usa `core.cudnn8.ctranslate2_cudnn_status()`). Importar
whisperx no prueba nada: CTranslate2 solo toca cuDNN 8 cuando construye modelo
CUDA; si falta, imprime `Could not locate cudnn_ops_infer64_8.dll` y `__fastfail`
(0xC0000409, sin traceback). "Ask before selecting". Además **usan
`_ctranslate2_cuda_ok()`, NO `torch.cuda.is_available()`**: un torch ROCm también
contesta True y CTranslate2 no tiene backend HIP (#1529).

### C.4 Por qué funciona y qué no copiar

Lo que compone el registro y merece réplica: ABC mínimo, metadata declarativa por
engine (id/display/gpu_compat/min_vram), `is_available()->(ok, reason)`,
`list_backends` con hint de instalación, fallbacks explícitos y verificación
ex-ante de librerías nativas.

Lo que NO conviene copiar así, tal cual, en un proyecto pequeño:
- Los diccionarios enlazados a sub-issues (#NNN) en cada comentario: son el
  método de trabajo de un proyecto en producción; para un asistente privado es
  ruido.
- El registro perezoso con import diferido por engine es elegante pero solo
  necesario si tienes 10+ engines de dependencias pesadas; en un asistente con
  2-3 backends mejor import directo y explícito.
- La herencia `_LazyRegistry(dict)` con resolución en `__missing__` complica el
  tipado para IDE/MyPy; si no hay carga pesada tipo MLX/CTranslate2, sáltatela.

---

## D. Detección de hardware y routing CPU/GPU

`backend/core/device_caps.py` es la *única fuente de verdad* de hardware:

- `HostCaps` (dataclass): ram_gb, disk_free_gb, gpu_vram_gb, gpu_family,
  gpu_driver_version, mps_available, cuda_available, rocm_available, xpu_available,
  npu_available, cpu_count, platform, arch, vendor, filename-safe gpu_name.
- `detect_host_caps()`: **nunca lanza** (si algo falla, degrada a valores por
  defecto); no toca red; distingue ROCm de CUDA (no como muchos proyectos) usando
  la familia del device + versiones de driver, no el nombre del binario.
- `DeviceFamily = Literal["cuda", "rocm", "mps", "xpu", "npu", "cpu"]` con
  `ACCELERATOR_PRIORITY = ("cuda", "rocm", "xpu", "npu", "mps")` — ojo: en la
  prioridad de dispositivo, XPU/NPU van antes que MPS.
- `KERNEL_RISK_MARKER` y `DIRECTML_MARKER`: hack de compatibilidad para kernels
  WSL/DXG que confunden la detección (ver sección H y el README WSL2).

`backend/services/engine_routing.py` une caps + engine:

- `resolve_routing(gpu_compat_tuple, HostCaps) -> RoutingResult` con
  `effective_device`, `routing_status`, `routing_reason` (los status de C.3).
- Hook opcional `runtime_compute_profile(engine_or_cls, caps)`: algunos engines
  computan su perfil real (¿el modelo cabe en VRAM con X bytes? ¿conviene fp16?)
  y lo reutiliza el routing.

Reflejo en la API, `backend/api/routers/system.py` (verificado):

```python
_is_cuda = torch.cuda.is_available()
try:
    _is_xpu = hasattr(torch, "xpu") and torch.xpu.is_available()
except Exception:
    _is_xpu = False
# hechos hardware estáticos, capturados UNA vez en import
_CPU_MODEL = _detect_cpu_model()
_GPU_NAME, _VRAM_TOTAL_GB = _detect_gpu()
_RAM_TOTAL_GB = round(psutil.virtual_memory().total / (1024 ** 3), 1)
_OS_VERSION = platform.platform()
```

`_detect_gpu()` usa torch (cuda/xpu/mps) y, si el build es CPU-only, cae a
detección por SO: Windows `Get-CimInstance Win32_VideoController`, Linux
`lspci -mm` (tipo "VGA compatible"/"3D controller"), macOS `system_profiler
SPDisplaysDataType` — con `_gpu_name_priority()` para ignorar
"Remote/Virtual/Basic Display" y favorecer "nvidia/radeon/amd/intel arc". Test que
fija el contrato: `tests/test_bind_host.py` (binding localhost por defecto).

Detalle fino de `_detect_os_gpu_name()`: usa `shutil.which("powershell.exe")`,
`creationflags=CREATE_NO_WINDOW` y timeout de 2-3s con `check=False` — nunca debe
romper el arranque.

**Para tu asistente**: replica `detect_host_caps()` (never-raise, sin red, fuentes
múltiples) + `resolve_routing(gpu_compat, caps)`; expón `device` en `/system/info`
y en `GET /engine/{id}`. Es corto y barato, y te evita el caso "torch me dice
cuda=True pero la familia es rocm y el comentario falla".

---

## E. Seguridad local (IPC, auth, CSRF, secretos, workers remotos)

Es de lo más trabajado del repo. El modelo mental: **"todo el mundo local sin
credenciales si es loopback; todo el mundo remoto necesita credencial explícita"**.

### E.1 Cómo se autentica (backend/core/auth.py)

- `AuthPrincipal` (frozen dataclass): `kind`, `capabilities`, `credential_id`,
  `transport`. Se construye por request vía dependency.
- `PrincipalKind` enum: `ANONYMOUS, LOOPBACK, TRUSTED_NETWORK, PIN, API_KEY,
  ADMIN_SESSION`. `CredentialTransport` con valores tipo
  `visited_loopback`/`x_real_ip`/`header_api_key`/`pin_header`.
- `_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}`.
- Capabilities (sets de strings): `consume` (generar), `admin` (mutaciones),
  `native` + flags `loopback` / `admin_session`. `require_admin` chequea
  "loopback OR admin_session OR (remoto con API_KEY de admin)".
- `remote_api_key()` lee `OMNIVOICE_API_KEY`, y compara con `secrets.compare_digest`
  (comparación en tiempo constante).

### E.2 CSRF (backend/core/csrf.py)

- Checks de **origin exacto** (scheme+host+port contra el origin registrado del
  frontend), métodos seguros en `SAFE_HTTP_METHODS`, y header requerido
  `x-voicestudio-csrf`. Manejo expreso de `x-forwarded-proto` con análisis de
  spoofing (no confiar en cabeceras que vienen de red abierta).
- Razón: el loopback es un talón de Aquiles clásico — una página maliciosa en el
  navegador puede *llegar* a 127.0.0.1 (DNS rebinding / CORS). Origin-check +
  custom header es la defensa estándar y correcta.

### E.3 Secretos en reposo (backend/core/_secret_key.py + services/settings_store.py)

- `_secret_key.py` deriva la clave Fernet: `scrypt(passwd=machine-id,
  salt=16 bytes, n=2**14, r=8, p=1, keylen=32)`. Clave **no portátil entre
  máquinas** a propósito (un volumen copiado a otra máquina no revela secretos),
  y sin depender de keyring/OS (portable).
- `settings_store.py`: `get_secret(name)` / `set_secret(name, value)` con prefijo
  `secret_*` en la DB; las API keys de LLM viven con prefijo `llm_key.` e
  `ASR_OPENAI_COMPAT_SECRET_NAME = "asr_openai_compat_key"`.
- Paths: `core/path_security.py` (no leído en detalle) valida rutas antes de
  servirlas (download/upload), y `_LazyRegistry` no filtra secretos (mask de
  tokens HF `hf_[A-Za-z0-9]{30,}` al loguear).

### E.4 Front verdadero del control de acceso (router level)

`backend/api/routers/system.py` — patrón a imitar:

```python
router = APIRouter(dependencies=[Depends(require_admin)])
```

Comentario del propio código: cierra la "trust boundary" para TODAS las rutas
futuras del router (no solo set-env como el parche #81), incluyendo las de
información técnica (`/system/info`, `/system/logs`, ...) que un atacante de
loopback malicioso no debe leer.

`/system/set-env` solo acepta claves de un allowlist (`PERSISTENT_KEYS`), valida
el rango de los puertos (1024-65535) y acota cualquier timeout de cómputo a
`_MAX_GENERATE_TIMEOUT_S = 21600.0` (6h) para que un "3000000" accidental no ponga
un worker ocupado días.

### E.5 Workers remotos (backend/worker/registry.py) — el sistema más elaborado

Sirve para "Remote Model Downloads: instala modelos en workers remotos inscritos".
Diseño con muchas decisiones correctas:

- **Enrollment**: `create_enrollment(endpoint, cert_fingerprint, label,
  ttl_seconds=15*60)` genera un token y guarda SOLO su hash; `redeem_enrollment`
  lo consume con un `UPDATE ... WHERE used_at IS NULL` condicional → single-use
  a prueba de carreras (rowcount==1). `purge_expired_enrollments()` limpia.
- **Workers**: `RemoteWorker` dataclass con `key_id` (huella de clave pública),
  `public_key` bytes, `enabled`, `revoked` (tombstone, no delete: la fila se queda
  para que un reconnect con la misma clave sea *reconocido y rechazado*, no tratado
  como extraño), `priority` (default 50; mayor gana, empate → least-busy),
  `max_concurrent_tasks` (a través de `clamp_concurrency`), `session_epoch`.
- **Consentimiento por worker**: `consent_granted_at` — "enviar tu audio a esta
  máquina" es un sí **por worker**; "agrees to use your own desktop is not agreeing
  to use someone else's". Esto es un patrón directo para redes LLM con datos de
  usuario.
- **Autenticación de reconnect**: `authenticate(key_id, public_key, challenge,
  signature, nonce, session_epoch)` verifica posesión de la clave firmada; el
  caller "debe no distinguir" entre las razones de fallo (no dar massaging).
  `begin_session` incrementa `session_epoch`; la sesión zombi de un stream
  half-open se descarta por epoch ("the zombie-session race that otherwise
  delivers two accepts for one assignment").
- **Persistence split** (del docstring): *persistido* = identidad + claves +
  revocaciones + config + tokens; *NO persistido* = sesiones vivas, heartbeats,
  latencia, snapshots de capacidad, estado del breaker (se reconstruye del
  reconnect; el worker es la fuente de verdad de lo que corre).

Fragmentos con valor técnico real:

```python
# Enroll consume — single-use race-safe via conditional UPDATE
cur = conn.execute(
    "UPDATE remote_worker_enrollments SET used_at = ?, used_by_worker = ? "
    "WHERE token_id = ? AND used_at IS NULL",
    (stamp, worker_id, token.token_id),
)
return cur.rowcount == 1

# Revocación = tombstone, no delete
cur = conn.execute(
    "UPDATE remote_workers SET revoked = 1, revoked_at = ?, enabled = 0 WHERE id = ?",
    (stamp, worker_id),
)
```

**Para tu asistente**: el split persistido/no-persistido, la revocación como
tombstone, y la validación de "cada worker con su consentimiento y su prioridad"
son directamente aplicables a un cluster de Ollama local (varios nodos, keys por
nodo, enrollment con TTL).

### E.6 Agujeros que ya cerraron (para no repetir)

- Parche #81 solo tapaba set-env; la solución real fue el `Depends(require_admin)`
  a nivel de router (lección: aplicar el gate en el montaje del router).
- Toxicidad del subproceso: `win_no_console` parcheado para que los hijos en
  Windows no abran ventana de consola (molesta y rompe focus).
- CLOEXEC del fd de drenaje heredado por Rust: `secure_backend_drain_fd()` lo
  restaura (no filtrar fds entre procesos).

---

## F. Colas, jobs y procesos contenidos

### F.1 Cola de jobs (backend/core/job_queue.py)

- `JobState`: `queued / running / done / failed / cancelled`.
- `Job` dataclass con `cancel_event` (threading.Event) y `done_event`; el worker
  comprueba cancelación entre chunks/etapas, no entre requests.
- `JobQueue` = cola **serial, un solo worker** con `enqueue(job)`, `cancel(job)`,
  y `position_of(job)` para hacer el progreso honesto en UI. Escala por pipeline
  (batch generation = una cola; dictado = otra).

### F.2 Persistencia (backend/core/job_store.py + core/db.py)

- Tablas `jobs` y `job_events` en SQLite (`_BASE_SCHEMA`, `CREATE TABLE IF NOT
  EXISTS`). `_EVENT_CAP_PER_JOB = 500` (recorta eventos antiguos).
- Lifecycle API: `create_job`, `mark_running`, `mark_done`, `mark_failed`,
  `mark_cancelled`; eventos con `seq` para replay por SSE `?after_seq=N`.

### F.3 Procesos contenidos (backend/core/contained_subprocess.py) — joya

El docstring lo resume mejor que yo:

> "The desktop owns the backend with an OS process group/Job. Engine and
> installer operations also need an independently terminable subtree: killing
> only their direct child on a timeout leaves uv/git/model workers holding pipes
> and mutating files."

Por esto `main.py` reenvía a `--supervise control_fd result_fd -- <engine>`.
Diseño:

- **POSIX**: un mini-supervisor Python es el líder no-reaped de un process group
  anidado (`start_new_session=True`). Un hilo hace `read(control_fd)`; cuando el
  parent muere, el pipe da EOF (corte de kernel) → `os.killpg(getpgrp(), SIGKILL)`.
  Al terminar, `os.waitid(P_PID, pid, WEXITED|WNOHANG|WNOWAIT)` prueba que el líder
  sigue siendo tuyo y no re-used antes de hacer `killpg` (nada de "kill a reused
  group"). El resultado vuelve por `result_fd` (`_RESULT = struct.Struct("!i")`).
- **Windows** (`WindowsJobPopen` + `_windows_job()`): crea un Job Object de kernel
  con `KILL_ON_JOB_CLOSE`, lanza el hijo **SUSPENDED** (`CREATE_SUSPENDED`),
  `AssignProcessToJobObject` y resume con Toolhelp32Snapshot
  (`Thread32First`/`OpenThread(RESUME)`). Si el backend muere, el kernel cierra el
  job del backend y mata el subárbol de la operación — sin `taskkill` ni proceso
  Python extra en el path del sidecar (#1734). Preferido sobre el supervisor POSIX
  porque "Windows Job handles already provide the stable ownership that POSIX
  needs a supervisor process group for".
- Espaciado `subprocess.Popen` hereda `stdin/stdout/stderr` y expone `poll/wait/
  terminate/kill` con la misma semántica (`OwnedPopen` hace `__getattr__` al
  Popen subyacente).

Esto es la respuesta a "¿cómo cancelan un TTS largo y no dejan colgados pipas ni
workers de uv/git?"

### F.4 Timeouts y recuperación de wedges (asr_backend.py, #730)

- `ASR_TRANSCRIBE_TIMEOUT_S` (default 300s, env `OMNIVOICE_ASR_TRANSCRIBE_TIMEOUT_S`).
- `run_transcribe_guarded(executor, fn, timeout, reset_on_timeout)`: `asyncio.wait_for(asyncio.shield(fut))` — el shield preserva la distinción "cancelled before start" vs "thread running"; `on_abandon` se llama exactamente una vez.
- `reset_pool_after_wedge(executor, what)`: si el hilo quedó pillado, abandonar el pool (`executor.reset()` via `getattr`, para que tests con ThreadPoolExecutor simple sean no-op) para que la siguiente tarea coja un worker fresco. "This is the ONE recovery mechanism shared by every transcribe path."
- Tras 2 timeouts seguidos, `_isolated_engine_hint()` recomienda cambiar a
  `faster-whisper-isolated` (sidecar en subproceso que SÍ se puede hard-kill para
  recuperar VRAM) — pero solo lo **recomienda**.
- `contain_system_exit(fn, what)` (de `utils/containment`): un dependency escrito
  como CLI no puede llamar a `sys.exit()` y tirarse el backend (#1133).

### F.5 Código copiable

```python
# Error que nunca miente: distingue "backend caído" de "modelo muy pesado"
class ASRTimeoutError(TimeoutError):
    """... la app sigue viva: el modelo es demasiado pesado para el cómputo."""

# Django-like: disparar cleanup una vez, aunque el futuro se cancele antes de correr
def _abandon() -> None:
    cancelled_before_start = concurrent_fut.cancel()
    with abandon_lock:
        abandon_state["requested"] = True
        finished = abandon_state["finished"]
    fut.cancel()
    if cancelled_before_start or finished:
        _fire_abandon_callback()
```

---

## G. Diagnósticos, health, logs y estado

### G.1 Self-check (backend/core/diagnose.py)

Checks con shape uniforme:

```python
{"id": ..., "label": ..., "status": "ok" | "warn" | "fail",
 "detail": ..., "hint": ...}
```

- Umbrales: `_DISK_FAIL_GB=2`, `_DISK_WARN_GB=10`, `_RAM_WARN_GB=8`; disco va
  contra `DATA_DIR` (`shutil.disk_usage`).
- Exposición: `GET /system/diagnose` (respuesta ya estructurada para UI) y
  `python backend/main.py --diagnose` (texto para terminal); `--deep` hace probes
  reales de síntesis.
- **Scrub antes de salir**: todo pasa por `core.scrub.scrub_text` (tokens HF, rutas
  de usuario, emails) — el bundle de diagnóstico exportado está "scrubbed". Para un
  asistente local-first esto es oro: el informe de bug no debe contener tus claves.

### G.2 /system/info y /sysinfo (backend/api/routers/system.py)

- `/system/info`: el contrato es **"MUST never throw — it's called on every
  Settings page load and a 500 here blocks the entire UI"**. Verificado: todo el
  cuerpo dentro de try/except con defaults seguros + campo `"error": ...`. Campos:
  versiones, timeouts efectivos (`GPU_JOB_TIMEOUT_S`/`CPU_JOB_TIMEOUT_S`), *flag de
  env shadowed* (`is_env_shadowed("OMNIVOICE_GENERATE_TIMEOUT_S")`: si una env var
  externa pisa el valor guardado, la UI lo dice en vez de prometer un restart que
  no cambia nada), paths, `model_checkpoint`, `asr_model`, `translate_provider`,
  `has_hf_token` (vía `token_resolver` de 3 fuentes: App → Env → HF-CLI), estado
  Xet (`HF_HUB_DISABLE_XET=1` por defecto porque Xet rompe la barra de progreso),
  device, CPU/RAM/GPU/VRAM/disco, ffmpeg, proxy, network-share, puertos.
- `/sysinfo`: versión "live": cpu%, RAM usada/total, GPU util (nvidia-smi sin
  dependencias opcionales), VRAM usada/total, `gpu_active`.
- `_fast_download_status()`: reporta **verdad de runtime** (`xet_installed` vs
  `xet_active`), no solo importabilidad.

### G.3 Logs (patrón de rotación + streams)

- `_WindowsSafeRotatingFileHandler(backupCount=3)` rota `omnivoice.log` a
  2 MB → `.1/.2/.3` (hasta 6 MB de historia).
- `/system/logs?tail=`, `/system/logs/tauri?tail=` (los del plugin de Tauri:
  `~/Library/Logs/com.debpalash.omnivoice-studio/tauri.log`,
  `%LOCALAPPDATA%\com.debpalash.omnivoice-studio\logs`, etc.), y **SSE**
  `/system/logs/stream?source=backend|tauri` para vivir los logs en la UI
  (EventSource; detecta truncado por rotación reseteando `last_pos`).
- `/system/logs/clear` y `/system/logs/tauri/clear`: el de Tauri **NO borra**
  `backend.log`/`backend_err.log` — ese es el registro de stdout/stderr del backend
  escrito por Rust y es **append-only**; "a respawn must not destroy the previous
  run's evidence" (#1510). Clear borra conjunto fijo de nombres (`.1..N`), no por
  enumeración (ventana de rollover).
- `run_sentinel` (`core/run_sentinel`): detección de cierre sucio multidespliegue
  (crea un marker con `last_activity.kind` al arrancar; al arrancar siguiente, si
  no hay cierre limpio, notifica). Version-gated ("records from a different
  release than the running build are ignored"), watermark ack (size+mtime)
  mantiene el registro.

### G.4 Notificaciones accionables (/system/notifications)

Shape: `{id, level: info|warn|error, title, message, action: {label, type, target}}`.
Ejemplos (todos con *acción* adjunta): `hf-token-missing` (navegar a settings),
`gpu-arch-unsupported` (Blackwell sm_120 en wheel pre-cu128 → ruido, no silencio),
`ffmpeg-missing`, `disk-low` (<5GB), `gpu-unavailable`, `last-run-crash-{ms-id}`,
`crash-last-session`. Lección: **los problemas se reportan con una acción, no con
un error crudo** (y un panel de notificaciones re-polla cada 30s).

```python
@router.get("/system/notifications")
def system_notifications():
    # cada nota: id (único), level, title, message, action {label, type, target}
    return {"notifications": notes, "count": len(notes)}
```

---

## H. Configuración, almacenamiento y preferencias

### H.1 Preferencias (core/prefs) + Settings store

- `prefs`: mapa plano id→valor persistido (`prefs.json`), con `set`/`delete` y
  `resolve(name, env=..., default=...)` → **predcedence: env > persisted >
  default**. `is_env_shadowed(key)` para que la UI advierta cuando una env var
  ignora lo guardado.
- Persistencia de env vars de usuario vía `PERSISTENT_KEYS` y el paso de arranque
  "env_prefs" en `main.py` (restaura os.environ ANTES de que `model_manager` se
  importe — el orden `env_prefs < ml_imports < ...` es crítico y está comentado).
- `settings_store`: valores de texto + secretos (`get_secret`/`list_secret_names`)
  cifrados con la clave de H.2; prefix `secret_*`; el panel de Settings los expone
  "have", no su valor.

Para LLMs en concreto (`backend/services/llm_providers.py`):

```python
@dataclass(frozen=True)
class Provider:
    id: str
    display_name: str
    default_base_url: str
    default_model: str
# - Ollama y LM Studio entran como providers locales con base_url "local"
#   (sentinel: sin key). Los remotos piden secret con prefijo "llm_key.".
# - Precedencia de base_url del LLM: env > settings_store (get_text) > default.
# - Un solo backend cliente: OpenAICompatBackend (llm_backend.py), todo
#   OpenAI-compatible → "un solo adaptador, N proveedores".
```

Esto es *directamente* el modelo para Ollama en tu asistente: registro de
proveedores con default base_url/model, sentinel "local", y secreto cifrado.

### H.2 Secretos y claves

De E.3: `_secret_key.py` = Fernet derivado con scrypt(machine-id + salt); no
portátil entre máquinas, sin keyring. `config.py` añade dos decisiones: ruta de
datos por plataforma bajo `OMNIVOICE_DATA_DIR` (override) y **redirección del
HF cache en Windows** a `Data/Users/.../huggingface` (el largo del path LFS rompe
Windows; PATH_MAX). En Docker `HF_HOME=/app/omnivoice_data/huggingface`.

### H.3 Base de datos (core/db.py)

- `_BASE_SCHEMA` con `CREATE TABLE IF NOT EXISTS` → un `omnivoice_data/` viejo
  simplemente recoge las tablas nuevas sin migración (feature off por defecto en
  worker). Tablas `jobs`, `job_events`, `remote_workers`, `remote_worker_enrollments`...
  El directorio/DB se llama `omnivoice_data/`.

---

## I. Instalador, build y Docker

### I.1 Docker (deploy/Dockerfile + README)

- Base multi-stage: `oven/bun:1-alpine` (build UI) → `pytorch/pytorch:2.8.0-cuda12.8-cudnn9-runtime`.
  Variante ROCm por override de `BASE_IMAGE` (imagen `:rocm` del README usa ROCm
  7.2.x; requiere `HSA_ENABLE_DXG_DETECTION=1` porque AMD solo lo eliminó en
  ROCk 7.13).
- `ENV PYTHONPATH=/app/backend`, `HF_HOME=/app/omnivoice_data/huggingface`,
  `OMNIVOICE_SERVER_MODE=1`.
- Uso (README): `docker run -d -p 127.0.0.1:3900:3900 -v omnivoice-data:/app/omnivoice_data ...
  palashdeb/omnivoice-studio:stable` (imágenes en ghcr.io y Docker Hub con mismos
  tags; `:latest`=main preview, `:stable`/`:X.Y.Z`=release).

### I.2 scripts/setup.py (post-instalador)

- Windows: localiza e instala VC++ Redistributable (claves de registro + DLLs).
- CTranslate2/cuDNN 8: sirve cuDNN 8 junto al wheel porque **torch empaqueta
  cuDNN 9**, y whisperx/faster-whisper (CTranslate2) buscan la DLL cuDNN8 — sin
  ella: `Could not locate cudnn_ops_infer64_8.dll` + fastfail (0xC0000409). La
  lógica exacta se refleja en `core/cudnn8.ctranslate2_cudnn_status()`.
- ROCm: swap del wheel de torch según `OMNIVOICE_TORCH_VARIANT=rocm`.
- `backend.spec` (PyInstaller) → binario backend embebido para la app de escritorio;
  `.github/workflows/release.yml` publica installers de las 3 plataformas
  (no disecado).
- Wizard de primera ejecución: `backend/api/routers/setup/`: `GET /setup/status`,
  `GET /setup/preflight`, `POST /setup/warmup` (descarga modelos requeridos con
  progreso); `REQUIRED_MODELS`, `is_cached`, `MIN_FREE_GB`, `disk_free_bytes` en
  los modelos — el preflight se niega si no hay disco.

Falsa fe vía ffmpeg (lección de soporte #479, aplicada en `_decode_audio_16k_mono`
y `find_ffmpeg()`): en Windows `shutil.which("ffmpeg")` puede devolver un stub de
WindowsApps o un binario corrupto; **probe de `-version` en cada candidato** antes
de usar el binario (el bundle usa imageio-ffmpeg/Tauri sidecar). No copies el hacks
de PATH; copies el *probe*.

---

## J. Conclusiones: ideas vs. código copiable, y checklist para asistente local-first (Python + Ollama)

### J.1 Mejores ideas del proyecto (ordenadas por "rentabilidad para tu asistente")

1. **Registro de engines con metadata declarativa y disponibilidad sin excepción**
   (`is_available() -> (bool, reason)`, `list_backends` con install_hint,
   `active_backend_id()` con precedencia env>prefs>default). Per C.2-C.3. Aplica
   a TTS, ASR y a los *providers LLM/Ollama* por igual.
2. **Degradación explícita y honesta, nunca silenciosa**: `RoutingStatus` con
   reason, gpu_compat declarativo por engine, cadena de compute types probada en
   orden, recomendación en vez de auto-decisión. Per C.3.
3. **Preflight VRAM/lib-nativa antes de cargar modelo** (presupuestos por compute
   type, `mem_get_info()` real, gate cuDNN, `ctranslate2_cuda_ok()` en vez de
   `torch.cuda.is_available()`). Per C.3/D.
4. **Subprocesos contenidos**: supervisor/killpg en POSIX, Job Object con
   SUSPENDED+Assign+Resume en Windows; timeout va al grupo, no al hijo. Per F.3.
5. **Recuperación de wedges en pools**: abandonar/renovar el pool (no matar el
   hilo), streak → sugerir sidecar aislado. Per F.4.
6. **Seguridad de loopback**: origin-check + custom CSR-x header, `require_admin`
   a nivel de router (no ruta a ruta), allowlist de env vars, comparación en
   tiempo constante. Per E.
7. **Workers remotos como el control plane**: enrollment single-use con hash,
   revocación-tombstone, consentimiento por worker, sesión epoch anti-zombi. Per
   E.5 — aplicable a nodos Ollama distribuidos.
8. **Diagnóstico y UI honesta**: checks `{id,label,status,detail,hint}`, scrubbing
   de secretos en bundles, `/system/info` que nunca lanza, notifications con
   action, logs append-only del crash real. Per G.
9. **Timeouts atados a la generación** (GPU/CPU job timeout separados), acotados
   arriba, capturados en import, y surface "shadowed" si una env var los pisa. Per
   H.1/G.2.
10. **OpenAI-compat como único adaptador**: un backend (`OpenAICompatBackend`),
    N providers con default_base_url/model. Per H.1. (Tu asistente + Ollama ya
    encaja aquí.)

### J.2 Qué NO copiar tal cual

- **Licencias y pesos**: VoiceStudio es AGPL-3.0 app · Apache-2.0 código; los
  pesos de OmniVoice son CC-BY-NC (no-comercial) y el snapshot incluye un
  tokenizador bajo términos separados (Boson Higgs Audio 2 / Meta Llama). Si
  copias código de este repo, míralo bajo Apache-2.0 el parte del *engine
  registry* y acompáñalo de atribución; **no** reutilices los pesos.
- **La escala de hardening puntual** (speechbrain LazyModule patcheado,
  `contain_system_exit`, los diez fallbacks encadenados): es producto de
  soportar miles de configs de GPU. Para un asistente privado los casos raros
  (Maxwell/Pascal, WSL2/ROCm) no justifican el código. Quédate con los contratos,
  no con las piezas.
- **El árbol monoplataforma**: el proyecto mantiene sidecars por engine y un
  PyInstaller; complicado de mantener. En un asistente pequeño: procesos
  `python -m ...` supervisados con el patrón de F.3 (sencillo, portátil), no
  binarios embebidos.
- **`_secret_key.py` raw** (scrypt + machine-id): funcional y portable, pero en
  Windows/macOS un proyecto nuevo probablemente prefiera 2 líneas de keyring del
  SO + fallback de archivo. Conserva el "no-portátil entre máquinas" si el
  asistente maneja datos sensibles.
- Referencias #NNN en comentarios: no copies el método *annotating*; es ruido fuera
  de su tracker.

### J.3 Checklist concreto para tu asistente (Python + FastAPI? + Ollama)

- [ ] `TensorFam caps = detect_host_caps()`: una sola función nunca-raise, sin red.
- [ ] `RoutingResult = resolve_routing(provider.gpu_compat, caps)` y mostrarlo en
      `/system/info` + por-provider; log `Falling back to CPU: <reason>`.
- [ ] Registry de providers: `{id, display_name, base_url, model}` con
      `Provider.kind in {"local", "remote"}`; Ollama = `"local"` sin key.
- [ ] `is_available() -> (bool, reason)` por provider; `list_providers()` con
      `install_hint`/`unavailable_reason`; `last_error` cacheado.
- [ ] Precedencia triple: `env > settings.json > default` con `is_env_shadowed`.
- [ ] Secretos con prefijo `provider_key.` cifrados (Fernet) + scrub del texto en
      logs (`hf_...`, `sk-...`, `LLM_API_KEY`).
- [ ] `require_admin` de router a router; origin-check CSRF para loopback;
      API key remota opcional `NOVA_API_KEY` con `secrets.compare_digest`.
- [ ] Job queue single-lane + `cancel_event`, y supervisor de subprocesos con
      control-pipe EOF (POSIX) / Job Object KILL_ON_JOB_CLOSE (Windows).
- [ ] Timeouts de cómputo separados GPU/CPU, capturados en import, max 6h, y
      surface env-shadow.
- [ ] `/system/info` que nunca lanza; `/system/diagnose` con checks
      `{id,label,status,detail,hint}` y umbrales disco≤2/10GB, RAM≤8GB; scrub del
      bundle.
- [ ] Logs rotatorios (2MB×3) + tail cros-rotación + SSE stream; crash log
      append-only no truncable.
- [ ] Worker remotos (para tu cluster Ollama): enrollment single-use con hash,
      revocación-tombstone, consent por worker + epoch de sesión.
- [ ] HTTP en `127.0.0.1` (env `NOVA_BIND_HOST` override), puerto con pre-check
      antes de uvicorn, `OMNIVOICE_*` analog aquí verdaderamente `NOVA_*`.

---

Fuentes principales (todas leídas en rama `main`, sha `4e55180`):

- `backend/main.py` — args y binding (`_bind_host` default 127.0.0.1, `--supervise`).
- `backend/core/{config,device_caps,diagnose,job_queue,job_store,contained_subprocess,auth,csrf,_secret_key}.py`
- `backend/services/{tts_backend,asr_backend,llm_providers,engine_routing}.py`
- `backend/worker/registry.py`
- `backend/api/routers/system.py`, `backend/api/routers/setup/wizard.py`
- `deploy/Dockerfile`, `scripts/setup.py`, `README.md`, `docs/install/docker.md`

Nota: `backend/services/llm_backend.py`, `engine_env.py`, `/system/engine_metadata.py`
y el resto de `worker/` no fueron diseccionados; lo citado sobre LLM/workers sale de
`llm_providers.py` y `worker/registry.py` (fx ali de los contratos).