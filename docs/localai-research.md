# Research: LocalAI como referencia para el Model Runtime / Router de N.O.V.A.

> Documento de investigación técnica sobre **LocalAI** (mudler/LocalAI, MIT) orientado a informar
> el diseño del Model Runtime/Router de N.O.V.A. Fuentes: rama `master` (git hash
> `15de88d3`), `backend/index.yaml`, `backend.proto`, docs oficiales y DeepWiki. La distinción
> **PATRÓN / IDEA** vs **CÓDIGO / ESTRUCTURA** marca lo que se debe *replicar de forma conceptual*
> contra lo que se puede *copiar casi literalmente*.

---

## A. Stack y mapa de carpetas

LocalAI es un **core Go compilado a binario único** cuya filosofía está escrita en el README:
"A small core, not a bundle. Each backend wraps a best-in-class engine." Desde ~v3.2 los motores
(llama.cpp, vLLM, whisper, etc.) dejaron de ir embebidos: ahora son **imágenes OCI separadas**,
descargadas a demanda (`pkg/downloader`), e incluso un backend puede ser un **endpoint gRPC
remoto** (p. ej. un `vllm` servido por otra máquina).

```
core/                 # la aplicación (un solo paquete Go plano, no narración)
  application/        # Application: compone services, registries, stores
  backend/            # 55 archivos con dispatch por RPC: llm.go, embeddings.go,
                      #   transcript.go, tts.go, image.go, video.go, 3d.go, soundgen.go...
  config/             # ModelConfig, ModelConfigLoader, backend_capabilities.go,
                      #   model_capabilities.go, hardware_defaults.go, gguf.go
  gallery/            # catalog index, backend_types.go, models.go, backends.go
  http/               # routes/ (openai.go, localai.go, ui_*.go), endpoints/,
                      #   middleware/ (auth, trace, usage), schema/
  services/           # galleryop (install ops asíncronas), nodes (distributed),
                      #   worker (backendSupervisor), routing (pii...), monitoring
pkg/                  # librerías reutilizables (no dependen de core/, ni a la inversa)
  model/              # ModelLoader, Model, process.go, watchdog.go, initializers.go
  grpc/               # client / server wrappers sobre backend.proto
  system/             # SystemState: detección GPU/vendor/capability
  downloader/         # URI schemes: huggingface://, ollama://, oci://, file://, http(s)://
  store/              # vector store local (pendiente de ver en detalle)
  vram/ xsysinfo/     # estimación de footprint GGUF y lectura de RAM (cgroup-aware)
  modelartifacts/     # materialización de snapshots HF (transformers/vllm/safetensors)
backend/              # NOTA: no colisiona con core/backend. Aquí viven backend.proto,
                      #   el registry backend/index.yaml y los engines (cpp/, python/, go/)
```

**Claves de arquitectura:**

- **Un contrato único, gRPC**: `backend/backend.proto` define el servicio `Backend`
  (`Health`, `LoadModel`, `Predict`, `PredictStream`, `Embedding`, `Rerank`,
  `TokenizeString`, `GenerateImage`, `Transcribe`, `TTS`, `VAD`, `AudioTransform`,
  `Diarize`, `SoundDetection`, `FaceVerify`, `VoiceVerify`, `Score`, `GetMetrics`...).
  Todo motor — C++, Python, Go — implementa ese mismo servicio. `pkg/grpc/backend.go`
  exige en tiempo de compilación que el wrapper cumpla `InferenceBackend + ControlBackend`.
- **Un proceso por modelo**: `pkg/model/process.go` lanza el binario del backend en
  `127.0.0.1:<puerto libre>` con `go-processmanager`, en un *state dir* propio
  (`backendProcessRuntime`), con `stderr/stdout` seguidos por `tail` y volcados al log
  (`BackendLogStore`, 1000 líneas). Esto aísla crash de un motor (SIGSEGV, ImportError de Python)
  del core.
