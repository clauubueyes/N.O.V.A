# N.O.V.A.

**N.O.V.A. = Neural Operations & Virtual Assistant**

Asistente personal de IA inspirado en JARVIS pero completamente original. Construido alrededor de **Ollama** (modelos locales), con arquitectura en capas que separa la **inteligencia** de la **ejecución** sobre el sistema.

> Estado actual: **PHASE 3 — Memory** (configuración, proveedor Ollama, conversación, contexto, logging, sistema de herramientas con permisos y audit, y memoria persistente con recuperación por embeddings).

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

## Proyecto

```
config/config.yaml    Configuración externa (modelos, permissions, audit, memory; nunca en código)
nova/
  core/              Config, logging, contexto de conversación (ChatSession), audit log
  llm/               LLMProvider (interfaz) + OllamaProvider (chat y embeddings) + registro
  tools/             Herramientas (BaseTool + schemas) + Permission System + runner
  memory/            Memoria persistente (SQLite) + recuperación por embeddings (PHASE 3)
  cli/               Interfaz de conversación
  api/               (PHASE 4) API REST
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