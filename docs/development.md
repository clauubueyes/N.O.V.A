# Desarrollo

## Comandos

```powershell
# Tests
.\.venv\Scripts\python -m pytest                # con verbose: -v -s

# Chat interactivo
.\.venv\Scripts\nova

# Dial con Ollama sin CLI (smoke test)
.\.venv\Scripts\python -c "from nova.core.config import load_settings; from nova.llm.registry import create_provider; p=create_provider(load_settings().llm); print([m.name for m in p.list_models()])"

# Smoke test de herramientas (sin LLM)
.\.venv\Scripts\python -c "from nova.core.config import load_settings; from nova.core.audit import AuditLog; from nova.tools import ToolRunner, registry; from nova.tools.permissions import PermissionSystem; s=load_settings(); r=ToolRunner(registry, PermissionSystem(s.permissions), AuditLog(s.audit.file)); print(r.run('calculate', {'expression': '2+2'}))"

# Smoke test de agents (semilla de herramientas locales, sin LLM)
.\.venv\Scripts\python -c "from nova.agents import agent_presets; print([p.name for p in agent_presets()])"
```

## Estructura

```
nova/
  core/      config.py, logging.py, session.py, audit.py   (nada depende de aquí hacia arriba)
  llm/       base.py (interfaz), ollama.py, registry.py
  memory/    store.py (SQLite), retriever.py, service.py, tools.py
  tools/     base.py, standard.py, registry.py, permissions.py, runner.py
  agents/    core.py (Agent + JSON tool-call), presets.py (5 presets + create_agent)
  api/       app.py (FastAPI), server.py (uveicorn), schemas.py, static/index.html
  cli/       chat.py
tests/       pytest
config/      config.yaml
docs/        documentación
```

## Convenciones

- Python 3.11 con type hints (`from __future__ import annotations`).
- Sin comentarios en código salvo docstrings de interfaz cuando aportan.
- Importable y testeable: inyección de dependencias (p. ej. `OllamaProvider` acepta un `httpx.Client` para tests; `ToolRunner` acepta registry/permisos/audit/confirm).
- Errores de proveedores envueltos en `NOVAProviderError` (nunca HTTP/httpx crudo fuera de `nova.llm`); errores de herramientas en `ToolError`.
- Config solo vía `config/config.yaml` o env `NOVA_*`: nada de valores de modelo en el código.
- Las herramientas nunca tocan el sistema sin pasar por `PermissionSystem` + `ToolRunner`; toda ejecución queda en el audit log.

## Ciclo por fase

1. **Analizar** el estado actual.
2. **Implementar** el incremento mínimo.
3. **Testear** (unit + smoke).
4. **Revisar** (nueva capa respeta las reglas: separación LLM/orquestación/herramientas/sistema).
5. **Documentar** (docs + `CHANGELOG.md` + actualizar `docs/roadmap.md`).

## Git

- Commits lógicos y atómicos; mensaje en inglés o español coherente.
- Nunca commitear `.env`, `config/local.yaml`, `logs/` ni `.venv/`.

## Deuda pendiente (a medida que aplique)

- Linter/formatter: `ruff` (añadir cuando los archivos lo justifiquen).
- Coverage: `pytest --cov` (también más adelante).