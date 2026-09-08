# N.O.V.A.

**N.O.V.A. = Neural Operations & Virtual Assistant**

Asistente personal de IA moderno, **gratuito, local-first, privado y extensible**. Inspirado conceptualmente en asistentes como JARVIS, pero completamente original. La inteligencia de producto vive en el sistema (orquestación, memoria, herramientas, agentes, permisos, routing), no en un modelo propietario: N.O.V.A. **es sistema + modelos existentes** (locales, vía Ollama en primer lugar).

Estado actual: **PHASE 13 — Setup/Install Wizard** (instalador automático `nova-setup` con detección de máquina RAM/GPU real, recomendación e instalación de los modelos Ollama adecuados, provision auto de `config.yaml` seguro, asistente guiado de primer arranque, autostart opcional y `nova doctor`). Añadida sobre la PHASE 12 — Automatización (configuración, proveedor Ollama, conversación, contexto, logging, sistema de herramientas con permisos y audit, memoria persistente con recuperación por embeddings, API REST + interfaz web, agentes que seleccionan herramientas con el LLM como proponente, host tools seguras `open_app`/`open_url`/`run` + tools de archivos acotadas por `host.roots`, entry point `nova-agent`, **routing automático de modelo**, **acceso remoto con token + CORS + host tools**, **web tools controladas** con robots/rate-limit/separación LLM/Web, **voz 100% local** con STT Vosk + TTS pyttsx3 + wake word opcional — el audio nunca sale del dispositivo, **plugins** que añaden tools bajo el mismo Permission System + audit, y **automatización** con scheduler de tareas programadas + workflows multi-paso bajo los mismos permisos). Pendiente de la fase: nada — ver [docs/roadmap.md](docs/roadmap.md).

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

### Instalador automático (PHASE 13) — `nova setup`

El instalador detecta tu máquina (RAM real y VRAM de GPU en Windows), comprueba
Ollama, **descarga los modelos adecuados a tu hardware** y escribe un
`config/config.yaml` seguro y listo para usar — todo en un comando:

```powershell
.\.venv\Scripts\nova-setup          # asistente guiado (recomendado)
.\.venv\Scripts\nova-setup auto     # bootstrap no interactivo
.\.venv\Scripts\nova-setup doctor   # diagnóstico de salud
.\.venv\Scripts\nova-setup autostart --enable 1   # arrancar N.O.V.A. al iniciar sesión
```

Lo que hace, según el hardware detectado (heurística conservadora):

| RAM | GPU | Modelo por defecto | Stack |
|---|---|---|---|
| < 12 GB | — | `llama3.2:3b` | embedding + 3B + 1B |
| ≥ 12 GB | — o ≥ 8 GB VRAM | `llama3.1:8b` | embedding + 8B + 1B + coder 7B |

Los defaults que escribe **respetan el modelo de seguridad** (ADR-013): herramientas
denegadas por defecto, `web`/`voice`/`automation` apagados, plugins seguros
(`text_tools`/`units`) activados, y nunca sobreescribe un valor que ya tuvieras en
tu `config.yaml`. También puedes lanzarlo desde dentro del chat con `/setup` y
diagnosticar con `/doctor`.

Voz (opcional, PHASE 10 — 100% local, sin APIs de pago):

```powershell
.\.venv\Scripts\python -m pip install -e ".[dev,voice]"   # vosk + pyttsx3 + sounddevice
```

Interfaz web y API REST:

```powershell
.\.venv\Scripts\nova-api     # sirve http://127.0.0.1:8000/ (UI) y /docs (OpenAPI)
.\.venv\Scripts\nova-agent   # proceso local del Desktop Agent (PHASE 6; remoto opcional en PHASE 8)
```

Persiste que Ollama esté corriendo (`ollama serve`) y que tengas al menos un modelo, p. ej. `ollama pull llama3.1:8b`. Para la memoria (PHASE 3) además un modelo de embeddings: `ollama pull nomic-embed-text` (si falta, N.O.V.A. funciona igual con búsqueda por keywords). Para el routing de PHASE 7, descarga los que quieras en el catálogo: `ollama pull llama3.2:1b` (small) y `ollama pull qwen2.5-coder:7b` (coding).

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

