# Decisiones / ADR

Formato ligero de "Architecture Decision Records". Cada decisión importante se documenta aquí.

## ADR-001 — Stack: Python 3.11 + FastAPI

- **Fecha:** 2026-09-06
- **Contexto:** el entorno ya tiene Python 3.11.9 con FastAPI, Pydantic 2, httpx y pytest instalados; además del ecosistema natural para LLMs (Ollama, embeddings).
- **Decisión:** backend en Python 3.11. FastAPI se añadirá en PHASE 4 (API), no antes, por principio de simplicidad.
- **Consecuencias:** Node 24 queda descartado como base del proyecto.
- **Estado:** aceptada (aprobada por el dueño del proyecto).

## ADR-002 — Arquitectura en capas: Inteligencia / Orquestación / Herramientas / Sistema

- **Fecha:** 2026-09-06
- **Contexto:** el LLM no debe tener control directo e ilimitado sobre el ordenador.
- **Decisión:** el LLM solo produce intenciones. Un `Orchestrator` las traduce en acciones propuestas, el `Permission System` decide, y las `Tools` ejecutan. Todo ejecución se registra.
- **Consecuencias:** las herramientas solo se escriben en PHASE 2; en PHASE 1 el Core no ejecuta nada sobre el sistema.
- **Estado:** aceptada. Es el principio fundamental del proyecto.

## ADR-003 — Desacoplamiento del proveedor LLM

- **Fecha:** 2026-09-06
- **Contexto:** Ollama es el proveedor inicial, pero N.O.V.A. no debe acoplarse a él.
- **Decisión:** interfaz `LLMProvider` (`chat`, `list_models`, `health`) + registro por nombre vía `nova.llm.registry`. Todo el código depende solo de la abstracción.
- **Consecuencias:** añadir un proveedor = implementar la interfaz + `registry.register(...)`. Nada en `core`/`cli` conoce a Ollama.
- **Estado:** aceptada.

## ADR-004 — Configuración externa, modelos nunca hardcodeados

- **Fecha:** 2026-09-06
- **Contexto:** la configuración del modelo debe estar separada del código.
- **Decisión:** `config/config.yaml` como fuente principal, overrides vía env `NOVA_*` (`NOVA_LLM_DEFAULT_MODEL`, `NOVA_LOGGING_LEVEL`, …). Tipado con Pydantic.
- **Consecuencias:** un nuevo despliegue solo cambia config/env, nunca el código.
- **Estado:** aceptada.

## ADR-005 — Simplicidad antes que herramientas distribuidas

- **Fecha:** 2026-09-06
- **Contexto:** tentación de adoptar Redis/Kafka/microservicios/vector DB/kubernetes desde el día 1.
- **Decisión:** prohibido introducirlos sin una necesidad real. Preferir SQLite sobre bases vectoriales externas en PHASE 3 (revisable cuando el tamaño lo exija).
- **Consecuencias:** menos infra, menos latencia, menos caos operativo.
- **Estado:** aceptada.

## ADR-006 — Proveedor que habla OpenAI-compatible con httpx (sin SDK "openai")

- **Fecha:** 2026-09-06
- **Contexto:** Ollama expone `/v1/chat/completions` (compatibilidad OpenAI). Añadir el SDK `openai` sería una dependencia más sin valor inmediato.
- **Decisión:** `OllamaProvider` usa `httpx` directamente contra esa API. El modelo de datos (roles/content) es el que todos los providers comparten.
- **Consecuencias:** sin dependencia externa innecesaria; si mañana se quiere el SDK oficial de algún proveedor, es un detalle interno del provider.
- **Estado:** aceptada.

## ADR-007 — Permission System: autonomía + reglas allow/deny, ejecución siempre auditada

- **Fecha:** 2026-09-06
- **Contexto:** PHASE 2 introduce la ejecución de herramientas; sin control, el LLM/el usuario podrían ejecutar cualquier acción sobre el sistema.
- **Decisión:** toda ejecución pasa por `PermissionSystem.authorize(tool)`. Nivel de autonomía por config: `off` (solo `allow`), `ask` (lo no regulado pregunta), `full` (solo `deny` bloquea). Precedencia: `deny` > `allow` > autonomía. Nada se ejecuta fuera de `ToolRunner`.
- **Consecuencias:** añadir una herramienta no la habilita sola; hay que configurar su regla en `config/config.yaml` -> `permissions`. El runner es el único punto que consulta permisos + audita.
- **Estado:** aceptada.

## ADR-008 — Audit log en JSONL local (rotativo), no una base de datos

