# Seguridad

## Principio

**El LLM propone; N.O.V.A. decide; el sistema ejecuta.** Ninguna seguridad depende del prompt del
modelo: la capa de decisión es código (`PermissionSystem` + `ToolRunner`) y toda ejecución queda
auditada en `logs/audit.nova.jsonl`.

```
LLM -> Tool Request -> PermissionSystem (allow/deny/ask) -> Validation -> (confirm) -> Tool -> Result -> LLM
```

## Modelo de permisos (ya implementado en PHASE 2)

`PermissionSystem.authorize(tool)` decide con esta precedencia: **deny > allow > autonomía**.

| Autonomía | Significado |
|---|---|
| `off` | Solo corren las herramientas listadas en `allow`. Todo lo demás denegado. |
| `ask` | `allow` corre directo, `deny` bloquea, el resto pregunta (confirm). |
| `full` | Todo lo no denegado corre directo. |

- Config: `config/config.yaml` -> `permissions` (y env `NOVA_PERMISSIONS_AUTONOMY`).
- Cada decisión y ejecución se registra en el audit log (JSONL rotativo, 2 MB x 3).
- En la **CLI**, un `ask` se resuelve con confirmación `[y/N]`. En la **API**, nunca se espera
  confirmación: un `ask` se **deniega** (`result.ok == false`); solo corre lo que está en `allow`.
- Ejemplos por defecto: `date_time` y `calculate` están en `allow`. El agente `system` usa `date_time`.

## Host tools (PHASE 6 — obligatorio)

Las host tools (`open_app`, `open_url`, `run`) **no se ejecutan nunca de forma arbitraria**: además
del `PermissionSystem` (allow/deny/ask), cada una tiene su propia barrera de seguridad en código,
**independiente de la autonomía configurada**.

### `open_app` — aplicaciones conocidas, por nombre

- La lista de apps vive en `config/config.yaml` -> `host.apps` (`nombre: ejecutable`). El LLM ofrece
  **solo el nombre**; si no está configurado -> fallo. **Nunca** acepta una ruta arbitraria del modelo.
- La ruta es configurada por el usuario (p. ej. `chrome: "C:\\Program Files\\...\\chrome.exe"`).

### `open_url` — solo http(s)

- Se permiten únicamente URLs `http://` / `https://` con host válido. Antes de abrir el navegador se
  rechazan esquemas peligrosos o inválidos: `file:`, `javascript:`, `data:`, `ftp:`, `ssh:`, URLs sin host, etc.
- Regla de oro: si el modelo propone una URL sospechosa, la herramienta devuelve error sin abrir nada.

### `run` — terminal por allowlist (denegado por defecto)

- **Allowlist de comandos**: solo se ejecutan comandos cuyo *nombre base* esté en `host.commands`
  (vacío = nada se ejecuta, **incluso con `autonomy: full`**). La allowlist es por comando base,
  comparando sin `.exe` y sin distinguir mayúsculas.
- **Sin shell**: la ejecución usa `subprocess.run([comando, *args], ...)` (lista, sin
  `shell=True`), por lo que no hay inyección de shell por argumentos.
- **Timeout configurable**: `host.timeout_s` (defecto 30 s) y override `timeout_s` por llamada; una
  orden que supera el límite se aborta y se reporta.
- **Captura controlada**: stdout/stderr se capturan (cap 100 KB por stream) y se devuelven con el
  `returncode`; sin herencia de terminal y sin comandos interactivos que bloqueen indefinidamente.
- **Bloqueo duro** (nunca anulable por configuración) de elevación, escape a shell y comandos
  destructivos: `runas`, `sudo`, `gsudo`, `cmd`, `powershell`, `pwsh`, `bash`, `sh`, `wsl`, `format`,
  `diskpart`, `bcdedit`, `shutdown`, `restart`, `reg`. La lista vive en
  `nova.tools.host.terminal.BLOCKED_COMMANDS`.
- **Sin elevación de privilegios**: la herramienta nunca lanza procesos como administrador.

### `host.roots` — límites de archivos y `cwd` (paso 2, obligatorio)

- **Roots de sistema de archivos**: solo se tocan rutas que **resuelven dentro** de un root de
  `host.roots` (paths absolutos, con o sin variables de entorno como `%USERPROFILE%`). Con `roots`
  vacía **no hay acceso al FS en absoluto**:
  - `read_file` / `list_files` (solo lectura) y `write_file` (solo dentro de un root, el directorio
    padre debe existir; no crea rutas fuera) fallan si no hay roots.
  - `run` puede fijar `cwd` (por llamada) o un `working_dir` por defecto, **ambos deben estar dentro
    de un root**; sin roots, `cwd` se rechaza.
- **Sin escapes de root**: cada ruta se expande y resuelve con `.resolve()` (normaliza `..` y resuelve
  symlinks/junctions) antes de comprobar si está dentro de un root — un intento de salir vía `../`,
  un symlink o un hardlink que apunte fuera no escapa del límite.
- `read_file` tiene tope de tamaño (100 KB) y `write_file` tope de contenido (1 MB).
- Elección de seguridad: **denegar por defecto** cualquier acceso al FS (roots vacíos) en lugar de
  permitir el sistema de archivos entero. La raíz se concede carpeta a carpeta.