- **Dos HTTP**: el core usa Echo (`github.com/labstack/echo/v4`); las rutas OpenAI → `/v1/*`
  y las propias de LocalAI se registran en `core/http/routes/openai.go` y
  `core/http/routes/localai.go`.
- **Modo distribuido** como capa: el control plane dialoga con workers vía NATS + registry
  SQL (`core/services/nodes`), y el `NetworkRouter`/`SmartRouter` hace selección de réplica
  por petición (`PickBestReplica`) en vez de cachear el *Model*.

```go
// PATRÓN (pkg/model/loader.go): encapsulación de Backend detrás de un cliente.
// El resto de la app habla con grpc.Backend; nunca con el proceso ni con el binario.
type Model struct {
    ID       string
    address  string
    client   grpc.Backend
    process  *process.Process
    // lastHealthCheck: TTL 30s para evitar un probe gRPC en cada request (distribuido)
}
```

---

## B. Abstracción de modelo y "backend registry"

Es el corazón. Hay **tres vocabularios** que se cruzan:

1. **Usecases canónicos** (`core/config/backend_capabilities.go`, constante `Usecase*`):
   `chat`, `completion`, `edit`, `vision`, `embeddings`, `tokenize`, `image`, `video`,
   `3d`, `transcript`, `tts`, `sound_generation`, `rerank`, `detection`, `depth`, `vad`,
   `audio_transform`, `diarization`, `sound_classification`, `realtime_audio`,
   `face_recognition`, `speaker_recognition`, `token_classify`, `score`.
   Cada uno tiene un **bitmask** (`FLAG_CHAT`, `FLAG_VISION`...), un **RPC gRPC de destino**
   (`GRPCMethod`), y los *modificadores* (`IsModifier: true, DependsOn: "chat"`):
   **`vision` no tiene RPC propio, modifica `Predict`** (usa `mmproj` en llama.cpp).
   `UsecaseInfoMap` mapea string → esa metadata.
2. **Tabla de capacidades por backend ya verificado** (`BackendCapabilities map`) — fuente
   de verdad en Go:

```go
// PATRÓN: "possible" vs "default" conservador.
type BackendCapability struct {
    GRPCMethods            []GRPCMethod   // RPCs que implementa
    PossibleUsecases       []string       // todo lo que PUEDE
    DefaultUsecases        []string       // lo seguro por defecto (ej. {"chat"})
    AcceptsImages          bool           // entrada multimodal en Predict
    AcceptsVideos          bool
    AcceptsAudios          bool
    AudioTransformInputMono16k bool       // opt-IN: requiere fold a 16kHz/mono/WAV
    voiceCloning            *VoiceCloningCapability
    TTSVoices              []TTSVoice
    Description            string
}
```

   Ejemplos reales: `llama-cpp` declara `{Predict, PredictStream, Embedding,
   TokenizeString, Score, TTS, TTSStream}` como métodos y possibles `chat, completion,
   edit, embeddings, tokenize, vision, score, tts`, pero **default `chat`** (un GGUF pelado
   es chat; el TTS se declara por modelo con `known_usecases: [tts]`). `nemo-speech-cpp`
   es un solo gRPC server que sirve ASR, diarización, TTS y traducción según la *arquitectura
   GGUF*: su `PossibleUsecases` es la **UNIÓN** y su default solo `transcript`.
3. **Registry declarativo** (`backend/index.yaml`): una lista de entradas de backend con
   metadatos y un mapa `capabilities:` que enruta de capability de hardware a **imagen
   concreta** (`GalleryBackend` en `core/gallery/backend_types.go`):

