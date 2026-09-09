# Setup

## Mantenimiento local

`nova-setup install`, `nova-setup update` y `nova-setup remove` gestionan el ciclo
de vida de la instalación. Los instaladores nuevos crean el entorno en
`%LOCALAPPDATA%\NOVA\venv` (Windows) o en el directorio de datos de usuario
equivalente. `.venv` sigue siendo el entorno de desarrollo del repositorio.
Consulta [lifecycle.md](lifecycle.md) para comandos, propiedad, catálogo remoto,
confirmaciones, rutas y conservación de instalaciones antiguas.

## Requisitos

- **Python** 3.11+ (verificado: 3.11.9).
- **Ollama** (instalado: 0.33.3). No hace falta tenerlo encendido a mano: al lanzar `nova`,
  `nova-api` o `nova-agent`, N.O.V.A. comprueba si responde y, si no, arranca `ollama serve`
  en segundo plano automáticamente (`ensure_ollama_running`). El instalador one-click también
  lo instala (winget en Windows) y comprueba que corra.
- Modelos descargados, p. ej.:
  ```powershell
  ollama pull llama3.1:8b
  ollama pull qwen2.5-coder:7b
  ollama pull nomic-embed-text   # embeddings para la memoria (PHASE 3)
  ```
  Si falta el modelo de embeddings, N.O.V.A. funciona igual con búsqueda por keywords.
  `nova-setup` recomienda e instala los modelos según tu RAM/GPU automáticamente.

## Instalación

### Automática (one-click, PHASE 13)

El instalador hace todo: crea el venv, instala el paquete, garantiza que Ollama esté
instalado y corriendo, detecta tu máquina (CPU/RAM/GPU), descarga los modelos
adecuados, escribe un `config.yaml` seguro y (opcionalmente) habilita el autostart:

```powershell
# Windows
.\install.ps1
# con voz local y autostart al iniciar sesión:
.\install.ps1 -Voice -Autostart
# provisiona el config pero salta la descarga de modelos (~GBs):
.\install.ps1 -NoModels
```

```bash
# Linux/macOS
./install.sh
./install.sh --voice --autostart
```

El bootstrapping correspondiente (`nova-setup`) también se puede invocar a mano:

```powershell
.\.venv\Scripts\nova-setup        # asistente guiado (detecta, pregunta, provisiona)
.\.venv\Scripts\nova-setup auto   # sin interacción, con defaults seguros
.\.venv\Scripts\nova-setup auto --voice --web --config ruta/config.yaml
.\.venv\Scripts\nova-setup auto --no-models   # config sí, descargas no
.\.venv\Scripts\nova-setup doctor             # diagnóstico completo (ver abajo)
.\.venv\Scripts\nova-setup repair            # re-audita y repara la instalación
.\.venv\Scripts\nova-setup autostart --enable 1   # (o --disable 0)
```

### Experiencia de instalación por fases (PHASE 14.2)

El asistente guiado (con o sin argumentos, o `nova-setup install`) recorre 11 fases visibles:
**System → Dependency Check → Install Missing → Verification → Hardware → Ollama → OpenCode →
Models → Config → Autostart/Voice (si lo pides) → Ready**. Cada fase usa la capa `Terminal`
(`nova/setup/terminal.py`): secciones, tabla de dependencias, spinner y símbolos `[✓]/[✗]/[!]`
con fallback ASCII automático cuando la consola no soporta Unicode (o con `--ascii`).

**Dependencias gestionadas** (`nova/setup/dependencies.py`): cada dependencia es un objeto con
nombre, propósito, binario/módulo, versión mínima, si es obligatoria, plataforma y método de
instalación — siempre derivado de lo que N.O.V.A. **realmente** usa:

- **Obligatorias (core)**: Python 3.11+, venv, pip, `httpx`, `pydantic`, `pydantic-settings`,
  `PyYAML`, `fastapi`, `uvicorn` y **Ollama**.
- **Opcionales**: `opencode` (solo HYBRID; nunca se auto-instala), voz (`numpy`, `vosk`,
  `pyttsx3`, `sounddevice`), dev (`pytest`, `git`).

La detección nunca asume que "estar en PATH" significa que funciona: comprueba el binario **y**
su versión (p. ej. `ollama --version`). Si falta una dependencia obligatoria de Python, se ofrece
instalarla con pip del venv activo (o se instala sola en `auto`/`repair`); Ollama se instala con
winget o el instalador oficial (solo cuando el usuario lo confirma o en modo `auto`); **OpenCode
nunca se instala ni se le tocan las credenciales**.

**`nova-setup repair [--non-interactive] [--config ruta]`** re-audita y repara: instala las
dependencias de Python que falten, valida el venv (nunca lo recrea solo), reinicia Ollama si está
instalado pero apagado, re-aplica defaults seguros de provisionado solo donde no hay valores
propios del usuario y (si Ollama responde) avisa/instala los modelos recomendados que falten.