- **Fecha:** 2026-09-06
- **Contexto:** se necesita un registro fiable de cada ejecución/decisión (quién, qué, argumentos, resultado, duración) para revisión y trazabilidad.
- **Decisión:** audit como archivo append-only de líneas JSON (`logs/audit.nova.jsonl`), rotación tipo RotatingFileHandler (2 MB x 3). `AuditLog.record` nunca lanza: un fallo de escritura se loggea y no interrumpe la ejecución.
- **Consecuencias:** sin dependencias nuevas. Si en PHASE 3 o PHASE 4 se necesita consulta/analítica pesada, se puede migrar a SQLite sin cambiar el runner.
- **Estado:** aceptada.

## ADR-009 — La selección de herramientas por el LLM se deja para Agents (PHASE 5)

- **Fecha:** 2026-09-06
- **Contexto:** PHASE 2 construye el sistema de herramientas y permisos. Hacer que el LLM decida qué herramienta llamar (function calling) añade complejidad de parsing/prompting y no es requisito del roadmap del Tool System.
- **Decisión:** en PHASE 2 el usuario invoca herramientas explícitamente (CLI `/run`) o vía API Python (`ToolRunner.run`). La orquestación automática intención->herramienta llega con los Agents (PHASE 5).
- **Consecuencias:** el Core de herramientas queda listo y testeable sin depender de un LLM; cuando lleguen los Agents solo habrá que conectar su salida a `ToolRunner.run`.
- **Estado:** aceptada.

## ADR-010 — Memoria en SQLite local + embeddings del proveedor con fallback a keywords

- **Fecha:** 2026-09-07
- **Contexto:** PHASE 3 necesita persistir conversación y hechos y recuperar contexto relevante. ADR-005 prohibe vector DB externas sin necesidad real.
- **Decisión:** `MemoryStore` sobre **SQLite** (`memory/nova.db`, stdlib `sqlite3`) con dos tablas (`memories`, `transcripts`). Los embeddings (`LLMProvider.embed_text` vía `/api/embed`, `nomic-embed-text`) se guardan como JSON por fila y la recuperación rankea por **similitud coseno** en Python (O(n) por consulta, suficiente a esta escala). Si el proveedor no soporta embeddings o fallan, se cae a **keywords**; la memoria nunca interrumpe el diálogo.
- **Consecuencias:** cero dependencias nuevas; migrar a una vector DB real en el futuro solo toca `MemoryRetriever`. El contexto recuperado se inyecta como mensaje `system` antes del turno del usuario.
- **Estado:** aceptada.

## ADR-011 — API REST stateless + sesiones con estado del Core por petición

- **Fecha:** 2026-09-07
- **Contexto:** PHASE 4 necesita exponer N.O.V.A. como servicio (API + web) sin duplicar la lógica del Core.
- **Decisión:** FastAPI en `nova/api` (ADR-001 lo fijaba para esta fase). Dos modos: `POST /v1/chat` stateless (mensajes completos, passthrough al provider) y `POST /v1/sessions/{id}/chat` que reutiliza `ChatSession` + `MemoryService` + `ToolRunner` (una sesión de API = un `ChatSession` + una memoria con `session_id` propio). El contexto se inyecta igual que en el CLI. La API nunca interactúa en vivo: un `ASK` se deniega (confirm = False).
- **Consecuencias:** añadir un cliente (web, escritorio, script) = llamar a la API; la lógica (permisos, audit, memoria) sigue viviendo en el Core y se testea con `fastapi.testclient` inyectando un provider falso.
- **Estado:** aceptada.

## ADR-012 — Agents como presets paramétricos con protocolo de tool-call por JSON estructurado

- **Fecha:** 2026-09-07
- **Contexto:** PHASE 5 materializa ADR-009 (el LLM selecciona herramientas). El function calling nativo difiere según proveedor (OpenAI/Ollama) y OpenAI SDK está prohibido (ADR-006), así que hay que elegir cómo el modelo propone herramientas.
- **Decisión:**
  1. **Protocolo de tool-call por JSON estructurado** en lugar de function calling nativo: el LLM responde SOLO con un objeto JSON `{"tool": "<name>", "args": {...}}` (tolerante a code fences) para proponer una llamada, o texto plano como respuesta final. Es provider-agnóstico y testeable con fakes.
  2. **Agentes como presets paramétricos** — una única clase `Agent` (loop acotado por `max_steps`) instanciada con un `AgentPreset` (name/description/system_prompt/tool_names); sin subclases. Los 5 agentes (`general`, `coding`, `research`, `system`, `automation`) son config, no herencia.
  3. El agente inyecta en el system prompt su perfil + schemas JSON de sus tools (`BaseTool.json_schema()`), alimenta cada resultado como mensaje `tool`, y delega TODA ejecución al `ToolRunner` (Permission System + audit): el LLM **propone**, N.O.V.A. **decide**.