```yaml
# ESTRUCTURA COPIABLE (backend/index.yaml) — un meta-backend (sin uri)
- &llamacpp
  name: "llama-cpp"
  alias: "llama-cpp"
  license: mit
  capabilities:
    default: "cpu-llama-cpp"
    nvidia: "cuda12-llama-cpp"
    nvidia-cuda-13: "cuda13-llama-cpp"
    nvidia-cuda-12: "cuda12-llama-cpp"
    nvidia-l4t-cuda-12: "nvidia-l4t-arm64-llama-cpp"
    intel: "intel-sycl-f16-llama-cpp"
    amd: "rocm-llama-cpp"
    metal: "metal-llama-cpp"
    vulkan: "vulkan-llama-cpp"
- !!merge <<: *llamacpp          # variante de canal (development/quantization)
  name: "llama-cpp-development"
  capabilities:
    default: "cpu-llama-cpp-development"
# ...y las imágenes concretas con uri real (localhost — un backend anda suelto):
- !!merge <<: *llamacpp
  name: "cuda12-llama-cpp"
  uri: "quay.io/go-skynet/local-ai-backends:latest-gpu-nvidia-cuda-12-llama-cpp"
  mirrors: ["localai/localai-backends:latest-gpu-nvidia-cuda-12-llama-cpp"]
```

   Reglas de resolución (PIEZA CLAVE): `SystemState.Capability()` devuelve la capability
   detectada del host (o `LOCALAI_FORCE_META_BACKEND_CAPABILITY`), y el meta-backend se
   resuelve a la imagen concreta vía ese mapa. Si el host reporta `vulkan-audio-cpp` pero no
   hay clave → *falla a `default`* (CPU), no a error. **Tres vocabularios de "hardware"**:
   capability detectada, `EnginePreferenceTokens` (qué runtime prefiere ese host, ej.
   `vllm` sobre `llama-cpp` en NVIDIA), y `ServingFeaturePreferenceTokens` (preferencia por
   features de serving, p. ej. streaming).