**Salida no interactiva / máquina**: los comandos `doctor`, `install`, `repair`, `auto` y `status`
aceptan `--non-interactive` (sin prompts ni spinners) y `--json` (una línea JSON por evento, sin
decoración) para pipelines y CI:
`nova-setup doctor --json | jq -r 'select(.level=="fail") | .message'`.

`nova-setup doctor` es ahora una **auditoría completa**: sistema (OS/CPU/RAM/GPU/Python), tabla de
dependencias con estado (OK/outdated/missing/optional), Ollama y modelos recomendados que falten,
OpenCode (modo AI, privacidad, providers, modelos, **autenticación**) y plan de hardware, con el
veredicto final `READY` / `NOT READY`. La autenticación de OpenCode se reporta solo como
`available` / `not_configured` / `unknown` mediante comprobaciones de existencia: **nunca** se
leen ni imprimen ficheros de credenciales.

```powershell
.\.venv\Scripts\nova-setup doctor --no-color    # consola sin ANSI (o $env:NO_COLOR=1)
.\.venv\Scripts\nova-setup doctor --ascii       # símbolos ASCII en consolas limitadas
.\.venv\Scripts\nova-setup repair --non-interactive

Los defaults que escribe respetan el modelo de seguridad: `permissions.autonomy=ask`,
`allow` solo con `date_time`, `calculate`, `list_dir`, `remember`, `memory_search`;
`web`/`voice`/`automation` apagados; plugins seguros (`text_tools`/`units`) activados.
**Nunca sobreescribe** valores que ya tuvieras en tu `config.yaml`. Si `voice` está
instalada, el asistente puede saludarte por TTS al terminar ("Bienvenido, señor...").

Dentro del chat también tienes `/setup` (lanzar el asistente interactivo), `/doctor`
y su alias `/status` para diagnosticar sin salir.

### Instalar desde la web

Cuando `nova-api` está corriendo en tu máquina, la interfaz web (estilo ChatGPT, en
`web/`) incluye el asistente **"Instalar N.O.V.A."** que hace lo mismo que el wizard CLI:
detecta el equipo, activa/desactiva plugins/voice/web/automation/autostart, escribe
`config/config.yaml` (sin tocar valores que ya tengas) y descarga los modelos que falten
con barra de progreso en vivo (SSE). Endpoints: `GET /v1/setup/status`,
`POST /v1/setup/provision`, `GET /v1/setup/pull`, `POST /v1/setup/autostart`,
`POST /v1/setup/greeting`. Estas rutas escriben en el host, así que si la API escucha fuera
de localhost requieren `api.token` (403 en caso contrario).

### Manual

Desde la raíz del proyecto:

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install --upgrade pip
.\.venv\Scripts\python -m pip install -e ".[dev]"
```

Instala el paquete `nova` en modo editable + dependencias de desarrollo (`pytest`).

### Voice (PHASE 10, opcional)

Voz **local** (STT/TTS OSS, ADR-017 — el audio jamás sale del dispositivo). Instala el extra y un modelo
Vosk pequeño:

```powershell
.\.venv\Scripts\python -m pip install -e ".[dev,voice]"
# Descarga un modelo de voz para tu idioma:
#   https://alphacephei.com/vosk/models  -> p. ej. vosk-model-small-es-0.42
Invoke-WebRequest -Uri https://alphacephei.com/vosk/models/vosk-model-small-es-0.42.zip -OutFile vosk-model.zip
Expand-Archive -Path vosk-model.zip
```

Configura `config/config.yaml`:

```yaml
voice:
  enabled: true
  stt:
    backend: vosk
    model_dir: "vosk-model-small-es-0.42"   # ruta del modelo descargado
    language: es
  tts:
    backend: pyttsx3                        # voces del sistema (SAPI5/eSpeak)
  wake_word: null                           # opcional: p. ej. "nova"
```

Y en el chat: `/voice` inicia el bucle (habla y pulsa ENTER al terminar; `/voice stop` sale) y
`/say <texto>` habla una línea con TTS. Con `wake_word` configurado solo reacciona a mensajes que
empiecen por esa palabra. Sin el extra instalado, N.O.V.A. funciona igual (los backends están detrás de
import lazy y degradan con un aviso).

## Configuración

Copia/edita `config/config.yaml`:

```yaml
llm:
  provider: ollama
  base_url: http://localhost:11434
  default_model: llama3.1:8b
  embedding_model: nomic-embed-text
  temperature: 0.7
```

Cualquier valor se puede sobrescribir con variables de entorno `NOVA_*`:

