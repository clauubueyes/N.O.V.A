# N.O.V.A.

**N.O.V.A. = Neural Operations & Virtual Assistant**

Asistente personal de IA inspirado en JARVIS pero completamente original. Construido alrededor de **Ollama** (modelos locales), con arquitectura en capas que separa la **inteligencia** de la **ejecución** sobre el sistema.

> Estado actual: **PHASE 5 — Agents** (configuración, proveedor Ollama, conversación, contexto, logging, sistema de herramientas con permisos y audit, memoria persistente con recuperación por embeddings, API REST + interfaz web, y **agentes** que seleccionan herramientas automáticamente con el LLM como proponente).

## Principio fundamental

El modelo de IA **propone**; N.O.V.A. **decide** y los sistemas externos **ejecutan** bajo permisos. El LLM jamás tiene control directo e ilimitado del ordenador.

```
Usuario -> N.O.V.A. -> LLM (Ollama) -> Orchestrator -> Permission System -> Tool -> Sistema -> Resultado -> N.O.V.A. -> Usuario
```

## Quickstart

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev]"
.\.venv\Scripts\nova
```

Interfaz web y API REST:

```powershell
.\.venv\Scripts\nova-api     # sirve http://127.0.0.1:8000/ (UI) y /docs (OpenAPI)
```

Persiste que Ollama esté corriendo (`ollama serve`) y que tengas al menos un modelo, p. ej. `ollama pull llama3.1:8b`. Para la memoria (PHASE 3) además un modelo de embeddings: `ollama pull nomic-embed-text` (si falta, N.O.V.A. funciona igual con búsqueda por keywords).

Prueba una herramienta dentro del chat:

```text
/run calculate {"expression":"2+2"}
```

Guarda un hecho y recupéralo:

```text
/remember me llamo Gabriel
/memory Gabriel
```

N.O.V.A. guarda cada conversación y, cuando preguntes algo, inyecta automáticamente la memoria relevante como contexto.

## Agentes (PHASE 5)

Los agentes dejan que el LLM **proponga** tools y N.O.V.A. las ejecute bajo el Permission System. Hay 5 presets: `general`, `coding`, `research`, `system` y `automation`.

```text
/agents                      # lista los presets
/agent research qué sabemos del proyecto?
/agent system qué fecha es hoy?
```

En la web usa el selector de agente; por API, `GET /v1/agents` y `POST /v1/agents/{name}/chat` (o crea una sesión con `{"agent": "coding"}`). El LLM responde con JSON estructurado (`{"tool": ..., "args": ...}`) o directamente; cada paso ejecutado aparece como `steps` y queda auditado.

## Proyecto

```
config/config.yaml    Configuración externa (modelos, permissions, audit, memory, api; nunca en código)
nova/
  core/              Config, logging, contexto de conversación (ChatSession), audit log
  llm/               LLMProvider (interfaz) + OllamaProvider (chat y embeddings) + registro
  tools/             Herramientas (BaseTool + schemas) + Permission System + runner
  memory/            Memoria persistente (SQLite) + recuperación por embeddings (PHASE 3)
  agents/            Agent + 5 presets paramétricos y tool-call por JSON estructurado (PHASE 5)
  api/               API REST (FastAPI) + interfaz web (PHASE 4)
  cli/               Interfaz de conversación
docs/                Documentación del proyecto
tests/               Tests pytest
```

## Documentación

| Documento | Descripción |
|---|---|
| [docs/architecture.md](docs/architecture.md) | Arquitectura y capas |
| [docs/roadmap.md](docs/roadmap.md) | Roadmap por fases |
| [docs/setup.md](docs/setup.md) | Instalación y configuración |
| [docs/development.md](docs/development.md) | Guía de desarrollo |
| [docs/decisions.md](docs/decisions.md) | Decisiones arquitectónicas (ADR) |
| [docs/troubleshooting.md](docs/troubleshooting.md) | Problemas comunes |
| [docs/api.md](docs/api.md) | APIs internas y externas |
| [CHANGELOG.md](CHANGELOG.md) | Historial de cambios |