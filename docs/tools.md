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

Disponibles en **CLI y `nova-agent`**; en la API solo con `api.host_enabled: true` (PHASE 8, exige token).

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

## Web (`nova/tools/web/`) — PHASE 9

**Off por defecto** (`web.enabled: false`): no se registra nada. Con `enabled: true` se registran las
tres tools bajo el mismo `PermissionSystem` (denegadas hasta no añadirlas a `permissions.allow` o
confirmarlas en `ask`). El LLM accede a Internet **solo** a través de estos tools; no existe ninguna
primitiva de red en el Core.

| Herramienta | Argumentos | Descripción | Riesgo |
|---|---|---|---|
| `web_search` | `query`, `max_results` (1-20) | Búsqueda web sin API key (defecto DuckDuckGo HTML). Devuelve título, URL y snippet. | Confirmado |
| `web_fetch` | `url`, `max_chars` (100-20000) | Descarga una página (solo `http(s)`, sin userinfo) y devuelve título + texto legible. | Confirmado |
| `web_extract` | `url`, `max_links` (1-100) | Devuelve los enlaces (texto + URL) de una página para explorar un sitio. | Confirmado |

### Política de red (`nova/tools/web/client.py` — `WebClient`)

Cada petición pasa por, en orden:

1. **Validación de URL**: solo esquemas `http`/`https`, host obligatorio, sin `usuario:contraseña@`.
2. **`robots.txt`** (`web.respect_robots`, defecto `true`): parser RFC-9309 (Allow/Disallow, prefijo más
   largo gana, Allow desempata). Por defecto se respeta.
3. **Rate limit por host** (`web.min_delay_s`, defecto 1 s): mínimo entre peticiones al mismo host,
   compartido por las tres tools.
4. **Límites**: `web.max_bytes` (defecto 1 MB, corta la descarga), `web.max_redirects` (5),
   `web.timeout_s` (12 s), `web.max_chars` (4000 caracteres devueltos al LLM por página/search).
5. **User-Agent identificable** (`web.user_agent`): `NOVA/1.0 ...` para `who are you` de robots.py.

### Configuración (PHASE 9)

```yaml
web:
  enabled: false                  # true registra web_search/web_fetch/web_extract
  user_agent: "NOVA/1.0 (N.O.V.A. local personal assistant)"
  timeout_s: 12.0
  max_redirects: 5
  max_bytes: 1000000
  max_chars: 4000
  respect_robots: true
  min_delay_s: 1.0
  search_url: "https://html.duckduckgo.com/html/?q={query}"  # o SearXNG/Brave API
  search_max: 5
```

La búsqueda usa el endpoint `web.search_url` (plantilla con `{query}`). El defecto es DuckDuckGo HTML
(sin API key); para producción se puede usar un SearXNG self-hosted (mismo `search_url`) o la Brave
Search API free tier. El LLM no nota la diferencia: la tool que usa es `web_search`.

## Plugins (`nova/plugins/`) — PHASE 11

**Off por defecto** (`plugins.enabled` vacío + `dir` nulo): no se carga nada. Un plugin amplía el
catálogo de tools registrando `BaseTool`s en el `ToolRegistry`; cada tool sigue pasando por el mismo
`PermissionSystem` + audit (una tool de plugin puede denegarse o requerir confirmación).

### Contrato (ADR-018)

```python
from nova.plugins.base import Plugin

class MyPlugin(Plugin):
    name = "mi_plugin"
    description = "Qué hace."

    def tools(self) -> list[BaseTool]:
        return [MyTool()]          # subclases de nova.tools.base.BaseTool
```

Carga en CLI / `nova-agent` / API: `load_plugin_tools(settings.plugins, registry=...)`.

### Built-ins

| Plugin | Herramientas | Descripción | Riesgo |
|---|---|---|---|
| `text_tools` | `text_base64_encode`, `text_base64_decode`, `text_slugify`, `text_uuid` | Utilidades de texto (base64, URL-safe slug, UUID v4). | Bajo |
| `units` | `convert_length`, `convert_weight`, `convert_temperature` | Conversión de unidades (m/km/cm/mm/ft/in/mi/yd; kg/g/mg/lb/oz/t; c/f/k). | Bajo |

### Configuración (PHASE 11)

```yaml
plugins:
  enabled: []            # built-ins por nombre, p. ej. ["text_tools", "units"]
  dir: null              # carpeta con módulos *_plugin.py que exponen un objeto PLUGIN
```

Env: `NOVA_PLUGINS_ENABLED=text_tools,units`, `NOVA_PLUGINS_DIR=path`. Un plugin roto se loguea y se
omite sin romper el arranque.

## Automatización (`nova/automation/`) — PHASE 12

**Off por defecto** (`automation.enabled: false`). La automatización no crea tools nuevas: programa
la ejecución de las tools/agentes existentes bajo el mismo `PermissionSystem` + audit (ADR-019).

### Scheduler

Cada tarea tiene un horario y una acción:

```yaml
automation:
  enabled: true
  poll_s: 1.0
  tasks:
    - name: heartbeat
      description: "Latido de prueba"
      schedule:
        interval_s: 60          # cada 60 s (o at: "09:00" para una vez al día; interval_s gana)
      tool: date_time            # acción: una tool...
      args: {}
      # agent: general          # ...un turno de agente (+ text)
      # workflow: my_workflow   # ...o un workflow configurado (mutuamente exclusivo)
  workflows:
    - name: my_workflow
      description: "Workflow de ejemplo"
      steps:
        - tool: calculate
          args: {expression: "2+2"}
        - agent: general
          text: "Resume el resultado."
          on_error: continue    # stop (defecto) o continue por paso
```

En modo desatendido un permiso `ask` se **deniega** (no hay humano que confirmar): las tools que un
task/workflow vaya a ejecutar deben estar en `permissions.allow` (o tener `autonomy: full`). Cada tool
queda auditada como cualquier otra.

- CLI: `/automation` (estado + próxima ejecución), `/workflows` (lista), `/workflow <name>` (ejecuta).
- API: `GET /v1/automation`, `POST /v1/automation/workflows/{name}/run`, `POST /v1/automation/tasks/{name}/run`.
- Env: `NOVA_AUTOMATION_ENABLED`, `NOVA_AUTOMATION_POLL_S`.