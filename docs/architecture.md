# Arquitectura

## Principio

N.O.V.A. separa estrictamente:

- **INTELIGENCIA** — el LLM (propuesta de intención).
- **ORQUESTACIÓN** — el `Orchestrator` (traduce intención en acciones).
- **HERRAMIENTAS** — las `Tools` (capacidades: archivos, comandos, web…).
- **SISTEMA** — el ordenador real.

El LLM **propone**; el sistema **decide**. El `Permission System` media entre ambos y registra todo.

```
Usuario
  |
  v
N.O.V.A. (Core: contexto, sesión, telemetría)
  |
  v
LLM Provider (Ollama hoy; otros proveedores mañana)
  |
  v
Orchestrator (traduce intención -> acción propuesta)
  |
  v
Permission System (¿permitido? según nivel de autonomía)
  |
  v
Tool (schema + validación + ejecución + resultado)
  |
  v
Sistema      -> Resultado -> N.O.V.A. -> Usuario
```

## Capas actuales (PHASE 1 + 2 + 3 + 4 + 5 + 6-paso1)

| Módulo | Responsabilidad |
|---|---|
| `nova.core.config` | Carga de configuración (YAML + env `NOVA_*`), tipada con Pydantic. |
| `nova.core.logging` | Logging estructurado: consola + archivo rotativo. |
| `nova.core.session` | Contexto de conversación: historial acotado + system prompt (+ `set_system_prompt`/`add_tool` para agents). |
| `nova.core.audit` | Audit log de ejecuciones de herramientas (`logs/audit.nova.jsonl`, JSON lines rotativo). |
| `nova.llm.base` | Interfaz `LLMProvider` (chat, listado, health, embeddings) + tipos `ChatMessage`, `ChatCompletionRequest/Response`, `NOVAProviderError`. |
| `nova.llm.ollama` | `OllamaProvider`: API compatible OpenAI (`/v1/chat/completions`), `/api/tags` y embeddings (`/api/embed`). |
| `nova.llm.registry` | Registro de proveedores por nombre; `create_provider` es la única fábrica usada por todo el código. |
| `nova.llm.resources` | `ResourceManager` (PHASE 7): snapshot best-effort de RAM/CPU/GPU/batería con readers inyectables. |
| `nova.llm.router` | `ModelRouter` (PHASE 7): clasifica la tarea y elige modelo por complejidad + recursos + privacidad; `build_router` para el wiring. |
| `nova.memory.store` | `MemoryStore`: SQLite local (hechos + trascripción de conversación, embeddings opcionales). |
| `nova.memory.retriever` | Recuperación por similitud coseno sobre embeddings, con fallback a keywords. |
| `nova.memory.service` | `MemoryService`: fachada `remember`/`record`/`search`/`context` para el CLI, la API y los Agents. |
| `nova.memory.tools` | Herramientas `remember` y `memory_search` (bajo el Permission System). |
| `nova.tools.base` | `BaseTool` (schema Pydantic + `execute` + `json_schema()`), `ToolResult`, `ToolError`. |
| `nova.tools.standard` | Herramientas de ejemplo: `calculate`, `date_time`, `list_dir`. |
| `nova.tools.host` | Host tools seguro (PHASE 6): `open_app`, `open_url`, `run` — allowlist de comandos y apps configurables, denegadas por defecto. |
| `nova.tools.host.files` | Tools de archivos acotadas por `host.roots`: `read_file`, `write_file`, `list_files`. |
| `nova.tools.host.paths` | `PathBounds`: reduce y comprueba que cada ruta toca solo dentro de `host.roots` (evita escapes `../`, symlinks). |
| `nova.tools.web` | Web Tools controladas (PHASE 9): `web_search`, `web_fetch`, `web_extract` — internet solo vía `WebClient` (URL http(s) -> robots.txt -> rate limit -> caps), off por defecto. |
| `nova.tools.registry` | Registro de herramientas por nombre. |
| `nova.tools.permissions` | `PermissionSystem`: autonomía (`off`/`ask`/`full`) + reglas allow/deny. |
| `nova.tools.runner` | `ToolRunner`: permiso -> validación -> ejecución, auditando cada paso. |
| `nova.agents.core` | `Agent`: loop acotado (`max_steps`) que propone tools por JSON estructurado, delega en `ToolRunner` y devuelve `AgentResult` con steps. |
| `nova.agents.presets` | `AgentPreset` + 5 presets (`general`, `coding`, `research`, `system`, `automation`) + `create_agent`. |
| `nova.desktop.agent` | Proceso local `nova-agent` (base del Desktop Agent, PHASE 6). |
| `nova.api.app` | API REST (FastAPI): `create_app` reutiliza el Core; sesiones con memoria y tools por petición, agentes persistentes por nombre. |
| `nova.api.server` | Entry point `nova-api`: levanta `uvicorn` con `APISettings`. |
| `nova.voice` | Voice local (PHASE 10, ADR-017): `VoiceSession` (captura -> STT -> wake word -> chat -> TTS), backends OSS `VoskSTT`/`Pyttsx3TTS`/`SoundDeviceSource` detrás de contratos inyectables; off por defecto y extra `[voice]`. |
| `nova.cli.chat` | Shell de conversación interactiva (chat + memoria + `/tools` + `/run` + `/agents` + `/agent`; incluye las host tools y voz `/voice`/`/say`). |