- **Consecuencias:** nuevo módulo `nova.agents` reutilizando `ChatSession`, `MemoryService` y `ToolRunner` (sin duplicar Core); `create_agent`/`agent_presets` para el CLI/API/web. Añadir un agente = añadir un `AgentPreset`. El bucle de steps está acotado para evitar bucles infinitos; el `max_steps` se expone al instanciar.
- **Estado:** aceptada.

## ADR-013 — Local-first, coste cero y privacidad como restricción arquitectónica

- **Fecha:** 2026-09-07
- **Contexto:** la visión de producto exige que N.O.V.A. sea usable sin APIs de pago; el coste de inferencia debe recaer en el dispositivo del usuario cuando sea posible, y en infraestructura gratuita/open source cuando la modalidad remota lo requiera.
- **Decisión:** N.O.V.A. es **local-first**:
  1. El proveedor principal es `ollama` (modelos locales/OSS); la interfaz `LLMProvider` ya lo permite.
  2. El Core **nunca depende** de servicios de pago. Cualquier API cloud es **integración opcional** que solo se activa si el usuario la configura explícitamente y que degrada con elegancia (el modelo diario sigue siendo local).
  3. Eficiencia como requisito: el **Model Router** (PHASE 7) seleccionará modelo por tarea y por capacidad del dispositivo (RAM/VRAM/CPU/GPU) para no derrochar recursos.
  4. Privacidad por defecto: datos locales; nada abandona el dispositivo sin consentimiento; sin telemetría; secretos fuera de Git.
- **Consecuencias:** cualquier funcionalidad futura (voz, web tools, desktop agent, acceso remoto) se diseña priorizando OSS/gratuito; si algo requiere infraestructura que cuesta dinero, primero se busca alternativa gratuita y, si no existe, se documenta como limitación y se convierte en opcional. Los ADR futuros de cada fase deben alinearse con esta restricción.
- **Estado:** aceptada.

## ADR-014 — Model Router: la selección de modelo es decisión del Core, no del LLM

- **Fecha:** 2026-09-07
- **Contexto:** PHASE 7 materializa la eficiencia prevista en ADR-013. Un único modelo por defecto no aprovecha los recursos disponibles ni respeta la privacidad según el tipo de tarea; dejar que el LLM elija su propio modelo rompería la separación "el LLM propone, N.O.V.A. decide".
- **Decisión:**
  1. **`ResourceManager`** (`nova/llm/resources.py`): snapshot best-effort de RAM (total/disponible), CPU, GPU/VRAM opcional y batería. Cada reader es un callable inyectable; nunca lanza y degrada a valores conservadores (ADR-005, sin dependencias pesadas tipo psutil).
  2. **Catálogo de modelos** en `config/config.yaml` -> `llm.models` con roles `small`/`local`/`coding`/`vision`/`embedding`. Roles vacíos caen a `default_model` (ADR-004: nombres en config, nunca en código).
  3. **`ModelRouter`** (`nova/llm/router.py`): clasifica la tarea (`simple`, `coding`, `vision`, `heavy`, `general`) por regex de pistas y elige modelo por complejidad + recursos + privacidad. Con RAM disponible < `model_router.min_ram_gb` o batería < 20 % sin AC (si `battery: true`) hace *downshift* de tareas pesadas/coding a `small`.
  4. Cloud es un rol opcional: `model_router.cloud_enabled` es `False` por defecto (ADR-013); nunca se depende de él.
- **Consecuencias:** el routing se aplica por turno en CLI, `nova-agent` y API (`/v1/route` + sesiones); sigue funcionando el `model` explícito (lo respeta). El sistema degrada con elegancia si el rol no está configurado. Añadir un criterio (latencia, modelo privado) = lógica nueva en `route_for` sin tocar `LLMProvider`.
- **Estado:** aceptada.

## ADR-015 — Host tools remotas solo con token; la API nunca se expone sin auth/TLS