```powershell
$env:NOVA_LLM_DEFAULT_MODEL = "qwen2.5-coder:7b"   # PowerShell
set NOVA_LLM_DEFAULT_MODEL=qwen2.5-coder:7b          # cmd
```

### Modo de IA: LOCAL (defecto) y HYBRID (PHASE 14)

N.O.V.A. es **local-first** (ADR-013): el modo por defecto es LOCAL (solo Ollama, offline, sin
cuenta, sin llamadas externas). El modo HYBRID añade un **fallback cloud opcional** a través de
OpenCode — N.O.V.A. **nunca instala OpenCode** ni gestiona tus credenciales: tú instalas y
ejecutas OpenCode (opencode.ai) y N.O.V.A. habla con su servidor local (`http://127.0.0.1:4096`).

```yaml
ai:
  mode: local        # local (defecto) | hybrid
  privacy: local_only  # local_only (defecto) | cloud_allowed
open_code:
  enabled: false
  base_url: http://127.0.0.1:4096   # servidor de OpenCode
  timeout_s: 120.0
  default_model: ""                 # modelo cloud genérico (formato "proveedor/modelo")
  models: {}                        # catálogo cloud por rol (coding, reasoning...)
model_router:
  min_ram_gb: 8.0
  battery: true
  strategy: local-first
```

- **LOCAL + `local_only`** (defecto): todo queda en Ollama. Igual que hasta ahora, sin cambios.
- **HYBRID + `cloud_allowed`**: solo las tareas **`heavy`** con recursos locales insuficientes
  (RAM/VRAM/batería) pueden ir a cloud. Las tareas `simple`/`general` nunca salen del dispositivo.
  El router decide proveedor + modelo y lo reporta (`/route` en el chat, `GET /v1/route` en la API).
- En `hybrid`, si el servidor de OpenCode no responde o el proveedor falla, N.O.V.A. cae de nuevo a
  local con un aviso en el log (fallback bidireccional).
- Variables de entorno: `NOVA_AI_MODE`, `NOVA_AI_PRIVACY`, `NOVA_OPEN_CODE_ENABLED`,
  `NOVA_OPEN_CODE_BASE_URL`, `NOVA_MODEL_ROUTER_STRATEGY`.

`nova setup` te deja elegir Local/Hybrid/Configure later; `nova doctor` muestra el modo, la política
de privacidad y el estado de OpenCode.

## Ejecución

CLI interactivo (si Ollama está instalado pero apagado, se arranca solo):

```powershell
.\.venv\Scripts\nova
# o
.\.venv\Scripts\python -m nova
```

API REST + interfaz web (PHASE 4):

```powershell
.\.venv\Scripts\nova-api
# o
.\.venv\Scripts\python -m nova.api.server
```

Abre `http://127.0.0.1:8000/` (chat web) o `http://127.0.0.1:8000/docs` (OpenAPI). Host/puerto en `config/config.yaml` -> `api` o con `NOVA_API_HOST` / `NOVA_API_PORT`.

Comandos dentro del chat: `/exit`, `/clear`, `/models`, `/model <nombre>`, `/tools`, `/plugins`, `/run <tool> <json>`, `/remember <text>`, `/memory [query]`, `/agents`, `/agent <nombre> <texto>`, `/automation`, `/workflows`, `/workflow <nombre>`, `/setup`, `/doctor`, `/status`, `/help`.

### Automatización (PHASE 12)

**Off por defecto**. Para que el scheduler corra en segundo plano y definir tareas/workflows, edita
`config/config.yaml`:

```yaml
automation:
  enabled: true          # el scheduler arranca en CLI, nova-agent y API
  poll_s: 1.0            # frecuencia de comprobación del scheduler
  tasks: []              # tareas con schedule (interval_s o at "HH:MM") + tool/agent/workflow
  workflows: []          # workflows multi-paso (tool/agent anidados, on_error stop|continue)
```

En modo desatendido un permiso `ask` se **deniega** (no hay humano que confirmar): añade a
`permissions.allow` las tools que vayan a ejecutar tus tareas/workflows. Ver el estado con
`/automation` (CLI) o `GET /v1/automation` (API); ejecuta un workflow a mano con `/workflow <nombre>`.

Carga **off por defecto**. Para activar los built-in o un directorio propio, edita `config/config.yaml`:

```yaml
plugins:
  enabled:
    - text_tools
    - units
  dir: null            # o ruta a una carpeta con módulos *_plugin.py que exponen PLUGIN
```

Las tools de los plugins son tools normales: para que el LLM las ejecute sin preguntar, añádelas a
`permissions.allow` (p. ej. `text_slugify`, `convert_length`) o sube `autonomy`. Ver los nombres con
`/plugins` en el chat o `GET /v1/plugins` en la API.

### Agentes (PHASE 5)