### Denegada por defecto a nivel de permisos

Aunque la herramienta esté registrada, hace falta además que el `PermissionSystem` la permita:
con `autonomy: ask`, el CLI pregunta `[y/N]`; puedes dejarlas en `permissions.allow` para que corran
directo (p. ej. `open_app`, `open_url`) o en `permissions.deny` para bloquearlas siempre.

### Alcance

- Las host tools se registran solo en el CLI y en `nova-agent` (proceso local). La API
  (`nova-api`) solo las expone con `api.host_enabled: true` (PHASE 8), y en ese caso **exige token**
  y permanecen bajo el mismo Permission System + audit.
- Toda invocación (permitida o denegada) queda en el audit log con decisión, argumentos y resultado.

### Modelo de decisión de una host tool

```
LLM propone (ej. open_app {app: "chrome"})
  -> PermissionSystem decide (¿en allow/deny/ask?)
  -> ToolRunner ejecuta la herramienta
      -> open_app verifica host.apps / run verifica host.commands
      -> rutas de archivos y cwd verifican host.roots (path bounds)
  -> subprocess/webbrowser (alcance acotado) -> resultado
  -> Audit registra solicitud y resultado
```

## Niveles de riesgo de las herramientas (guía para PHASE 6+)

Cuando se añadan herramientas de host (Desktop Agent), clasificar así:

| Riesgo | Ejemplo | Política por defecto |
|---|---|---|
| Seguro | fecha/hora, listar contenido, cálculo, leer página | `allow` (o `ask`) |
| Confirmado | abrir una aplicación, abrir URL, ejecutar tests, organizar archivos | `ask` con confirmación |
| Alto | ejecutar comandos de terminal, borrar/renombrar, cambiar configuración del sistema | **denegado por defecto**; habilitar con `allow` explícito y por herramienta |
| Extremo | comandos como administrador, apagar/esperar, escritura fuera de raíces permitidas | **bloqueado** salvo regla explícita y revisada |

Reglas:

- **Nuevas herramientas = off por defecto.** Se añaden a `permissions.allow` una a una; nunca se
  amplían permisos para una carpeta/raíz entera sin motivo.
- Las herramientas de host limitan su alcance (p. ej. rutas permitidas, lista de aplicaciones
  conocidas, tiempo de espera en comandos) para reducir el impacto de un LLM equivocado.

## Exposición de red / acceso remoto (PHASE 8)

- Por defecto la API escucha en `127.0.0.1` (local, sin token necesario). Nada cambia en el flujo
  habitual: las rutas `/v1/*` corren auth solo cuando `api.token` está configurado.
- **Token (Bearer)**: con `api.token: <secreto>` en `config/config.yaml` (o `NOVA_API_TOKEN`), toda
  ruta `/v1/*` exige `Authorization: Bearer <secreto>`; sin él responde **401**. `healthz` y `/` quedan
  abiertos (la web sirve el client y deja el token al usuario).
- **Host tools remotas (`api.host_enabled`)** — activar solo si realmente quieres controlar el host
  desde el móvil/navegador. **Guarda dura (ADR-015)**: `create_app` **no arranca** si
  `host_enabled: true` sin token (`ValueError`). Nunca habilites esto sin token.
- **TLS obligatorio para Internet**: si el host/puerto se exponen a WAN, pon un proxy reverso con TLS
  delante (p. ej. Caddy `reverse_proxy` con certificado automático, nginx+certbot, o túnel Cloudflare).
  El token viaja en un header — **sin TLS va en claro y lo puede esnifar cualquiera en la red**.
  Regla: sin TLS la API no se publica a Internet.
- **CORS**: `api.cors_origins` controla qué orígenes pueden llamar al API desde otro sitio (defecto
  `"*"`). Para servir la web desde hosting estático (p. ej. Vercel) apunta el frontend con
  `?api=<base>` y deja `"*"` o la lista exacta; el **LLM nunca corre en serverless**, solo el cliente.
- **Sin confirmación humana en la API**: un `ask` se deniega (`result.ok == false`); solo corre lo de
  `permissions.allow`. Con `host_enabled` usa `autonomy: full` únicamente con `host.commands` y
  `host.roots` explícitos y auditando.
- El móvil (PHASE 8) accede vía la API con el Desktop Agent como brazo de ejecución del host, bajo
  las mismas reglas de permisos y audit.

## Web Tools (PHASE 9) — separación LLM / Web

- **El LLM no tiene primitivas de red**: internet solo se alcanza vía las tools `web_search`,
  `web_fetch` y `web_extract`. No existe `requests`/`httpx` desnudo en el Core; la única salida es
  `nova.tools.web.client.WebClient`, que aplica un orden fijo de barreras.
- **Off y denegadas por defecto**: `web.enabled: false` (defecto) no registra nada; con `true`, las
  tools siguen bajo el `PermissionSystem` (añadirlas a `permissions.allow` o confirmar en `ask`).