## Desacoplamiento del proveedor LLM

El resto del código **nunca** conoce a Ollama: solo depende de `nova.llm.base.LLMProvider` y del settings `llm.provider`. Añadir un proveedor = implementar la interfaz + `registry.register("nombre", Clase)`. Ver [decisions.md](decisions.md) ADR-003.

## Flujo de una conversación

1. `nova.cli.chat` construye la sesión y pide entrada al usuario.
2. `ChatSession` añade `user`, acota el historial y compone `ChatCompletionRequest`.
3. `nova.memory` persiste el turno y recupera la memoria relevante (`MemoryService.context`), inyectada como mensaje `system` antes del turno del usuario (si la hay).
4. `create_provider(settings.llm)` entrega el proveedor configurado.
5. El proveedor envía la petición y devuelve `ChatCompletionResponse`.
6. La respuesta se añade a la sesión, se persiste en la memoria y se muestra; todo se loggea.

> Si el proveedor soporta embeddings (`supports_embedding`), la recuperación usa similitud coseno sobre `nomic-embed-text`; si no, cae a keywords. La memoria nunca bloquea el chat.

## Ejecución de una herramienta (`ToolRunner.run`)

1. `nova.tools.registry` localiza la herramienta por nombre.
2. `PermissionSystem.authorize(name)` decide: `ALLOW`, `DENY` o `ASK` (deny > allow > autonomía).
3. Si `ASK`, el `confirm` callback pregunta al usuario (p. ej. en el CLI: `[y/N]`).
4. `BaseTool.validate` valida los argumentos contra el schema Pydantic.
5. `BaseTool.execute` ejecuta y devuelve un `ToolResult` tipado; los errores se envuelven como `failure`.
6. `nova.core.audit` registra cada ejecución y decisión en `logs/audit.nova.jsonl`.

```
Usuario -> (/run manual o /agente vía Agent)
  v
ToolRunner
  v
PermissionSystem (allow / deny / ask)
  v
BaseTool.validate (schema Pydantic)
  v
BaseTool.execute -> ToolResult
  v
AuditLog (JSON lines) + Resultado -> Usuario
```

## Agentes (PHASE 5)

Los agentes son una única clase `Agent` (loop acotado por `max_steps`) instanciada con un
`AgentPreset` (perfil + system prompt + conjunto de tools). El LLM **propone** tool-calls con
**JSON estructurado** (`{"tool": "<name>", "args": {...}}`, tolera code fences); N.O.V.A.
**decide** delegando en el `ToolRunner` (Permission System + audit) y alimenta los resultados
como mensajes `tool` hasta que el modelo responde en texto plano o se agota el presupuesto.
Cada paso queda registrado en `AgentResult.steps`.

