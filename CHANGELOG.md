# Changelog

El formato sigue [Keep a Changelog](https://keepachangelog.com/es/1.1.0/) y sigue versionado semántico.

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