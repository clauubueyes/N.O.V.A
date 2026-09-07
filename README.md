# N.O.V.A.

**N.O.V.A. = Neural Operations & Virtual Assistant**

Asistente personal de IA moderno, **gratuito, local-first, privado y extensible**. Inspirado conceptualmente en asistentes como JARVIS, pero completamente original. La inteligencia de producto vive en el sistema (orquestación, memoria, herramientas, agentes, permisos, routing), no en un modelo propietario: N.O.V.A. **es sistema + modelos existentes** (locales, vía Ollama en primer lugar).

Get-Process | Where-Object {
    $_.ProcessName -like '*Riot*' -or
    $_.ProcessName -like '*VALORANT*'
} | Select-Object Id,ProcessName,Path> Estado actual: **PHASE 6 — Desktop Agent** (paso 2: configuración, proveedor Ollama, conversación, contexto, logging, sistema de herramientas con permisos y audit, memoria persistente con recuperación por embeddings, API REST + interfaz web, agentes que seleccionan herramientas con el LLM como proponente, y **host tools** seguras `open_app`/`open_url`/`run` + tools de archivos acotadas por `host.roots`, entry point `nova-agent` local). Siguiente: vínculo seguro con la API (PHASE 8, ver [docs/roadmap.md](docs/roadmap.md)).

Distribuido bajo la licencia **MIT** (ver [LICENSE](LICENSE)). Libre de usar, modificar y distribuir.

## Principio fundamental

El modelo de IA **propone**; N.O.V.A. **decide** y los sistemas externos **ejecutan** bajo permisos. El LLM jamás tiene control directo e ilimitado del ordenador.

```
Usuario -> N.O.V.A. -> LLM (local) -> N.O.V.A. Core -> Permission System -> Tool -> Sistema -> Resultado -> N.O.V.A. -> Usuario
```

## Compromiso: gratuito y privado

- N.O.V.A. **funciona sin APIs de pago**: la inferencia corre en el dispositivo del usuario (Ollama + modelos locales/OSS).
- Los datos del usuario permanecen locales siempre que sea posible; nada sale del dispositivo sin consentimiento explícito.
- Las APIs cloud son **integración opcional en el futuro**, solo si el usuario la configura; el Core nunca depende de ellas (ver [docs/security.md](docs/security.md) y [docs/models.md](docs/models.md)).

## Quickstart

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev]"
.\.venv\Scripts\nova
```

Interfaz web y API REST:

```powershell
.\.venv\Scripts\nova-api     # sirve http://127.0.0.1:8000/ (UI) y /docs (OpenAPI)
.\.venv\Scripts\nova-agent   # proceso local del Desktop Agent (PHASE 6, sin remoto)
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

## Host (PHASE 6, paso 1)

Herramientas de control del ordenador, **denegadas por defecto** y bajo el mismo Permission System + audit (solo CLI y `nova-agent`, no en la API):

```text
/run open_app {"app":"notepad"}       # lanza una app configurada en host.apps
/run open_url {"url":"https://example.com"}   # solo http(s), rechaza esquemas peligrosos
/run run {"command":"echo","args":["hola"]}   # solo comandos de host.commands, sin shell, con timeout
/run read_file {"path":"..."}        # lee un archivo dentro de host.roots (solo lectura)
/run list_files {"path":"..."}       # lista un directorio dentro de host.roots
```

- `open_app`: el LLM solo aporta el **nombre**; la ruta de la app vive en `config/config.yaml` -> `host.apps` (nunca una ruta arbitraria).
- `run`: la **allowlist** `host.commands` es obligatoria — vacía = nada se ejecuta, incluso con `autonomy: full`. Elevación y comandos destructivos siempre bloqueados.
- Archivos: `read_file`/`write_file`/`list_files` y el `cwd` de `run` **solo tocan rutas dentro de `host.roots`** (vacío = sin acceso al FS; los escapes `../`/symlinks se bloquean).
- Cómo habilitar una app, comando o carpeta: ver [docs/security.md](docs/security.md) y `config/config.yaml`.

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
config/config.yaml    Configuración externa (modelos, permissions, audit, memory, api, host; nunca en código)
nova/
  core/              Config, logging, contexto de conversación (ChatSession), audit log
  llm/               LLMProvider (interfaz) + OllamaProvider (chat y embeddings) + registro (+ Model Router en PHASE 7)
  tools/             Herramientas (BaseTool + schemas) + Permission System + runner
    host/            Host tools seguras: open_app/open_url/run + files acotados (PHASE 6)
  memory/            Memoria persistente (SQLite) + recuperación por embeddings (PHASE 3)
  agents/            Agent + 5 presets paramétricos y tool-call por JSON estructurado (PHASE 5)
  desktop/           Processo local nova-agent (base del Desktop Agent, PHASE 6)
  api/               API REST (FastAPI) + interfaz web (PHASE 4)
  cli/               Interfaz de conversación
docs/                Documentación del proyecto
tests/               Tests pytest
```

Arquitectura objetivo (evolución, no un salto de golpe):

```
         N.O.V.A. Core
      ┌──────┼──────┐
   Memory  Agents  Tools
      └──────┼──────┘
        Model Router
      ┌──────┼──────┐
    Ollama  Locales  Cloud (SOLO si el usuario lo configura)
```

## Documentación

| Documento | Descripción |
|---|---|
| [docs/architecture.md](docs/architecture.md) | Arquitectura y capas (actual + objetivo) |
| [docs/roadmap.md](docs/roadmap.md) | Roadmap por fases |
| [docs/setup.md](docs/setup.md) | Instalación y configuración |
| [docs/development.md](docs/development.md) | Guía de desarrollo |
| [docs/decisions.md](docs/decisions.md) | Decisiones arquitectónicas (ADR) |
| [docs/security.md](docs/security.md) | Modelo de seguridad y permisos |
| [docs/tools.md](docs/tools.md) | Catálogo de herramientas (estándar, memoria, host) |
| [docs/models.md](docs/models.md) | Modelos y política de selección |
| [docs/troubleshooting.md](docs/troubleshooting.md) | Problemas comunes |
| [docs/api.md](docs/api.md) | APIs internas y externas |
| [CHANGELOG.md](CHANGELOG.md) | Historial de cambios |