- **Por cada petición**, en orden:
  1. Validación estricta de URL: solo `http(s)`, con host, sin `usuario@`.
  2. `robots.txt` (parser RFC-9309) si `web.respect_robots` (true). `Disallow` bloquea; `Allow`
     desempata por prefijo más largo.
  3. Rate limit por host (`web.min_delay_s`) y timeouts (`web.timeout_s`, `web.max_redirects`).
  4. Caps: `web.max_bytes` corta la descarga; `web.max_chars` acota lo que llega al LLM.
  5. User-Agent identificable `NOVA/1.0 ...` (política de cortesía ante robots / who-are-you).
- **Riesgo**: consultas/páginas web pueden filtrar datos del usuario (tan solo ver qué URL se pide).
  N.O.V.A. nunca envía contenido propio de la conversación salvo el propio query/URL que pide el LLM.
- Elección (ADR-016): separar el acceso a red en una capa de tools controladas en lugar de darle al
  modelo un cliente HTTP genérico — si la extracción falla, degrada con un `ToolResult` de error y el
  resto del sistema (permisos, audit) aplica igual que con cualquier otra tool.

## Voz local (PHASE 10) — ADR-017

- **El audio jamás sale del dispositivo**: STT (Vosk, offline) y TTS (voces del sistema) corren en tu
  máquina; N.O.V.A. no sube grabaciones ni usa APIs de pago/cloud de voz.
- **Off por defecto** (`voice.enabled: false`) y extra opcional `[voice]` con imports lazy: no instalar
  voz no cambia el comportamiento y el arranque no pisa código tuyo si falta la lib.
- El micrófono captura **solo durante el bucle `/voice`** (mientras esperas, tras el prompt); la sesión
  de voz se cierra al salir del chat (`voice.close()`). Con `voice.wake_word` configurado, solo se
  procesa el mensaje que empieza por el wake word; el resto se ignora y no llega al chat.
- **La voz origina texto y nada más**: entra por el mismo `chat_line` que el teclado — memoria, router,
  Permission System, tools y audit aplican idénticos; no existe una vía de audio que salte los permisos.
- Los backends reales (`vosk`/`pyttsx3`/`sounddevice`) están **detrás del contrato** `STTProvider`/
  `TTSProvider`/`AudioSource`; si un backend falla devuelve un error capturado (`VoiceError`) y el bucle
  sigue. El `audit` no se ve afectado: la voz no ejecuta tools por sí misma.

## Privacidad (ADR-013)

- Local-first: conversaciones y datos permanecen en el dispositivo salvo consentimiento explícito.
- Sin telemetría recolectada por el proyecto; el audit log es local y se puede desactivar.
- No se envían conversaciones a servidores externos por defecto. Cuando una funcionalidad (p. ej.
  una API cloud opcional configurada por el usuario) envíe datos fuera, la interfaz debe decirlo.
- Secretos (tokens/keys) nunca en Git; se configuran por env (`NOVA_*`) o archivos ignorados
  (`.env`, `config/local.yaml`).

## Modo HYBRID y privacidad (PHASE 14, ADR-020)

El modo HYBRID es **opt-in y nunca rompe la privacidad por defecto**:

- `ai.privacy` tiene dos valores: `local_only` (defecto, **nada** sale del dispositivo) y
  `cloud_allowed` (permite fallback cloud **solo** para tareas `heavy` con recursos locales
  insuficientes). Con `local_only` el fallback cloud no existe, aunque haya `open_code.models`.
- N.O.V.A. **jamás instala OpenCode** ni lee/borra/escribe sus credenciales
  (`~/.local/share/opencode/auth.json` o env del propio OpenCode): el auth lo gestiona OpenCode.
  `nova-setup remove` solo elimina las secciones `ai`/`open_code` de su propio `config.yaml`.
- La **memoria queda siempre local** (SQLite + embeddings Ollama); no se suben bases de datos de
  memoria a cloud. A un proveedor cloud solo llega el prompt del turno y solo cuando la política lo
  permite.
- No se hardcodea una lista de modelos "gratuitos": si el coste de un modelo no es fiable, se
  reporta `unknown` y el usuario decide.
- El UI/`nova doctor` muestran el modo y la política actuales; `/route` y `GET /v1/route` muestran
  el proveedor elegido y su razón.

## Regla contra las alucinaciones de capacidad

N.O.V.A. solo afirma lo que puede hacer con sus herramientas. Si una capacidad no existe (p. ej.
correo, calendario), el agente responde que aún no la tiene, en lugar de fingirla. La lista real de
capacidades es `GET /v1/tools` / `/tools`, determinada por el registro, no por el LLM.

## Checklist de auditoría (para cada fase)

- [ ] ¿Cualquier ejecución de tool pasa por `ToolRunner` (permiso -> validación -> ejecución -> audit)?
- [ ] ¿Las nuevas tools están denegadas por defecto?
- [ ] ¿El LLM no tiene acceso directo a red/sistema si puede evitarlo (herramientas controladas)?
- [ ] ¿Se registra modelo, herramienta, resultado, error y duración sin datos sensibles?
- [ ] ¿La API puede quedar expuesta sin auth? Si sí, se documenta el riesgo y se bloquea por defecto.