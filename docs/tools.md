# Catálogo de herramientas

Todas las herramientas son `BaseTool`: validan argumentos con un **schema Pydantic** y se ejecutan
solo tras la decisión del `PermissionSystem` (allow/deny/ask), quedando todo auditadas en
`logs/audit.nova.jsonl`. Flujo: **LLM propone -> Permission System decide -> ToolRunner ejecuta ->
Audit registra**.

El registro real (qué tools hay y sus schemas) lo expone el propio sistema: `/tools` en el CLI y
`nova.tools` en la consola Python / `GET /v1/tools` en la API.

## Estándar (`nova/tools/standard.py`) — PHASE 2

| Herramienta | Argumentos | Descripción | Riesgo |
|---|---|---|---|
| `calculate` | `expression` | Evalúa una expresión aritmética **segura** (AST whitelist: números, `+ - * / % **`, `pi/e`, `abs/round/min/max/int/float/sqrt`). | Seguro |
| `date_time` | `format` (strftime, defecto ISO con offset) | Fecha/hora local actual. | Seguro |
| `list_dir` | `path` (defecto `.`), `show_hidden` (bool) | Lista las entradas de un directorio. **Solo lectura**. | Seguro |

## Memoria (`nova/memory/tools.py`) — PHASE 3

| Herramienta | Argumentos | Descripción | Riesgo |
|---|---|---|---|
| `remember` | `text`, `importance` (0-1, defecto 0.5) | Guarda un hecho/preferencia en memoria persistente (SQLite). | Seguro (se audita) |
| `memory_search` | `query`, `max_results` | Recupera memorias y conversaciones relevantes (embeddings locales con fallback a keywords). | Seguro (solo lectura) |

## Host (`nova/tools/host/`) — PHASE 6

Solo disponibles en **CLI y `nova-agent`** (proceso local); **no** expuestas en la API.

| Herramienta | Argumentos | Descripción | Riesgo |
|---|---|---|---|
| `open_app` | `app` (nombre) | Lanza una aplicación **configurada por nombre** en `host.apps`. El LLM nunca aporta una ruta. | Confirmado |
| `open_url` | `url` | Abre una URL en el navegador. **Solo `http`/`https`** con host válido; otros esquemas -> error. | Confirmado |
| `run` | `command`, `args[]`, `cwd?`, `timeout_s?` | Ejecuta un comando **de la allowlist** `host.commands` sin shell, capturando salida con timeout. El `cwd` (si se fija) debe estar dentro de `host.roots`. | Alto (allowlist) |
| `read_file` | `path` | Lee un archivo de texto **dentro de `host.roots`** (tope 100 KB). Solo lectura. | Confirmado |
| `write_file` | `path`, `content` | Escribe un texto **dentro de `host.roots`** (tope 1 MB); el directorio padre debe existir. | Alto |
| `list_files` | `path` | Lista un directorio **dentro de `host.roots`**. Solo lectura. | Seguro |

## Configuración (PHASE 6)

Sección `host:` en `config/config.yaml`:

```yaml
host:
  apps:                       # open_app: nombre -> ejecutable o ruta
    notepad: notepad.exe
    # chrome: "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"
  commands: []                # run: allowlist de comandos (vacía = nada se ejecuta)
  roots: []                   # path bounds: rutas de FS y cwd permitidos (vacía = sin acceso a archivos)
  working_dir: null           # cwd por defecto de `run` (debe estar en roots)
  timeout_s: 30.0             # run: timeout por defecto (override por llamada `timeout_s`)
```

### Habilitar una aplicación, comando o raíz de archivos

1. **`open_app`**: añade la entrada a `host.apps` (por ejemplo
   `chrome: "C:\Program Files\Google\Chrome\Application\chrome.exe"`) y reinicia el CLI/agente.
2. **`run`**: añade el comando base a `host.commands` (por ejemplo `- git` para `git status`). El
   comando debe existir en el `PATH` del sistema.
3. **Accesso a archivos (`read_file`/`write_file`/`list_files`) y `cwd` de `run`**: añade las carpetas
   permitidas a `host.roots` (ativas o sin roots no se permite nada). Opcionalmente
   `working_dir` como `cwd` por defecto. Todo lo que `write_file` cree/cambie queda dentro de estos roots.
4. **Permiso de la herramienta**: con `autonomy: ask` el CLI preguntará `[y/N]` la primera vez; si
   quieres que corra directa, añádela a `permissions.allow` (o a `permissions.deny` para bloquearla)
   siempre. En la API solo se registran con `api.host_enabled: true` (PHASE 8) y siguen bajo permisos.

Los comandos de elevación/destructivos son imposibles de permitir: `nova.tools.host.terminal.BLOCKED_COMMANDS`
los bloquea siempre (ver [security.md](security.md)).