```
Usuario -> Agent.act(texto)
  v
Agent(session + memoria + tools prompt en system)
  v
LLMProvider (responde texto plano O {"tool":..., "args":...})
  v
parse_tool_call -> ToolRunner.run (permisos + audit)
  v
resultado -> mensaje "tool" -> vuelve al LLM (máx. max_steps)
  v
respuesta final -> se persiste en memoria -> AgentResult
```

Los 5 presets (`general`, `coding`, `research`, `system`, `automation`) en `nova.agents.presets`
comparten el `MemoryService` y el `ToolRunner`: inyección de contexto y registro de conversación
igual que el chat normal. Añadir un agente = añadir un `AgentPreset` (ver ADR-012).

## API REST y web (PHASE 4)

`nova-api` sirve la web y la API. Cada sesión de API lleva un `ChatSession` + un `MemoryService` (mismo `MemoryStore`, `session_id` propio) + su `ToolRunner`:

```
Web / cliente -> FastAPI (nova.api.app)
  |-> POST /v1/sessions/{id}/chat -> ChatSession + MemoryService + Provider (misma lógica que el CLI)
  |-> POST /v1/sessions/{id}/run   -> ToolRunner (Permission System + audit)
  |-> POST /v1/chat                -> Provider directo (stateless)
```

El contexto recuperado se inyecta igual que en el CLI. En la API un `ASK` se deniega (no hay confirmación interactiva); lo que esté en `permissions.allow` corre directo.

## API REST, web y agents (PHASE 4 + 5)

`nova-api` sirve la web y la API. Cada sesión de API lleva un `ChatSession` + un `MemoryService` (mismo `MemoryStore`, `session_id` propio) + su `ToolRunner`, opcionalmente vinculada a un agente:

```
Web / cliente -> FastAPI (nova.api.app)
  |-> POST /v1/sessions/{id}/chat -> ChatSession + MemoryService + Provider (misma lógica que el CLI)
  |-> POST /v1/sessions/{id}/run   -> ToolRunner (Permission System + audit)
  |-> POST /v1/chat                -> Provider directo (stateless)
  |-> GET /v1/agents               -> presets disponibles
  |-> POST /v1/agents/{name}/chat  -> Agent persistente por nombre (sesión + memoria + tools)
  |-> POST /v1/sessions {agent}    -> sesión ligada a un agente (responde con `steps`)
```

Crear una sesión con `{"agent": "research"}` hace que `/chat` use `Agent.act` y devuelva los `steps`
de cada tool además de la respuesta. En la web, el selector de agente decide con qué presets habla la sesión.

## Host tools (PHASE 6, paso 1)

Las host tools (`nova.tools.host`) siguen exactamente la misma ruta que cualquier otra herramienta —
**LLM propone -> Permission System decide -> `ToolRunner` ejecuta -> Audit registra** — pero añaden una
segunda barrera de seguridad **independiente del Permission System**:

1. `open_app` — el LLM aporta solo el **nombre** (`app`); la ruta/ejecutable vive en `host.apps` del
   `config.yaml`. Nombre no configurado = fallo. Nunca una ruta arbitraria del modelo.
2. `open_url` — la URL debe ser `http`/`https` con host válido; cualquier otro esquema
   (`file:`, `javascript:`, `data:`, `ftp:`, `ssh:`...) se rechaza antes de abrir el navegador.