- **Fecha:** 2026-09-07
- **Contexto:** PHASE 8 permite acceder al Desktop Agent desde fuera (móvil -> API -> host). Las host tools (`open_app`, `open_url`, `run`, archivos) ejecutan código y tocan el sistema del usuario; exponerlas a la red sin control supondría una puerta abierta al host. El resto del stack ya es local-first (ADR-013) y "el LLM propone, N.O.V.A. decide" (ADR-007).
- **Decisión:**
  1. **Auth obligatoria**: `api.token` (Bearer) con `api.host_enabled: true`. `create_app` **lanza `ValueError`** si `host_enabled` está activo sin token; cada ruta `/v1/*` devuelve 401 sin el header `Authorization: Bearer <token>`. `healthz` y `/` quedan abiertos.
  2. **CORS de origen**: `api.cors_origins` (defecto `"*"`) permite servir un frontend estático desde otro origen; el token se envía desde el navegador (localStorage), nunca en la URL.
  3. **El LLM nunca corre en serverless**: el frontend es un cliente estático; el backend (LLM + memoria + tools) vive siempre en el dispositivo. Para el hosting gratuito solo se publica el cliente.
  4. **Sin confirmación humana en la API**: un `ask` se deniega (`result.ok == false`); solo corre lo que esté explícito en `permissions.allow`. Recomendado `autonomy: full` solo con `host.commands`/`host.roots` explícitos y auditado.
  5. **TLS por proxy reverso**: exponer la API a Internet requiere TLS delante (p. ej. Caddy/nginx/Cloudflare). Sin TLS, la API no se publica — ver `docs/security.md`.
- **Consecuencias:** las host tools se listan en `GET /v1/tools` y se inyectan en las sesiones de la API solo con `host_enabled`. El frontend se sirve igual desde la propia API (`/`) — misma origin, sin CORS — o estático con `?api=<base>`. Tests en `tests/test_api_auth.py`.
- **Estado:** aceptada.

## ADR-016 — Web Tools: internet solo vía herramientas controladas (separación LLM / Web)

- **Fecha:** 2026-09-07
- **Contexto:** PHASE 9 da al asistente acceso a internet (búsqueda, lectura y extracción). El stack ya es local-first (ADR-013) y "el LLM propone, N.O.V.A. decide" (ADR-007). Dar al modelo un cliente HTTP genérico rompería esa separación y permitiría tráfico arbitrario sin barreras (sin robots, sin rate limit, sin caps) y sin visibilidad en el audit.
- **Decisión:**
  1. **Sin primitivas de red en el Core**: la única salida es `nova/tools/web/client.py::WebClient`, usado por las three tools `web_search`, `web_fetch`, `web_extract`. Cada petición pasa por orden fijo: validación de URL (solo `http(s)`, con host, sin userinfo) -> `robots.txt` (respecto por defecto) -> rate limit por host -> caps de bytes/caracteres -> User-Agent identificable.
  2. **Off por defecto**: `web.enabled: false` no registra nada. Con `true` siguen bajo el `PermissionSystem` (denegadas hasta `permissions.allow` o confirmación `ask`) y el audit de `ToolRunner`.
  3. **Búsqueda pluggable sin API key**: `web.search_url` es plantilla con `{query}`; por defecto DuckDuckGo HTML. Para producción, un SearXNG self-hosted o la Brave Search API free tier sustituyen el endpoint sin cambiar la interfaz de la tool.
  4. **Robots y cortesía**: parser RFC-9309 (Allow/Disallow, prefijo más largo gana) cacheado por host; `min_delay_s` entre peticiones al mismo host; `max_redirects`/`max_bytes`/`max_chars` acotan el impacto.
- **Consecuencias:** el LLM propone query/URL; las Web Tools ejecutan con sus límites; `ToolRunner` decide y audita. Cualquier fetch se ve en `logs/audit.nova.jsonl` como cualquier tool. Tests en `tests/test_web_tools.py` (fakes con `httpx.MockTransport`).
- **Estado:** aceptada.

## ADR-017 — Voz 100% local: el audio jamás sale del dispositivo

- **Fecha:** 2026-09-07
- **Contexto:** PHASE 10 añade voz (escuchar y hablar). Hay APIs de pago maduras, pero violan el compromiso local-first/coste cero (ADR-013). Además, el micrófono captura conversaciones privadas; enviarlas a un tercero rompería la privacidad garantizada por ADR-013 y sorprendería al usuario.
- **Decisión:**
  1. **STT y TTS locales y OSS**: `VoskSTT` (modelo offline descargado una vez por el usuario) y `Pyttsx3TTS` (voces del sistema). **Sin APIs de pago ni servidores**: N.O.V.A. nunca sube audio grabado.
  2. **Off por defecto y extra opcional**: `voice.enabled: false`; las dependencias viven en el extra `[voice]` y se importan **lazy** — instalar o no voz no cambia el arranque ni rompe N.O.V.A.
  3. **Abstracción inyectable**: contratos `STTProvider`/`TTSProvider`/`AudioSource` + factory `build_voice`; los tests usan fakes (sin micrófono ni modelos). Un backend alternativo futuro (p. ej. whisper.cpp) se añade sin tocar el pipeline.
  4. **La voz entra por el mismo chat**: `chat_line(text)` compartido — la voz solo origina texto; memoria, router, tools y audit del teclado aplican idénticos.