**Normalización de nombres** (`GetBackendCapability`): exact match primero; si no, se le
**quitan** prefijos de hardware de la galería (`galleryHardwarePrefixes`: `"cuda13-
nvidia-l4t-arm64-"`, `"vulkan-"`, `"cpu-"`...) y sufijos de canal
(`-development`, `-quantization`) y se busca el meta. Por eso `cuda12-vibevoice-cpp` resuelve
a la capacidad de `vibevoice-cpp`. Un bug real de este sistema (#10945) fue que *solo*
match exacto rompía visión en variantes pineadas — lección: normalizar siempre antes de
decidir comportamiento.

**Alias por configuración en runtime** (`pkg/model/initializers.go`): `Aliases` map
(`llama` → `llama-cpp`, `huggingface-embeddings` → `transformers` con `TypeAlias`
`SentenceTransformer`) + backend externo por URI (`SetExternalBackend(name, uri)`).

**Auto-detección de backend** (`ModelLoader.Load`): si `backend:` está vacío, el loader
itera los backends disponibles **en orden** y prueba a cargar; pero al detectar GGUF solo
prueba contra backends que tengan un usecase de `llmAutoLoadUsecases`
(`chat/completion/edit/embeddings`) — issue #9287: no intentar `opus` (codec) o un backend
de audio al lado del GGUF.

> Traducción para N.O.V.A.: el "registry de backends" NO es un array de objetos con nombre y
> versión; es (a) un set de usecases/bitmask, (b) una tabla por-backend de lo possible/default,
> (c) un mapa declarativo capability→artefacto, (d) una capa de alias. Las cuatro piezas viven
> separadas y se juntan en el *loader*, no en una sola struct.

---

## C. Detección de capacidades

`pkg/system/capabilities.go` concentra la detección de hardware:

- Capacidades con nombre: `nvidia-cuda-13`, `nvidia-cuda-12`, `nvidia-l4t-cuda-12/13`,
  `metal`, `vulkan`, `darwin-x86`, y `default` (cuando nada se detecta).
- Vendors: `nvidia`, `amd`, `intel`; engines sobre los que se puede elegir: `vllm`,
  `sglang`, `llama-cpp`, `mlx` (vídeos, tokens de detección por toolchain).
- **Overrides**: env `LOCALAI_FORCE_META_BACKEND_CAPABILITY` y run-file
  `/run/localai/capability` (`LOCALAI_FORCE_META_BACKEND_CAPABILITY_RUN_FILE`) — sirve para
  containers/containers como DGX Spark que traen la capability "escrita" por el operador en
  el filesystem.

```go
// PATRÓN (pkg/system/capabilities.go): SystemState es la "voz del hardware".
// getSystemCapabilities() → getSystemCapabilities() (env run-file) → Capability() →
// IsBackendCompatible(backend, "") — que deriva Darwin-only / NVIDIA-only / ROCm-only
// desde el NOMBRE del backend, sin que el autor de la galería describa hardware.
func (s *SystemState) IsBackendCompatible(backend, uri string) bool { ... }
```

- En modo distribuido, los **workers** reportan su capability al registro de nodos; el
  control plane arma `availableCapabilities` y los endpoints `/api/backends/available`
  computan la intersección con lo instalado (`ClusterCapabilityProviderFor`,
  `ClusterInstalledProviderFor`).

**Mapeo de modelos a capacidades** (`core/config/model_capabilities.go`): la *entrada* real
de un modelo es `known_usecases` (bits declarados por el autor del YAML). `GuessUsecases`
infiere bits por heurística desde el *file name / path / backend*. Para decidir si un modelo
**acepta visión** NO se usa `FLAG_VISION` de GuessUsecases (el propio comentario del código
explica que GuessUsecases no tiene rama FLAG_VISION y *pinta visión en todo modelo chat*);
se usa `KnownInputModalities` (text/image/audio/video/3d canónico) o el marcador multimodal
de `mmproj`/template. Lección N.O.V.A.: **el flag "auto-guessado" no debe darse por bueno
para capacidades críticas**; declara explícito o calla.

---

## D. Hardware: auto-tuning y gestión de memoria

`core/config/hardware_defaults.go` + `pkg/model/watchdog.go` + `pkg/vram`:

- `ApplyHardwareDefaults(cfg, gpu)` se parametriza con un descriptor **`GPU{Vendor,
  ComputeCapability}`**, no con detección directa — es lo que permite aplicarlo al nodo
  seleccionado en modo distribuido. Configurable: `LOCALAI_DISABLE_HARDWARE_DEFAULTS`.
  Reglas aplicadas:
  - **n_batch/n_ubatch = 2048** en Blackwell (compute capability ≥ 12) para saturar prefill MoE.
  - **Guardia de VRAM**: estima footprint, y si un contexto grande no cabe, reduce el batch
    físico.
  - **Slots paralelos** escalados por VRAM (1 slot en 2GB → 8 slots en 48GB+).
- **`gpu_layers`** (llama.cpp): deck hace offload por **capas** del LLM a VRAM. Doc oficial:
  - valor por defecto de LocalAI: **9999999** ("todo a GPU"), integrado con su mecanismo de
    **unloading por umbral de VRAM**.
  - `0` → CPU.
  - `-1` → *auto-fit* de llama.cpp (cabe lo que quepa), **desaconsejado** porque colisiona
    con el reclaimer de VRAM de LocalAI (issue conocido `tensor_buft_override`).
- **Estimación de footprint sin descargar** (`pkg/vram.EstimateModelMultiContext`):
  range-fetch del header GGUF → HEAD HTTP → `size:` declarado. Se usa para *elegir variante*
  y para la guardia de VRAM.
- **WatchDog** (`pkg/model/watchdog.go`): monitor de salud/último uso por dirección; hace
  **evicción LRU** (max backends) y **memory reclaimer** por umbral (95%). Modelos con
  `pinned: true` quedan excluidos; `concurrency_group` fuerza exclusividad mutua (dos modelos
  del mismo grupo no coexisten en el nodo — útil para VRAM).
- **Shutdown en 2 fases** (`pkg/model/process.go`): primero RPC `Free()` para liberar VRAM
  (con `Unimplemented` tolerado), luego stop del proceso; en `force` (watchdog busy-killer)
  se omite el `Free` y se mata directo.

```go
// PATRÓN (pkg/model/process.go): LOCKS por modelo, no globales.
// Las operaciones de ciclo de vida de cada modelo llevan su propio lock
// (modelOperationLocks), para que un backend roto no bloque e el ciclo de vida de otro.
// Cooldown tras carga fallida: base 10s → máx 5min (exponencial), devuelto como
// ModelLoadCooldownError → HTTP 503 + Retry-After en el handler.
```

---

## E. Galería y ciclo de vida de instalación

`core/gallery/models.go` + `models_types.go` + `backends.go`:

- **Entrada de catálogo** (`GalleryModel`): `ConfigFile map[string]any`, `Overrides
  map[string]any`, `Variants []Variant`. Un `Variant` referencia **otra entrada** del catálogo
  (build distinto: MLX/vLLM/quantización distinta) o el propio entry como base.
- **Resolución de variante** (`ResolveVariant`, `SelectVariant`): vota hardware
  (`IsBackendCompatible`), luego **rank** por `EnginePreferenceTokens` +
  `ServingFeaturePreferenceTokens`, luego **memoria sondeada** (probe con timeout 5s por
  variante; desconocido = se de-valora, no falla el install). La base del entry SIEMPRE
  resuelve (compatibilidad hacia atrás) pero se rankea también.
- **Pin persistente**: el pin elegido se guarda en `._gallery_<installName>.yaml`
  (`PinnedVariant`, `ResolvedVariant`). Reinstalaciones respetan el pin; si la variante
  pineada desaparece del catálogo, se degrada a re-selección con warning (evita modelo
  "permanentemente roto" por un dotfile).
- **Instalación** (`InstallModelFromGallery` → `InstallModel`):
  1. Obtiene config (por `url` remota o `config_file` embedido); **merge** de `overrides`
     del request con `mergo.WithOverride`.
  2. `files:` → lista `downloader.FileTask{URI, Destination, SHA256, ...}`; verifica SHA.
  3. **Escaneo de seguridad** `HuggingFaceScan` (clamAV + pickles peligrosos) si `enforceScan`
     (env `LOCALAI_DISABLE_SCAN` controla el flag; verificar).
  4. `prompt_templates` → se escriben como `<name>.tmpl`.
  5. Escritura **atómica** del `<name>.yaml` final (`writeModelConfigAtomic`) + metadata
     `._gallery_<name>.yaml`.
  6. Instala el backend automáticamente si el modelo lo necesita (`automaticallyInstallBackend`)
     desde la backend gallery → imagen OCI (`InstallBackendFromGallery`: uri + mirrors,
     `sha256`/digest, verificación cosign opcional).
  - Deep-copy de maps antes de mutar (mergo escribe in-place): protege el catálogo cacheado.
- **Jobs asíncronos**: `/models/jobs/:uuid`, `/backends/jobs/:uuid`,
  `/backends/upgrades` con `OpCache` + `galleryService` (`core/services/galleryop`).
- **Delete** (`DeleteModelFromSystem`): recoge los archivos del modelo y los de TODOS los
  demás (refcount manual); un archivo compartido no se borra.

```go
// ESTRUCTURA COPIABLE — bloques básicos del "apply" de la galería
type File struct {
    Filename string `yaml:"filename"`
    SHA256   string `yaml:"sha256"`
    URI      string `yaml:"uri"`
}
type ModelConfig (gallery) struct {
    Description, Icon, License, Name string
    ConfigFile      string            // YAML embebido
    Files           []File
    PromptTemplates []PromptTemplate  // -> <name>.tmpl
    // registro de resolución de variante para reinstalar/upgradar en el mismo pin:
    EntryName, ResolvedVariant, PinnedVariant string `yaml:"...,omitempty"`
}
```

`BackendMetadata` (instalado en el nodo): `{Alias, MetaBackendFor, Name, GalleryURL,
InstalledAt, Version, URI, Digest}` — es lo que permite `GET /backends/upgrades`
comparar el digest publicado vs instalado.

---

## F. Compatibilidad de API / superficie de servidor

- `core/http/routes/openai.go` registra OpenAI-compatible: `/v1/chat/completions`,
  `/v1/completions`, `/v1/embeddings`, `/v1/models`, `/v1/images/generations`,
  `/v1/audio/transcriptions`, `/v1/audio/speech`, Realtime API (`/v1/realtime`,
  `/v1/realtime/sessions`, `/v1/realtime/transcription_session`, `/v1/realtime/calls`).
- Middleware stack común: `TraceMiddleware`, `UsageMiddleware`, y opcional
  `nodeHeaderMiddleware` (`--expose-node-header` → `X-LocalAI-Node`), útil en distribuido
  para saber qué worker sirvió.
- **Cold load con 503 en envelope OpenAI** (`core/schema/model_loading.go`):
  `/api/models/:id/load-status` expone progreso del load (en distribuido consulta el
  `LoadJobStore` del registry del nodo); el 503 de loading para clientes OpenAI es un
  error en formato OpenAI con campo `loading`.
- **Auto-descubrimiento**: `GET /.well-known/localai.json` devuelve endpoint map plano +
  `endpoint_groups` (openai_compatible, model_management, monitoring, mcp, stores, agents...)
  + `capabilities` flags; `GET /api/features` idem booleans (`agents`, `mcp`,
  `fine_tuning`, `quantization`, `distributed`). Pensado para que agentes se descubran sin creds.
- `GET /api/instructions` (sin auth) da instrucciones de uso a los agentes.
- Salida multimodal propia: `/video`, `/3d/generations`, `/v1/detection`, `/v1/face/*`,
  `/v1/voice/*`, `/vad`, `/audio/transform(ations)`, `/stores/*`, TTS `/tts`, etc.

---

## G. Embeddings / RAG

- **`/v1/embeddings`** (OpenAI-compatible): compatible con backends llama.cpp, bert.cpp,
  sentence-transformers (Python). YAML mínimo: `name`, `parameters.model`,
  `backend: "<backend>"`, `embeddings: true`. Modelos de galería `qwen3-embedding-*`.
- **Stores** (vector store local): `/stores/set`, `/stores/get`, `/stores/find`,
  `/stores/delete` (implementación en `pkg/store/`). LocalAI mantiene su propia capa de
  retrieval; documenta integración con **LlamaIndex** para RAG.
- Lectura transversal: N.O.V.A. ya usa `nomic-embed-text` vía Ollama; LocalAI no obliga a
  un proveedor de embeddings — cualquier backend con usecase `embeddings` sirve el mismo
  endpoint, y el router elige por capacity. En el runtime propio de N.O.V.A., el endpoint
  `/v1/embeddings` debería delegar a cualquier provider que declare `embeddings`, igual que
  hace el `/v1/chat/completions` con `chat`.

---

## H. Seguridad

`core/http/auth/middleware.go` implementa **3 capas** sobre Echo:

1. **`auth.Middleware` (global)**: orden de resolución
   1. sin auth y sin legacy keys → *pass-through*;
   2. con base de datos de usuarios (auth DB asíncrono opcional): cookie de sesión → DB,
      `Authorization: Bearer` (valida primero como sesión, luego como user API key) →
      `x-api-key` / `xi-api-key` → cookie `token`; rotación de session cookies con HMAC;
   3. con **legacy API keys** (env/flag `--api-keys` / `API_KEY`): si la key es válida
      → **usuario sintético con rol `admin`** (comparación en **tiempo constante**,
      `crypto/subtle`);
   4. rutas públicas + `--path-without-auth` (prefijos) + `usesAlternativeAuthentication`;
   5. si `--disable-api-keys-for-http-get` + regex de `HttpGetExemptedEndpoints`, GETs
      exentos pasan;
   6. si no → 401 `WWW-Authenticate: Bearer` (o `204 No Content` con `OpaqueErrors`).
2. **Feature/permisos por ruta**: `RequireRouteFeature` (mapea `METHOD:route-cache` →
   feature) y `RequireFeature(feature)`; admins siempre pasan; el resto debe tener el permiso
   (no presente → default on/off según `isDefaultOnFeature`).
3. **Gates de autorización** al vuelo: `RequireAdmin`, `RequireModelAccess` (allowlist de
   modelos por usuario; el nombre se extrae de path/query/JSON/form, con cuerpo re-readable)
   y `RequireQuota` (429 + `Retry-After` en rutas de inferencia).
- Los **endpoints de administración de galería** (`/models/apply`, `/backends/*`,
  `/stores/*`...) piden `adminMiddleware` = `RequireAdmin` (o `NoopMiddleware` sin auth).
- Endpoints **seguramente sin auth a propósito**: `/api/instructions`, endpoints de
  discovery, y el estado de carga del modelo (`/api/models/:id/load-status`) — gatearlo
  detrás de admin ocultaría la explicación del 503 al cliente que la necesita.
- gRPC distribuido autenticado por bearer token (`NewClientWithToken`).
- PII: middleware `pii.RequestMiddleware(redactor, events, adapter, fallbackUser, NER resolver,
  policy resolver)` — redacción de PII en los requests con resolución de
  `token_classify` (PII detection tier).
- Procesos backend con **estado y scratch propios** (`backendProcessRuntime`), y los
  backends Vulkan apuntan al loader a sus **ICD manifests empaquetados** (`VK_DRIVER_FILES`)
  en vez de depender del driver del host.

> PATRÓN interesante para N.O.V.A.: "legacy API keys → admin sintético" con comparación
> constante y `OpaqueErrors` (no filtrar qué hay mal). Y el principio de que el estado de
> carga de un 503 no exige credenciales.

---

## I. Patrones a adoptar / a evitar para N.O.V.A.

### Adoptar (decisiones de diseño)

| # | Patrón de LocalAI | Por qué para N.O.V.A. |
|---|---|---|
| 1 | **Un contrato gRPC único entre core y todo motor** (`backend.proto`) | Desacopla runtime de providers (hoy Ollama, mañana llama.cpp/vLLM/remoto). Un "adapter" por proveedor, todos con el mismo surface `Health/Load/Predict/PredictStream/Embedding`. |
| 2 | **Tres vocabularios separados**: usecases/bitmask ⇄ tabla por-backend (possible/default) ⇄ mapa declarativo capability→artefacto | En `nova/llm/router.py` hoy la decisión se toma por tarea/recursos; agregar el eje "qué sabe correr el proveedor" sin mezclarlo en el mismo struct. |
| 3 | **Default conservador vs possible** (`DefaultUsecases` vs `PossibleUsecases`), con declaración explícita (`known_usecases`) por modelo | Evita que un modelo GenGGUF "adoctrine" capacidades que no tiene (el caso vision #10945). |
| 4 | **Normalizar antes de decidir comportamiento** (`GetBackendCapability`, `NormalizeBackendName`, `IsLlamaCppBackend`) | IDs con alias/nombre de canal deben resolver a la misma regla o hay bugs endémicos. |
| 5 | **Fallos seguros de detección** (unknown → CPU/base, memoria desconocida → de-valorar, capability no en mapa → `default`) | En un runtime desktop la detección de GPU falla a menudo; el impacto debe ser "funciona más lento", nunca "explotó". |
| 6 | **Field de salud/LRU/memoria con `pinned` y `concurrency_group`** (`WatchDog`) | N.O.V.A. ya tiene ResourceManager; agregarle evicción LRU + reclaimer por umbral con modelos de memoria pinneables es el salto natural. |
| 7 | **Cooldown exponencial en cargas fallidas + 503 `Retry-After`** | Un desktop con modelo roto no debe spawnear un subproceso por cada request. |
| 8 | **Proceso por modelo con state-dir propio + tail de stderr** | Resiliencia a crash del motor y diagnóstico (hoy N.O.V.A. pierde ese detalle con subprocess de Ollama). |
| 9 | **Pin de variante persistido por modelo** (`._gallery_` con `ResolvedVariant/PinnedVariant`) | Decisión de "cómo se instaló" reproducible; y degradación suave si el catálogo cambia. |
| 10 | **Operaciones largas como jobs asíncronos con UUID** (`/models/jobs/:uuid`) | Instalar modelos puede durar minutos (download de GBs); hoy N.O.V.A. bloquea en HTTP sync. |
| 11 | **Errores en envelope del estándar + estado de carga** (503 model_loading) | Compatibilidad real con clientes OpenAI; y `/.well-known` self-description para agentes. |
| 12 | **Refcount en borrado** (no borrar archivos compartidos) | Evita romper otro modelo al desinstalar (patrón barato y concreto: `DeleteModelFromSystem`). |

### Evitar o replantear (riesgos detectados)

| # | Riesgo en LocalAI | Qué hacer en N.O.V.A. |
|---|---|---|
| 1 | **Registry en un monolitico** (`backend/index.yaml`, ~245 KB, un archivo único como fuente de verdad) | Muy acoplado al repo de un mantenedor. En N.O.V.A. prefiero catálogo versionado, particionado por familia, generado desde fuentes upstream. |
| 2 | **Registros duplicados en dos lugares** (tabla `BackendCapabilities` en Go + `backend/index.yaml`) | Des-sincronización silenciosa; se mitiga con tests de paridad pero el diseño lo invita. Elegir UNO (declarativo) y derivar el otro con generación + test de CI. |
| 3 | **Acoplamiento a un solo vendor**: `gpu_layers`/`top_k=40`/serving-options son de llama.cpp, inyectados como default global y "parcheados" con allow/deny lists (`nonLlamaSamplerBackends`, `UsesLlamaCppServingOptions`) | En N.O.V.A.: semántica neutral del runtime (p. ej. "offload GPU" en vez de "capa X"); opciones de vendor dentro del adapter del vendor, no en el contrato común. |
| 4 | **`GuessUsecases` heurístico con capacidad critical** | Nunca deducir visión desde el nombre del archivo; exigir declaración o probe real (mmproj / marker). |
| 5 | **Dependencia de fuentes raw de GitHub como origen de catálogo** (fetch de `gallery/index.yaml` a cada install) | Cachear con TTL + hash y permitir mirrors/registro privado de serie. |
| 6 | **Complejidad de soporte multimodal enorme** (video, 3D, face, voice...) con decenas de backends | La feature-creep cuesta mantenimiento; en un desktop local-first empezar por chat/embed/vision/TTS y dejar el "registry" listo para crecer, no crecido. |
| 7 | **Docs `gpu_layers` "auto-fit -1" documentada como problemática** | Si N.O.V.A. expone offload, la combinación con propio reclaimer de VRAM debe respetar el contrato del engine (lección del `tensor_buft_override`). |

### Conclusión operativa

LocalAI demuestra que un **core pequeño + registry declarativo + motores como procesos
independientes detrás de un gRPC** es escalable y resistente (la mayoría de sus bugs se
arreglaron con normalización y fallos seguros, no con más código). Para N.O.V.A. la jugada es:
mantener la capa `LLMProvider`/`ModelRouter` actual, **añadirle el eje usecases/capacidades
(§B-C) sobre el mismo contrato abstracto**, y **mover la instalación/descarga de modelos a
jobs asíncronos con status (§E)**. El adaptador "llama.cpp directo" (sin Ollama) es la
segunda integración más valiosa después de la actual, y el §D (auto-tuning + watchdog) solo
tiene sentido cuando exista esa integración.