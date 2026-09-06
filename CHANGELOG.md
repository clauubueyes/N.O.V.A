# Changelog

El formato sigue [Keep a Changelog](https://keepachangelog.com/es/1.1.0/) y sigue versionado semántico.

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