3. `run` — permite **solo** comandos listados en `host.commands` (la base del comando debe coincidir,
   comparendo sin `.exe` y sin distinción de mayúsculas). Se valida esto **aunque `autonomy: full`**:
   la allowlist es la barrera del host, no la autonomía. Además:
   - Se ejecuta **sin shell** (`subprocess` con lista de argumentos; sin inyección).
   - Timeout configurable (`host.timeout_s`, override por llamada `timeout_s`).
   - Captura controlada de stdout/stderr (cap 100 KB por stream) + `returncode`.
   - **Bloqueo duro** (no configurable vía allowlist) de comandos de elevación o destructivos:
     `runas`, `sudo`, `gsudo`, shells (`cmd`, `powershell`, `bash`, `wsl`...), `format`, `diskpart`,
     `shutdown`, `reg`, etc.
   - Sin elevación de privilegios (no se usan `runas`/UAC desde la herramienta).
   - El `cwd` (por llamada o `working_dir`) debe estar dentro de `host.roots`; sin roots se rechaza.
4. Tools de archivos (`read_file`, `write_file`, `list_files`) — **acotadas por `host.roots`**: toda
   ruta se resuelve con `.resolve()` (normaliza `..`, symlinks/junctions) y debe quedar dentro de un
   root. `roots` vacío = **sin acceso al FS**. `write_file` no crea directorios. Con estos límites,
   el LLM nunca toca el sistema de archivos entero, solo las carpetas concedidas.

```
CLI / nova-agent
  v
ToolRunner  (permisos: allow / deny / ask [y/N])
  v
Host tool:
  open_app -> ¿name en host.apps? -> Popen([ejecutable])
  open_url -> ¿scheme http/https y host? -> webbrowser.open(url)
  run      -> ¿comando en host.commands y no bloqueado? -> subprocess(lista, timeout) -> stdout/stderr
              (cwd dentro de host.roots)
  read/write/list -> ¿ruta dentro de host.roots? -> IO acotado (caps)
  v
AuditLog (JSON lines) + Resultado -> Usuario
```

- Las host tools se registran en CLI y `nova-agent` (proceso local), y en la API solo con
  `api.host_enabled: true` bajo token Bearer obligatorio (PHASE 8, ADR-015).
- Config mínima en `config/config.yaml` -> `host`: `apps` (nombre -> ejecutable), `commands` (allowlist),
  `timeout_s`.
- Referencias: [security.md](security.md) y [tools.md](tools.md).

## Web Tools (PHASE 9) — separación LLM / Web

El LLM **no tiene primitivas de red**: todo tráfico sale por `WebClient`, compartido por las tres tools
(vía el contenedor `WebTools`), con el mismo flujo propone->decide->ejecuta->audita:

```
LLM propone query/URL
  v
ToolRunner  (permisos: allow / deny / ask -> denegado en API)
  v
Web Tool:
  web_search -> validación URL -> robots.txt -> rate limit -> DDG/SearXNG/Brave (search_url) -> títulos/URLs/snippets
  web_fetch  -> validación URL -> robots.txt  -> rate limit -> descarga (max_bytes, redirects, timeout) -> texto (max_chars)
  web_extract-> (igual) -> enlaces (texto + URL, max_links)
  v
AuditLog (una línea por petición) + Resultado -> LLM
```

Cada petición pasa por un orden fijo de barreras en `nova.tools.web.client`:

1. Validación estricta de URL: solo `http(s)`, con host, **sin userinfo**.
2. `robots.txt` por host (RFC-9309), si `web.respect_robots` (true).
3. Rate limit por host (`web.min_delay_s`) — el rate limiter es **global** (compartido por las tres tools).
4. Caps: `web.max_bytes` corta la descarga; `web.max_chars` acota la salida; `web.timeout_s` + `web.max_redirects`.
5. User-Agent identificable (`NOVA/1.0 ...`).

El registro lo hace `all_web_tools(settings.web)`, que devuelve `[]` cuando `web.enabled: false` (off por
defecto). Integración: CLI y `nova-agent` (`BANNER`/HELP y registry `memory + host + web`), y API
(`GET /v1/tools` + sesiones) siempre bajo el Permission System y audit. La búsqueda es pluggable
(`web.search_url` con `{query}`; DuckDuckGo HTML por defecto, sin API key). Detalle en [tools.md](tools.md)
y política en [security.md](security.md).