## Model Router (PHASE 7)

El modelo se elige **por turno** según la tarea, los recursos disponibles y la privacidad (ADRs 013/014). El catálogo vive en `config/config.yaml` -> `llm.models` (`small`/`local`/`coding`/`vision`/`embedding`) y nunca en código; si un rol no está configurado se usa `default_model`.

```text
/route escribe una función en python   # muestra task_kind, role y modelo elegido
/catalog                                # lista roles -> modelos
```

Con RAM por debajo de `model_router.min_ram_gb` (o batería baja sin AC), tareas pesadas/coding caen a `small`. En la API, `POST /v1/route` expone la misma decisión y las sesiones rutean por turno salvo que se pase `model` explícito.

## Host (PHASE 6, paso 1)

Herramientas de control del ordenador, **denegadas por defecto** y bajo el mismo Permission System + audit (CLI y `nova-agent`; en la API solo con `api.host_enabled: true` y token — PHASE 8):

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

## Acceso remoto (PHASE 8)

Local de serie (`api.host: 127.0.0.1`), sin token. Para acceder desde el móvil/LAN o publicar un frontend estático:

```yaml
api:
  host: 0.0.0.0              # LAN/móvil
  token: "cambia-este-secreto"   # cada ruta /v1/* exigirá Authorization: Bearer <token>
  host_enabled: true         # opcional: expone host tools (run/archivos) a la API — solo con token
  cors_origins: "*"          # hosting estático (p. ej. Vercel) apuntando con ?api=<base>
```

- **Guarda dura (ADR-015)**: `host_enabled: true` sin token impide arrancar la API (`ValueError`).
- **Nunca a Internet sin TLS**: usa un proxy reverso con certificado (Caddy/nginx/Cloudflare); sin TLS el token viaja en claro.
- El **LLM nunca corre en serverless**: el frontend es un cliente estático; el backend (LLM + memoria + tools) vive solo en tu dispositivo.
- Guía completa en [docs/setup.md](docs/setup.md) y límites en [docs/security.md](docs/security.md).

## Web Tools (PHASE 9)

El LLM accede a Internet **solo** a través de tools controladas (separación LLM / Web, ADR-016): `web_search`,
`web_fetch` y `web_extract`. **Off por defecto** (`web.enabled: false`); al activarlas siguen bajo el
Permission System (añádelas a `permissions.allow` o confírmalas en `ask`):

```yaml
web:
  enabled: true
  min_delay_s: 1.0       # rate limit por host
  max_chars: 4000        # lo que ve el LLM por petición
```

```text
/run web_search {"query":"mejores practicas python"}
/run web_fetch {"url":"https://docs.python.org/es/3/"}
/run web_extract {"url":"https://docs.python.org/es/3/"}
```

Cada petición pasa por: validación de URL (solo http(s), sin userinfo) -> `robots.txt` -> rate limit por
host -> caps de bytes/caracteres, con User-Agent identificable. La búsqueda usa `web.search_url`
(DuckDuckGo sin API key por defecto; permite SearXNG/Brave). Detalle en [docs/tools.md](docs/tools.md).

## Plugins (PHASE 11)

Los plugins añaden **tools** (nunca permisos) bajo el mismo `PermissionSystem` + audit (ADR-018).
**Off por defecto** (`plugins.enabled` vacío). Actívalos en `config/config.yaml`:

```yaml
plugins:
  enabled:
    - text_tools      # base64 encode/decode, slugify, UUID
    - units           # conversión de longitud/peso/temperatura
  dir: null           # o carpeta con módulos *_plugin.py que expongan un objeto PLUGIN
```

```text
/plugins                                # lista plugins cargados y sus tools
/run text_slugify {"text":"Hola Mundo!"}
/run convert_temperature {"value":100,"from_unit":"c","to_unit":"f"}
```

Las tools de plugins se deniegan por defecto igual que cualquier otra: añádelas a `permissions.allow`
o confímalas en `ask`. En la API: `GET /v1/plugins`. Detalle en [docs/tools.md](docs/tools.md).

