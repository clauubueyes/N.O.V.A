# Changelog

El formato sigue [Keep a Changelog](https://keepachangelog.com/es/1.1.0/) y sigue versionado semántico.

## [Unreleased]

Pendientes del análisis estratégico aún no implementados:

- Limpieza de los 2 warnings de deprecación en tests (starlette/httpx y anyio).
- Alinear/reducir `requirements.txt`/`requirements-dev.txt` con `pyproject.toml`.

## [0.8.0] - 2026-09-07

### Añadido — PHASE 8: Acceso remoto + auth + frontend

- **Auth Bearer en la API** (`nova/api/app.py`): con `api.token` configurado (o `NOVA_API_TOKEN`), toda ruta `/v1/*` exige `Authorization: Bearer <token>` (401 si falta/es inválida). `healthz` y `/` quedan abiertos.
- **CORS configurable**: `api.cors_origins` (defecto `"*"`) + `CORSMiddleware` para servir un frontend desde otro origen/hosting.
- **Vínculo remoto segurizado (Desktop Agent -> API)**: con `api.host_enabled: true` se exponen las host tools (`open_app`, `open_url`, `run`, archivos) en `GET /v1/tools` y en las sesiones de la API, bajo el mismo Permission System y audit (un `ask` en API se deniega). **Guarda dura**: `host_enabled` sin token impide arrancar `create_app` (`ValueError`); regla documentada como ADR-015.
- **Frontend servible como estático** (`nova/api/static/index.html`): base de API configurable (`?api=<base>` o `localStorage`), campo de token en el encabezado (Bearer autogenerado), y CSS responsive para móviles (`@media max-width: 600px`).
- **Config**: `APISettings` ampliado (`token`, `host_enabled`, `cors_origins`) con env `NOVA_API_TOKEN`/`NOVA_API_HOST_ENABLED`/`NOVA_API_CORS_ORIGINS`; sección `api:` ampliada en `config/config.yaml` con ejemplos y advertencias.
- **Tests**: `tests/test_api_auth.py` con 10 tests (401 sin/con token erróneo, 200 con token, token protege sesiones/chat y `/v1/models`, guarda `host_enabled` sin token, host tools listadas/ejecutadas solo activas, `ask` denegado en API). Total: **180** (170 previos + 10), 2 skips.
- **Docs**: actualizados `README.md`, `docs/architecture.md` (vínculo API-host), `docs/roadmap.md` (PHASE 8 en progreso), `docs/api.md` (auth + tabla), `docs/setup.md` (modo local/LAN/hosting estático), `docs/security.md` (sección exposición de red PHASE 8) y `docs/decisions.md` (ADR-015).

### Notas

- Regla transversal (ADR-015): nunca exponer host tools en la API sin Bearer token, y nunca publicar la API a Internet sin TLS (proxy reverso). El LLM nunca corre en serverless: solo se sirve el cliente estático.
- Pendientes detectados en el análisis (no resueltos en esta fase): 2 warnings de deprecación en tests (starlette/httpx, anyio) y alinear `requirements*.txt` con `pyproject.toml`.

## [0.7.0] - 2026-09-07

### Añadido — PHASE 7: Model Router + Resource Manager

- **`ResourceManager`** (`nova/llm/resources.py`): snapshot best-effort de recursos del sistema con la stdlib, sin dependencias pesadas:
  - RAM total/disponible (Linux vía `/proc/meminfo`; resto de plataformas degrada conservador), CPU (cores + carga neutral por defecto), GPU/VRAM opcional (lector inyectable, por defecto no disponible) y batería (porcentaje + AC, por defecto AC).
  - Cada reader es un **callable inyectable**; nunca lanza: si falla, degrada a valores conservadores (cpu 50 %, AC sí, sin GPU). Dataclass `SystemResources` con `ram_available_pct()`.
- **Catálogo de modelos** en `config/config.yaml` -> `llm.models`: roles `small`/`local`/`coding`/`vision`/`embedding` (nombres nunca hardcodeados, ADR-004); roles vacíos caen a `default_model`.
- **`ModelRouter`** (`nova/llm/router.py`): clasifica la tarea (`simple`, `coding`, `vision`, `heavy`, `general`) por pistas en el texto y elige modelo por **complejidad + recursos + privacidad**; si el rol no está configurado degrada a `default_model`. Con RAM disponible < `model_router.min_ram_gb` o batería < 20 % sin AC (si `battery: true`) hace *downshift* de tareas pesadas/coding a `small`. `build_router` para el wiring.
- **Config**: `ModelRouterSettings` (`min_ram_gb`/`battery`/`cloud_enabled`, env `NOVA_MODEL_ROUTER_*`) y campo `llm.models`; sección `model_router:` y `llm.models:` en `config/config.yaml`.
- **CLI** (`nova/cli/chat.py`): routing automático por turno (chat general y `/agent <name>`), comandos `/route <text>` (decisión) y `/catalog` (roles y modelos); banner/ayuda actualizados a PHASE 7.
- **API** (`nova/api/app.py`): `POST /v1/route` (`{task_kind, role, model, reason}`); `POST /v1/sessions/{id}/chat` rutea por turno cuando no se pasa `model` explícito (el explícito se respeta).
- **`nova-agent`** (`nova/desktop/agent.py`): misma ruta de routing por turno, `/route` y `/catalog`; corregido bug preexistente `/clear` (`clear()` en lugar de `clear_history()`).
- **Tests**: 29 nuevos (clasificación del router, routing con recursos normales/bajos, downshift auto-desactivado, degradación de readers, GPU, catálogo, config con `llm.models`/`model_router`, 4 de API para `/v1/route` + routing en sesión, smoke real contra Ollama que se omite si no está disponible). Total: **172** (143 previos + 29), 2 skips.
- **Docs**: actualizados `README.md`, `docs/architecture.md`, `docs/roadmap.md` (PHASE 7 ✅), `docs/models.md` (router implementado + tabla de escenarios), `docs/api.md` (`/v1/route`), `docs/security.md`, `docs/decisions.md` (ADR-014) y `CHANGELOG.md`.

### Notas

- La selección de modelo es una **decisión del Core**, nunca del LLM (ADR-014): el modelo propone, N.O.V.A. decide conforme a recursos y privacidad (ADR-013). Sin dependencias nuevas.
- Pendientes detectados en el análisis (no resueltos en esta fase): 2 warnings de deprecación en tests (starlette/httpx, anyio) y la API no tiene auth (PHASE 8).

## [0.6.0] - 2026-09-07

### Añadido — PHASE 6: Desktop Agent (paso 1 — host tools)

- **Licencia MIT**: añadido `LICENSE` (decisión aprobada; se indicaba como pendiente en el análisis estratégico) y declarada `license = "MIT"` en `pyproject.toml`.
- **Herramientas de host** (`nova/tools/host/`), **denegadas por defecto** y bajo el mismo Permission System + audit:
  - `open_app`: lanza una aplicación **configurada por nombre** (`host.apps` en `config.yaml`); el LLM nunca aporta una ruta arbitraria. Si el nombre no está configurado -> fallo.
  - `open_url`: abre una URL **solo `http`/`https`** en el navegador; rechaza esquemas peligrosos o inválidos (`file:`, `javascript:`, `data:`, `ftp:`, sin host...).
  - `run`: ejecuta **solo comandos de una allowlist** (`host.commands`; vacía = nada corre, incluso con `autonomy: full`). Sin shell (sin inyección), timeout configurable (`host.timeout_s` o `timeout_s` por llamada), captura controlada de stdout/stderr (cap 100 KB), y **bloqueo duro** de comandos de elevación/shell/destructivos (`runas`, `sudo`, `cmd`, `powershell`, `format`, `shutdown`, ...).
- **Config** (`nova/core/config.py`): `HostSettings (apps/commands/timeout_s)` con env `NOVA_HOST_*` y sección `host:` en `config/config.yaml` con ejemplos comentados.
- **CLI** (`nova/cli/chat.py`): las host tools se registran en el mismo `ToolRunner`; `/run open_app {...}`, `/run open_url {...}`, `/run run {...}` reutilizan la ejecución existente (sin lógica duplicada); banner/ayuda actualizados a PHASE 6.
- **`nova-agent`** (`nova/desktop/agent.py` + script `nova-agent`): proceso local (base del Desktop Agent) con el mismo wiring (provider, memoria, host tools, permisos, audit). **Sin comunicación remota todavía** (PHASE 8).
- **Tests**: 24 nuevos en `tests/test_host.py` (deny por defecto, ask sin confirmación, open_app configurado/desconfigurado/ruta arbitraria, open_url válido y 6 URLs peligrosas/inválidas, run no permitido incluso con autonomy full, comandos bloqueados, captura de salida, timeouts, comando inexistente, argumentos inválidos, audit allow/deny, defaults y env de HostSettings). Total: **123** (99 previos + 24).
- **Docs**: actualizados `README.md`, `docs/architecture.md`, `docs/roadmap.md` (PHASE 6 en progreso), `docs/security.md` (sección host tools) y creado `docs/tools.md`.

### Notas

- El flujo de decisión se documenta explícitamente: **LLM propone -> Permission Manager decide -> ToolRunner ejecuta -> Audit registra**. Las herramientas de host añaden una segunda barrera independiente del Permission System: la allowlist de comandos / lista de aplicaciones configurables.
- La API (`nova-api`) **no** expone las host tools en este paso (sin funcionalidad remota): solo están activas en CLI y `nova-agent`.
- Pendientes detectados en el análisis: 2 warnings de deprecación en tests (starlette/httpx, anyio) y la API no tiene auth (se aborda en PHASE 8).

### Añadido — PHASE 6 paso 2: límites de rutas / FileSystem bounds

- **`PathBounds`** (`nova/tools/host/paths.py`): acota **toda** ruta que toque una host tool. Cada path se expande y `resolve()` (normaliza `..` y symlinks/junctions) y debe quedar dentro de uno de los `host.roots`; comparación sin distinguir mayúsculas en Windows. `roots` vacío = **sin acceso al FS**.
- **Tools de archivos acotadas** (`nova/tools/host/files.py`):
  - `read_file` (solo lectura, tope 100 KB), `list_files` (solo lectura), `write_file` (tope 1 MB; el directorio padre debe existir; no crea rutas). Todas denegadas por defecto y bajo el Permission System.
- **`run` acotado**: el `cwd` (por llamada) y el `working_dir` por defecto deben estar dentro de `host.roots`; sin roots se rechaza. Se reporta el `cwd` real en el resultado.
- **Config**: `HostSettings.roots` (lista) y `working_dir` (string|None), env `NOVA_HOST_ROOTS`/`NOVA_HOST_WORKING_DIR`; sección `roots:`/`working_dir:` en `config/config.yaml` con `roots` vacío por defecto (off).
- **CLI**: las 3 tools de archivos se registran; `/run read_file|write_file|list_files {...}` disponibles.
- **Tests**: 20 nuevos en `tests/test_host_paths.py` (tools de archivos denegadas por defecto / sin roots, leer/escribir/listar dentro y fuera de root, escape `../` bloqueado, archivo inexistente/too large, escribir sin directorio padre, `cwd` dentro/fuera/sin roots, `working_dir` por defecto, `cwd` inexistente, defaults y env de roots). Total: **143** (123 + 20).
- **Docs**: actualizados `docs/security.md`, `docs/architecture.md`, `docs/roadmap.md`, `docs/tools.md`, `README.md` y `CHANGELOG.md`.

## [0.5.0] - 2026-09-07

### Añadido — PHASE 5: Agents

- **Núcleo de agentes** (`nova/agents/core.py`): clase única `Agent` con loop acotado (`max_steps`). El LLM **propone** tool-calls con JSON estructurado (`{"tool": "<name>", "args": {...}}`, tolera code fences) y N.O.V.A. **decide**/ejecuta vía el `ToolRunner` (Permission System + audit). Retorna `AgentResult` con la respuesta, el modelo y los `steps`.
- **`ChatSession.set_system_prompt` y `add_tool`**: el agente reemplaza el prompt de sistema con su perfil + lista de tools (schemas JSON) y alimenta al LLM los resultados de cada tool como mensajes `tool`.
- **`BaseTool.json_schema()`**: expone el schema Pydantic de cada herramienta para describirlas al LLM.
- **5 presets paramétricos** (`nova/agents/presets.py`): `AgentPreset` (name/description/system_prompt/tool_names) para `general`, `coding`, `research`, `system` y `automation`, con `create_agent` y `agent_presets`/`get_preset`. Sin subclases.
- **Memoria compartida**: los agentes reutilizan el `MemoryService` (inyección de contexto por turno y registro de la conversación) usando `remember`/`memory_search` como tools.
- **CLI** (`nova/cli/chat.py`): comandos `/agents` (listar) y `/agent <name> <text>` (sesiones persistentes por agente); se muestra cada step de tool.
- **API** (`nova/api/app.py`): `GET /v1/agents`, `POST /v1/agents/{name}/chat` (agentes persistentes por nombre), sesiones con campo `agent` (`POST /v1/sessions` con `{"agent": ...}`, turnos con `steps`). En la API un permiso `ASK` se deniega (sin confirmación interactiva).
- **Web** (`nova/api/static/index.html`): selector de agente y visualización de los steps de cada turno.
- **Tests**: 19 tests de agents (parse del JSON de tool-call, tool ok, tool denegada por permisos, tool desconocida, guarda de `max_steps`, error del provider, inyección de contexto de memoria, registro de conversación, presets) + 5 tests de API de agents. Total: 99.

### Notas

- Decisión ADR-012 (Agents como presets paramétricos con protocolo RSA/JSON estructurado) en `docs/decisions.md`.
- Sin dependencias nuevas; la selección automática de herramientas se exponía en ADR-009 y se materializa en esta fase.
- Actualizados `docs/architecture.md`, `docs/api.md`, `docs/roadmap.md`, `docs/setup.md`, `docs/development.md` y `README.md`.

## [0.4.0] - 2026-09-07

### Añadido — PHASE 4: Interface

- **API REST** (`nova/api/app.py`): app FastAPI (`create_app`) que reutiliza el Core (LLM, `ChatSession`, memoria, `ToolRunner`). Endpoints:
  - `GET /healthz` — salud, estado del proveedor y versión.
  - `GET /v1/models`, `GET /v1/tools` — modelos y herramientas registradas (estándar + memoria).
  - `POST /v1/chat` — completado stateless (mensajes completos, OpenAI-compatible ligero).
  - `POST /v1/sessions` — crea sesión; `POST /v1/sessions/{id}/chat` — turno con sesión y memoria (inyección de contexto); `GET /v1/sessions/{id}/messages`; `DELETE /v1/sessions/{id}`.
  - `POST /v1/sessions/{id}/run` — ejecuta una herramienta bajo el Permission System (en la API un `ASK` se resuelve como denegado).
  - `POST /v1/sessions/{id}/remember` y `GET /v1/sessions/{id}/memory?q=` — memoria explícita y recuperación.
- **Interfaz web** (`nova/api/static/index.html`): chat HTML/JS (sin CDNs) servido en `/`; crea sesión, envía mensajes, lista modelos/herramientas y muestra el contexto inyectado.
- **Servidor** (`nova/api/server.py`): entry point `nova-api` con `uvicorn`; `APISettings` (`api.host`, `api.port`) con env `NOVA_API_HOST`/`NOVA_API_PORT`; documentación OpenAPI en `/docs`.
- **Tests**: 18 tests con `fastapi.testclient` (health, modelos, herramientas, chat stateless y por sesión, memoria recuperando contexto, run de herramientas con permisos, 404/502).
- Dependencias nuevas: `fastapi>=0.115`, `uvicorn>=0.30` (previstas en ADR-001 para PHASE 4).

### Notas

- Decisión ADR-011 (API FastAPI stateless + sesiones con memoria por petición) en `docs/decisions.md`.
- Actualizados `docs/architecture.md`, `docs/api.md`, `docs/roadmap.md`, `docs/setup.md` y `README.md`.

## [0.3.0] - 2026-09-07

### Añadido — PHASE 3: Memory

- **Memoria persistente** (`nova/memory/store.py`): `MemoryStore` sobre SQLite local (`memory/nova.db`, sin dependencias nuevas) con dos tablas: `memories` (hechos/preferencias) y `transcripts` (conversación persistente por sesión). Embeddings opcionales por registro.
- **Embebidos en el proveedor** (`nova/llm/ollama.py`): `embed_text` vía `POST /api/embed` con `nomic-embed-text`; `LLMProvider.embed_text` + flag `supports_embedding` (por defecto no soportado, lanza `NOVAProviderError`). Campo `llm.embedding_model` en config.
- **Recuperación con contexto** (`nova/memory/retriever.py`): `MemoryRetriever` rankea memorias e historial por similitud coseno sobre embeddings; si no hay provider o falla el embedding, cae a búsqueda por keywords (offline).
- **`MemoryService`** (`nova/memory/service.py`): fachada `remember` / `record` / `search` / `context` / `recent_memories`; genera un bloque de contexto inyectable en el prompt del LLM.
- **Herramientas de memoria** (`nova/memory/tools.py`): `remember` (guardar hecho) y `memory_search` (recuperar) como `BaseTool` estándar, bajo el Permission System y audit.
- **CLI**: cada turno se persiste automáticamente; el contexto relevante se inyecta como mensaje `system` antes de cada petición al LLM; comandos `/remember <text>` y `/memory [query]`.
- **Config** (`nova/core/config.py`): `MemorySettings` (`db_file`, `session_id`, `max_context`, `similarity_threshold`) con overrides env `NOVA_MEMORY_*`.

### Notas

- Decisiones ADR-010 (memoria en SQLite + embeddings del proveedor con fallback) en `docs/decisions.md`.
- Actualizados `docs/architecture.md`, `docs/api.md`, `docs/roadmap.md`, `docs/setup.md` y `README.md`.

## [0.2.0] - 2026-09-06

### Añadido — PHASE 2: Tool System

- **Herramientas** (`nova/tools/base.py`): `BaseTool` con schema Pydantic por herramienta, validación de argumentos y resultados tipados (`ToolResult`). Errores: `ToolError`, `ToolArgumentError`.
- **Registro de herramientas** (`nova/tools/registry.py`): `ToolRegistry` + instancia global; añadir una herramienta = `registry.register(MiTool())`.
- **Herramientas estándar** (`nova/tools/standard.py`): `calculate` (aritmética segura vía AST whitelist), `date_time`, `list_dir` (solo lectura).
- **Permission System** (`nova/tools/permissions.py`): niveles de autonomía `off`/`ask`/`full` + reglas `allow`/`deny` (precedencia `deny` > `allow` > autonomía). Config en `config/config.yaml` -> `permissions`.
- **ToolRunner** (`nova/tools/runner.py`): flujo permiso -> validación -> ejecución, auditando cada paso; resuelve `ASK` con un callback de confirmación.
- **Audit log** (`nova/core/audit.py`): registro append-only JSON lines de cada ejecución/decisión en `logs/audit.nova.jsonl` (rotativo 2 MB x 3); `record` nunca lanza.
- **Config** (`nova/core/config.py`): `PermissionSettings` y `AuditSettings`, con overrides env `NOVA_PERMISSIONS_AUTONOMY` y `NOVA_AUDIT_FILE`.
- **CLI** (`nova/cli/chat.py`): comandos `/tools` (listar) y `/run <tool> <json>` (ejecutar con confirmación `[y/N]` si el Permission System lo requiere).
- **Tests**: registry, `calculate`/`date_time`/`list_dir`, lógica de permisos (allow/deny/off/full/ask) y audit (allow, deny, error de validación).

### Notas

- Decisiones ADR-007 (Permission System), ADR-008 (audit JSONL) y ADR-009 (selección de herramientas por el LLM en PHASE 5) documentadas en `docs/decisions.md`.
- Actualizados `docs/architecture.md`, `docs/api.md`, `docs/roadmap.md`, `docs/development.md`, `docs/setup.md` y `README.md`.

## [0.1.0] - 2026-09-06

### Añadido — PHASE 1: N.O.V.A. Core

- Estructura base del proyecto: paquete `nova`, config, logging, CLI y tests.
- **Configuración externa**: `config/config.yaml` con override por variables de entorno `NOVA_*`. Los nombres de modelo se configuran, nunca se hardcodean.
- **LLM Provider interface** (`nova/llm/base.py`): contrato abstraído (`chat`, `list_models`, `health`) desacoplado de Ollama.
- **OllamaProvider** (`nova/llm/ollama.py`): usa la API compatible OpenAI de Ollama (`/v1/chat/completions`) y `/api/tags` para listar modelos.
- **Registry de proveedores** (`nova/llm/registry.py`): punto de registro para proveedores futuros (local/cloud sin tocar el Core).
- **ChatSession** (`nova/core/session.py`): contexto de conversación con historial acotado y `system prompt` persistente.
- **Logging** (`nova/core/logging.py`): consola + archivo rotativo en `logs/nova.log`.
- **CLI de conversación** (`nova`): chat interactivo con comandos `/exit`, `/clear`, `/models`, `/model <name>`, `/help`.
- **Tests**: config (yaml/env/overrides), ChatSession, OllamaProvider (payload OpenAI, lista de modelos, manejo de errores) e integración contra Ollama (se omite si no está disponible).

### Notas

- Documentación inicial creada: `README.md` y `docs/{architecture,roadmap,setup,development,decisions,troubleshooting,api}.md`.
- Entorno de desarrollo: Python 3.11, `httpx`, `pydantic`, `pydantic-settings`, `PyYAML`, `pytest`.