## Voice (PHASE 10) — ADR-017

La voz es **100% local** (el audio jamás sale del dispositivo) y entra por el **mismo chat** que el
teclado: solo origina texto.

```
mic (SoundDeviceSource, hilo de fondo)  --PCM16 --> VoskSTT (offline) --> texto
   texto -> chat_line()  (memoria + router + tools + audit, igual que teclado)
   respuesta -> Pyttsx3TTS (voces del sistema) -> altavoz
```

- `VoiceSession` (nova/voice/pipeline.py): `listen_once()` captura en un hilo mientras `wait_fn` bloquea
  (en el CLI, "pulsa ENTER al terminar"), concatena los chunks y devuelve el transcript ya aplicado el
  wake word opcional (`voice.wake_word`, match case-insensitive por palabra inicial). `say(text)` habla.
- **Off por defecto** (`voice.enabled: false`, `build_voice` -> `None`); dependencias en el extra
  `[voice]` con imports lazy: `available()`/`status()` permiten degradar (mic/stt/tts) sin romper.
- Backends detrás de contratos inyectables (`STTProvider`/`TTSProvider`/`AudioSource`): los tests usan
  fakes. La voz no tiene permisos propios ni nueva superficie de tools: `chat_line` es compartido.
- CLI: `/voice` (bucle, ENTER al terminar, `/voice stop`) y `/say <texto>`. Política en
  [security.md](security.md).

## Arquitectura objetivo (evolución incremental)

La visión de producto mapea sobre esta estructura sin saltos de arquitectura:

```
                     N.O.V.A.
                        │
                 N.O.V.A. Core  (orquestación, contexto, sesión, audit)
                        │
         ┌──────────────┼──────────────┐
         │              │              │
      Memory          Agents         Tools
         │              │              │
         └──────────────┼──────────────┘
                        │
                  Model Router  (PHASE 7)
                        │
          ┌─────────────┼─────────────┐
          │             │             │
       Ollama       Other Local    Optional
       Models        Providers      Cloud APIs
                                      │
                             SOLO si el usuario lo configura (ADR-013)
```

Estado de cada pieza hoy: **Memory** ✅ (PHASE 3, SQLite + embeddings), **Agents** ✅ (PHASE 5),
**Tools + Permission System + audit** ✅ (PHASE 2), **Model Router + Resource Manager** ✅ (PHASE 7),
**Cloud** ❌ no está presente ni se asume — el Core funciona 100% local y gratuito. La selección de
modelo es decisión del Core (`ModelRouter`), nunca del LLM (ADR-014).

El **Desktop Agent** (PHASE 6) conecta la misma ruta `Usuario -> Core -> Permission System -> Tool`
desde el ordenador del usuario (host), reutilizando el `ToolRunner` existente; en PHASE 8 el móvil
accede a través de la API (`api.host_enabled` + token) con el Desktop Agent como brazo de ejecución
del host.

## Caminos futuros (incremental)

- **PHASE 10-12** — Voice, plugins y automatización avanzada. Detalle en [roadmap.md](roadmap.md).

## Restricciones de diseño

- Sin Redis/Kafka/microservicios/vector DB hasta que exista una necesidad real (SQLite + embeddings locales cubren PHASE 3 — ADR-005/ADR-010; FastAPI se añadió en PHASE 4 — ADR-011).
- Arquitectura sencilla que funcione, antes que enorme que no lo haga.
- La configuración de modelos vive en `config/config.yaml` (o env), nunca en código.
- Las herramientas solo se ejecutan tras la decisión explícita o permitida del `PermissionSystem`; toda ejecución queda auditada.
- La memoria nunca interrumpe el diálogo: si el embedding falla o no existe, se degrada a keywords.
- La API (y la web) solo hablan con el Core: nunca conocen los detalles de Ollama ni de las herramientas.
- Los Agents son config, no herencia: una clase `Agent` + presets paramétricos (ADR-012). El LLM propone, el `ToolRunner` decide y audita.