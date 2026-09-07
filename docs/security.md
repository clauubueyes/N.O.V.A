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

- Las host tools se registran **solo** en el CLI y en `nova-agent` (proceso local). La API
  (`nova-api`) **no** las expone en este paso: sin funcionalidad remota hasta PHASE 8.
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

- Hoy la API escucha en `127.0.0.1` (local). Abrir a la LAN (`api.host: 0.0.0.0`) es decisión del usuario.
- Antes de exponer a Internet se requiere: **autenticación (token)**, consideración de TLS (proxy
  reverso) y que el host solo ejecute tools bajo permisos. Sin auth, la API no debe publicarse.
- El móvil (PHASE 8) accede vía la API con el Desktop Agent como brazo de ejecución del host, bajo
  las mismas reglas de permisos y audit.

## Privacidad (ADR-013)

- Local-first: conversaciones y datos permanecen en el dispositivo salvo consentimiento explícito.
- Sin telemetría recolectada por el proyecto; el audit log es local y se puede desactivar.
- No se envían conversaciones a servidores externos por defecto. Cuando una funcionalidad (p. ej.
  una API cloud opcional configurada por el usuario) envíe datos fuera, la interfaz debe decirlo.
- Secretos (tokens/keys) nunca en Git; se configuran por env (`NOVA_*`) o archivos ignorados
  (`.env`, `config/local.yaml`).

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