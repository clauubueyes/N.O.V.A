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