```powershell
# listar los presets disponibles
/agents
# pasar un texto a un agente (las tools las propone el LLM, N.O.V.A. las ejecuta)
/agent research qué sabemos del proyecto?
/agent coding refactoriza nova/tools/standard.py
```

Cada agente mantiene su propia sesión compartiendo memoria y permisos. Para que el LLM pueda
ejecutar tools sin preguntar, añade sus nombres a `permissions.allow` en `config/config.yaml`
o sube el nivel de autonomía:

```yaml
permissions:
  autonomy: full   # off | ask | full  (full = todo lo no denegado corre directo)
  allow:
    - date_time
    - calculate
```

### Ejemplos de la API

```powershell
# Salud
Invoke-RestMethod -Uri http://127.0.0.1:8000/healthz
# Crear sesión y chatear
$s = Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/v1/sessions
$body = @{ message = "Hola" } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/v1/sessions/$($s.session_id)/chat" -ContentType "application/json" -Body $body
```

API de agentes:

```powershell
# listar agentes
Invoke-RestMethod -Uri http://127.0.0.1:8000/v1/agents
# crear una sesión ligada a un agente (responde con "steps")
$s = Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/v1/sessions -ContentType "application/json" -Body '{"agent":"research"}'
# turno con el agente persistente por nombre
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/v1/agents/coding/chat -ContentType "application/json" -Body '{"message":"¿cuánto es 2+2?"}'
```

### Ejemplos de herramientas

```text
/run calculate {"expression":"2+2"}
/run date_time {"format":"%Y-%m-%d %H:%M:%S"}
/run list_dir {"path":"C:/Users/Usuario/Desktop"}
```

Si la herramienta no está en `permissions.allow`, el Permission System preguntará antes de ejecutarla (`[y/N]`).

## Red y acceso externo (PHASE 8)

La API escucha en `api.host` / `api.port` (por defecto `127.0.0.1` — solo local, sin token).

### Modo local (defecto) — ninguna configuración

- Solo tu ordenador accede a `http://127.0.0.1:8000/`. No hace falta token (auth de `/v1/*` solo
  se activa si configuras `api.token`).

### Modo LAN / remoto (móvil propio)

```yaml
api:
  host: 0.0.0.0            # escucha en toda la red local
  token: "cambia-este-secreto"   # obligatorio si le pones host_enabled, recomendado siempre
  host_enabled: true       # opcional: expone las host tools a la API (¡móvil -> API -> host!)
```

- Cada ruta `/v1/*` exigirá `Authorization: Bearer <token>`. La web local la usas igual: metes el
  token en el campo del encabezado (se guarda en `localStorage` del navegador).
- Desde el móvil abre `http://<IP-del-PC>:8000/` y guarda el mismo token.

### Hosting estático (frontend en Vercel/Netlify/github.io, backend local)

El frontend es un **cliente estático** (carpeta `web/`, sin build) : el LLM, la memoria y
las tools viven en tu ordenador, nunca en serverless. Despliega la carpeta `web/` en tu
hosting y añade tu origen a la API:

1. Pack: `web/` contiene `index.html`, `style.css` y `app.js`. En Vercel basta con el
   `vercel.json` del repo (`"rootDirectory": "web"`, sin framework) e importar el repo.
2. En `config/config.yaml`: `api.cors_origins` con tu dominio (p. ej. `https://nova-app.vercel.app`) y `api.token` con un secreto.
3. Abre la web publicada con `?api=<url-de-tu-pc>` (o usa `localStorage.setItem("nova.apiBase", ...)`); introduce el token en el campo del encabezado.
4. La API local sirve la misma UI en `/` (así también funciona sin desplegar nada).

> ⚠️ **Internet sin TLS = jamás.** Si expones el puerto a la WAN, pon delante un proxy reverso con
> TLS (Caddy/nginx/Cloudflare); el token va en un header y sin TLS se esnifa. Detalles y ejemplos en
> `docs/security.md`.

> ⚠️ **`api.host_enabled: true` sin `api.token` impide arrancar la API** (`ValueError`, ADR-015). Las
> host tools (`run`, archivos...) solo se exponen con un token, `permissions.allow` explícito y
> `host.commands`/`host.roots` definidos — ver `docs/security.md`.

## Tests

```powershell
.\.venv\Scripts\python -m pytest
```

El test de integración contra Ollama se omite automáticamente si Ollama no responde.

## Logs

- `logs/nova.log` — logging de la aplicación (archivo rotativo, 2 MB x 3 backups). Nivel configurable en `config/config.yaml` -> `logging.level`.
- `logs/audit.nova.jsonl` — audit de cada ejecución de herramienta (decisión, argumentos, resultado, duración; rotativo 2 MB x 3).
- `memory/nova.db` — base SQLite de memoria (hechos + trascripción de conversación; no versionar).