## Automatización (PHASE 12)

Tareas programadas (scheduler) y workflows multi-paso que corren bajo los **mismos** permisos que el
chat: la automatización amplía el *horario*, nunca los permisos (ADR-019). **Off por defecto**
(`automation.enabled: false`).

```yaml
automation:
  enabled: true
  poll_s: 1.0
  tasks:
    - name: heartbeat
      schedule: {interval_s: 60}   # o at: "09:00" (una vez al día)
      tool: date_time              # tool | agent(+text) | workflow
  workflows:
    - name: my_workflow
      steps:
        - tool: calculate
          args: {expression: "2+2"}
        - agent: general
          text: "Resume el resultado."
          on_error: continue       # stop (defecto) o continue por paso
```

En modo desatendido un permiso `ask` se **deniega** de forma segura: añade a `permissions.allow` las
tools que vayan a ejecutar tus tareas/workflows.

```text
/automation           # estado del scheduler + próximas ejecuciones
/workflows            # lista los workflows configurados
/workflow my_workflow # ejecuta un workflow a mano
```

En la API: `GET /v1/automation`, `POST /v1/automation/workflows/{name}/run`,
`POST /v1/automation/tasks/{name}/run`. Detalle en [docs/tools.md](docs/tools.md).

## Voice (PHASE 10)

Voz **100% local** (ADR-017): el audio jamás sale de tu dispositivo. STT con **Vosk** (modelo
offline) y TTS con **pyttsx3** (voces del sistema). **Off por defecto** (`voice.enabled: false`).

```text
/voice               # bucle: habla, pulsa ENTER al terminar; /voice stop para salir
/say hola            # habla una línea con TTS
```

La voz entra por el **mismo chat** que el teclado (memoria + router + tools + audit): vía `/voice`,
el transcript se envía y la respuesta se habla. Opcional: `voice.wake_word: "nova"` hace que solo
reaccione a mensajes que empiecen por esa palabra. Requiere instalar el extra `[voice]` y descargar un
modelo Vosk; sin ellos N.O.V.A. funciona igual y `/voice` avisa de qué falta.

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
config/config.yaml    Configuración externa (modelos, permissions, audit, memory, api, host, router; nunca en código)
nova/
  core/              Config, logging, contexto de conversación (ChatSession), audit log
  llm/               LLMProvider (interfaz) + OllamaProvider (chat y embeddings) + registro + ResourceManager + ModelRouter (PHASE 7)
  tools/             Herramientas (BaseTool + schemas) + Permission System + runner
    host/            Host tools seguras: open_app/open_url/run + files acotados (PHASE 6)
    web/             Web tools controladas: web_search/web_fetch/web_extract (PHASE 9)
  plugins/           Interfaz Plugin + cargador + built-ins text_tools/units (PHASE 11)
  automation/        Scheduler de tareas + workflows multi-paso bajo los mismos permisos (PHASE 12)
  voice/             Voice local: STT Vosk + TTS pyttsx3 + mic — 100% local (PHASE 10)
  setup/             Setup/instalador: detección de máquina, instalación de modelos,
                     provision de config, asistente JARVIS, autostart (PHASE 13)
  memory/            Memoria persistente (SQLite) + recuperación por embeddings (PHASE 3)
  agents/            Agent + 5 presets paramétricos y tool-call por JSON estructurado (PHASE 5)
  desktop/           Processo local nova-agent (base del Desktop Agent, PHASE 6)
  api/               API REST (FastAPI) + interfaz web (PHASE 4; auth + remoto en PHASE 8)
  cli/               Interfaz de conversación
docs/                Documentación del proyecto
tests/               Tests pytest
```

Arquitectura actual:

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
| [docs/tools.md](docs/tools.md) | Catálogo de herramientas (estándar, memoria, host, web, plugins, automatización) |
| [docs/models.md](docs/models.md) | Modelos y política de selección |
| [docs/troubleshooting.md](docs/troubleshooting.md) | Problemas comunes |
| [docs/api.md](docs/api.md) | APIs internas y externas |
| [CHANGELOG.md](CHANGELOG.md) | Historial de cambios |