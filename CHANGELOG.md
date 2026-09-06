# Changelog

El formato sigue [Keep a Changelog](https://keepachangelog.com/es/1.1.0/) y sigue versionado semántico.

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