- **Consecuencias:** el CLI gana `/voice` y `/say`; el bucle es local (un futuro uso remoto móvil->API requeriría exponer audio y se decidiría aparte, con el consentimiento explícito del ADR-013). Tests en `tests/test_voice.py`.
- **Estado:** aceptada.
## ADR-018 — Plugins: el plugin amplía tools, nunca los permisos

- **Fecha:** 2026-09-07
- **Contexto:** PHASE 11 quiere que terceros (o el propio usuario) amplíen N.O.V.A. sin reescribir el Core. El roadmap exige evitar overengineering (primero la interfaz, después plugins concretos). Podría caerse en un sistema de plugins que ejecute código arbitrario al margen de la seguridad (ADR-002/ADR-007).
- **Decisión:**
  1. **Contrato mínimo `Plugin`** (`nova/plugins/base.py`): `name`/`description` + `tools() -> list[BaseTool]`. Sin dependencias nuevas; los plugins entregan herramientas ya existentes de `BaseTool` (schemas Pydantic + validación + `ToolResult`).
  2. **Carga controlada** (`nova/plugins/loader.py`): built-ins por nombre en `plugins.enabled` y directorio opcional `plugins.dir` cuyos `*_plugin.py` exponen un objeto `PLUGIN`. Un plugin que falla (import, contrato, tools) se registra en el log y se **omite sin romper el arranque**.
  3. **El plugin nunca bypasea seguridad**: las tools del plugin se registran en el mismo `ToolRegistry` y pasan por el mismo `ToolRunner`/`PermissionSystem`/audit que cualquier tool del Core. Una tool de plugin puede denegarse, requerir confirmación o fallar validación como cualquier otra.
  4. **Off por defecto**: `plugins.enabled` vacío + `dir` nulo = nada se carga. Los plugins de ejemplo (`text_tools`, `units`) son puros y sin APIs externas (local-first, ADR-013).
- **Consecuencias:** CLI (`/plugins`), `nova-agent` y API (`GET /v1/plugins` + tools en sesiones) exponen lo que carguen los plugins bajo el mismo flujo propone->decide->ejecuta->audita. Hooks de ciclo de vida (setup/teardown) y plugins reales (spotify/vscode/home-assistant/discord) se añaden cuando haya demanda concreta, sin cambiar el contrato base. Tests en `tests/test_plugins.py`.
- **Estado:** aceptada.

## ADR-019 — Automatización: el scheduler amplía el horario, nunca los permisos

- **Fecha:** 2026-09-07
- **Contexto:** PHASE 12 añade tareas programadas (scheduler) y workflows multi-paso. Un task desatendido ejecuta sin que nadie esté delante para confirmar `ask`; podrías caer en (a) correr con `autonomy: full` en nombre de la automatización (pierde la capa de decisión de ADR-007) o (b) bloquear esperando una confirmación interactiva que nunca llega (rompe la automatización).
- **Decisión:**
  1. **El `Scheduler` decide cuándo, el `ToolRunner` decide qué**: el scheduler solo elige el momento (`interval_s`/`at`) y llama a un executor; toda ejecución pasa por el mismo `ToolRunner` + `PermissionSystem` + audit que el chat manual (ADR-002).
  2. **Runner desatendido, sin confirmación**: la automatización usa un `ToolRunner` dedicado con `confirm` denegadora — un `ask` en modo desatendido se deniega de forma segura. Para automatizar una tool hay que listarla en `permissions.allow` (o tener `autonomy: full`).
  3. **Workflows multi-paso con `on_error`**: pasos `tool`/`agent`/`workflow` anidado en orden; por defecto `stop` en el primer fallo, `continue` para seguir. Guarda de profundidad contra ciclos.
  4. **Siempre auditado y observable**: cada tool de un task/workflow queda en el audit log; el estado del scheduler es consultable (`/automation`, `GET /v1/automation`).
- **Consecuencias:** CLI (`/automation`, `/workflows`, `/workflow <name>`, scheduler en segundo plano si `automation.enabled`), `nova-agent` (igual + fix del `run_llm` inexistente) y API (`GET /v1/automation`, `POST /v1/automation/tasks|workflows/{name}/run`). Off por defecto. La compaction/resumen de memoria queda fuera de alcance (fase futura). Tests en `tests/test_automation.py`.
- **Estado:** aceptada.
