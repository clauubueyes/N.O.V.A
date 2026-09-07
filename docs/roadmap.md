# Roadmap

Fases progresivas. Cada fase pasa por: ANALIZAR -> IMPLEMENTAR -> TESTEAR -> REVISAR -> DOCUMENTAR.
La numeración del proyecto se mantiene (PHASE 0-5 completas); las fases de la visión de producto se mapean a PHASE 6-12 sin romper el historial.

## Mapeo visión -> fases del proyecto

| Visión (propuesta original) | Proyecto (numeración real) | Estado |
|---|---|---|
| PHASE 0 Discovery | PHASE 0 Discovery | ✅ |
| PHASE 1 N.O.V.A. Core | PHASE 1 N.O.V.A. Core | ✅ |
| PHASE 2 Ollama integration | PHASE 1 (proveedor + registry) | ✅ |
| PHASE 3 Tool system | PHASE 2 Tool System | ✅ |
| PHASE 4 Permissions & Security | PHASE 2 (Permission System + audit) | ✅ |
| PHASE 5 Memory | PHASE 3 Memory | ✅ |
| PHASE 6 Web interface | PHASE 4 Interface (API + web) | ✅ |
| PHASE 10 Agents | PHASE 5 Agents | ✅ |
| PHASE 7 Desktop Agent | **PHASE 6 — Desktop Agent** | ⏳ paso 2 hecho |
| PHASE 9 Model Router | **PHASE 7 — Model Router + Resource Manager** | ✅ |
| PHASE 8 Remote connectivity | **PHASE 8 — Acceso remoto + auth + frontend** | ⏳ pasos 1-2 hechos |
| — (web tools) | **PHASE 9 — Web Tools** | ✅ |
| PHASE 11 Voice | **PHASE 10 — Voice (STT/TTS local)** | pendiente |
| PHASE 12 Plugins | **PHASE 11 — Plugins** | pendiente |
| PHASE 13 Advanced automation | **PHASE 12 — Automatización avanzada** | pendiente |

## PHASE 0 — Discovery ✅

- Análisis del repositorio: estaba vacío (solo `.git`, sin commits).
- Entorno detectado: Python 3.11.9, Node 24, Ollama 0.33.3 con `llama3.1:8b`, `qwen2.5-coder:7b`, `nomic-embed-text`.
- Decisiones clave documentadas en [decisions.md](decisions.md).

## PHASE 1 — N.O.V.A. Core ✅

- [x] Configuración externa (YAML + env), sin modelos hardcodeados.
- [x] Ollama provider (desacoplado vía `LLMProvider`).
- [x] Gestión de modelos (listar, switch de modelo en sesión).
- [x] Conversación (CLI interactiva).
- [x] Context manager (`ChatSession` con historial acotado).
- [x] Logging (consola + archivo rotativo).

## PHASE 2 — Tool System ✅

- [x] Sistema de herramientas (`nova.tools`).
- [x] Schemas Pydantic por herramienta.
- [x] Validación de argumentos.
- [x] Ejecución y resultados tipados.
- [x] **Permission System**: niveles de autonomía (`off`/`ask`/`full`), allow/deny.
- [x] Registro (audit log) de todas las ejecuciones.

> Estado: la selección automática de herramientas por parte del LLM se aborda con Agents (PHASE 5).

## PHASE 3 — Memory ✅

- [x] Memoria de conversación (persistente por sesión en `transcripts`).
- [x] Memoria persistente (SQLite local, `memory/nova.db`; sin vector DB externa — ADR-005).
- [x] Contexto y recuperación (embeddings locales `nomic-embed-text` + similitud coseno, con fallback a keywords).
- [x] Herramientas `remember` y `memory_search` (bajo el Permission System).
- [x] Inyección automática de contexto relevante en el chat.

> Nota: la suma/resumen semántico de conversaciones largas (compresión de memoria) se puede abordar como etapa de "compaction" en PHASE 9 (Web Tools / tareas de agente).

## PHASE 4 — Interface ✅

- [x] API REST (FastAPI) en `nova/api`: chat stateless y por sesión, modelos, herramientas, memoria, salud.
- [x] Interfaz web (HTML/JS vanilla servida por la API en `/`).
- [x] Comunicación con el Core: la API reutiliza `ChatSession`, `MemoryService` y `ToolRunner` con el mismo Permission System y audit.
- [x] Documentación OpenAPI automática en `/docs`.

> Nota: interfaz de escritorio (sistemas) se puede abordar más adelante como cliente de esta API; la web cubre el caso funcionante.

## PHASE 5 — Agents ✅

- [x] General Agent.
- [x] Coding Agent.
- [x] Research Agent.
- [x] System Agent.
- [x] Automation Agent.
- [x] Protocolo de tool-call por JSON estructurado (provider-agnóstico).
- [x] Agentes como presets paramétricos (`AgentPreset`) sobre una única clase `Agent`.
- [x] Integración CLI (`/agents`, `/agent <name> <text>`), API y web con pasos visibles.

> Los agentes se implementan como **presets** (perfil + conjunto de tools + prompt) sobre
> una única clase `Agent`: el LLM **propone** llamadas con JSON estructurado y N.O.V.A.
> **decide** vía el `ToolRunner` (Permission System + audit) — ver [decisions.md](decisions.md) ADR-012.

## PHASE 6 — Desktop Agent (control del ordenador, seguro) ⏳ paso 2 hecho

- [x] Herramientas de host bajo el Permission System: `open_app` (aplicaciones configuradas por nombre) y `open_url` (solo `http(s)`).
- [x] Terminal seguro `run`: allowlist de comandos (`host.commands`; vacía = denegado por defecto, incluso con `autonomy: full`), sin shell, timeout configurable, salida capturada y bloqueo duro de elevación/destructivos.
- [x] `nova-agent`: entry point que expone el host al Core (local; la exposición vía API/web se aborda en PHASE 8) con el mismo flujo propone->decide->ejecuta->audita.
- [x] Tests de seguridad (deny por defecto, allow explícito, comandos bloqueados/inválidos, timeouts, audit de cada ejecución).
- [x] **Rutas y capacidades limitadas**: `host.roots` acota `run` (`cwd`, `working_dir`) y las nuevas tools de archivos `read_file`/`write_file`/`list_files`; con `roots` vacío no hay acceso al FS. Los escapes `../` y symlinks se resuelven antes de comprobar.
- [ ] Vínculo del Desktop Agent con sesiones/agentes de la API de forma segura. (PHASE 8)

> Decisiones clave de seguridad en `docs/security.md` y catálogo de herramientas en `docs/tools.md`.
> Las host tools se añaden a `config.yaml` -> `host` (lista de apps + allowlist de comandos + roots) y **son off por defecto**:
> además del Permission System (`permissions.allow`), `run` requiere el comando en `host.commands` y todo acceso a archivos requiere `host.roots`.

## PHASE 7 — Model Router + Resource Manager ✅

- [x] `ResourceManager` (`nova/llm/resources.py`): abstracción sencilla de recursos sin dependencias pesadas (RAM total/disponible, CPU, GPU/VRAM opcional, batería). Lecturas best-effort: cada reader es un callable inyectable y nunca lanza; si no se puede leer, degrada a valores conservadores.
- [x] Catálogo de modelos en config (`llm.models`): roles `small`/`local`/`coding`/`vision`/`embedding`, nombres nunca hardcodeados; roles vacíos caen a `default_model`.
- [x] `ModelRouter` (`nova/llm/router.py`): clasifica la tarea (`simple`, `coding`, `vision`, `heavy`, `general`) y elige modelo por complejidad + recursos + privacidad. Con RAM/batería bajos hace *downshift* de tareas pesadas/coding a `small`.
- [x] Cloud solo como perfil opcional (`model_router.cloud_enabled` = False, ADR-013); degradación elegante si no hay modelo local adecuado (todo cae a `default_model`).
- [x] Integración: CLI `/route` + `/catalog` y routing automático por turno; agents ruteados por texto; API `POST /v1/route` y routing por turno cuando no se pasa modelo; `nova-agent` usa routing.
- [x] Tests con fakes del ResourceManager + router (clasificación, downshift por RAM/batería, degradación) y smoke test real contra Ollama (se omite si no está disponible).

> La selección de modelo es una decisión del Core, nunca del LLM: el modelo **propone**, N.O.V.A. **decide** conforme a recursos y privacidad (ADR-013 y nuevo ADR-014).
> Fase completada con 172 tests (167 + 5 del router/resources; 2 skips, uno del smoke test real sin Ollama).

## PHASE 8 — Acceso remoto + auth + frontend (⏳ pasos 1-2 hechos)

- [x] **Auth en la API**: `api.token` (Bearer) exigido en cada ruta `/v1/*` cuando está configurado (401 si falta/es inválido); `healthz` y `/` quedan abiertos. **Guarda dura**: `api.host_enabled: true` sin token impide arrancar `create_app` (`ValueError`).
- [x] **CORS configurable**: `api.cors_origins` (`"*"` por defecto) para servir un frontend desde otro origen/hosting.
- [x] **Vínculo Desktop Agent -> API**: con `api.host_enabled: true` se exponen las host tools (`open_app`, `open_url`, `run`, archivos) en las sesiones de la API y en `GET /v1/tools` bajo las mismas reglas del Permission System y audit; un `ask` en la API se deniega (no hay confirmación humana), solo corre lo listado en `permissions.allow`.
- [x] **Frontend servible como estático**: `?api=<base>` / `localStorage` para apuntar a otra instalación; campo de token por navegador (`Bearer` autogenerado); el LLM nunca corre en serverless — el frontend es solo cliente.
- [x] **Mobile responsive**: `@media (max-width: 600px)` con cabecera apilable, input de token a ancho completo y cajas de mensaje al 92%.
- [x] Tests dedicados (`tests/test_api_auth.py`): auth 401/200, token protege las rutas y las sesiones, guarda `host_enabled` sin token, host tools listadas/ejecutadas solo cuando está activo.
- [ ] Modo local vs remoto bien documentado en `docs/setup.md` y guía de proxy TLS en `docs/security.md` (paso 3).

> Regla de seguridad **(ADR-015)**: nunca exponer las host tools del Desktop Agent en la API sin Bearer token,
> y nunca exponer la API a Internet sin TLS. Con `api.host_enabled` desactivado (defecto) la API no ve qué
> pasa en el host; úsalo solo con `autonomy` conservadora y comandos `host.commands` explícitos.

## PHASE 9 — Web Tools

- [x] **Herramientas controladas** (`nova/tools/web/`), **off por defecto** (`web.enabled: false` y bajo `PermissionSystem`):
  - `web_search`: búsqueda web sin API key (defecto DuckDuckGo HTML; `web.search_url` permite SearXNG/Brave). Devuelve título, URL y snippet.
  - `web_fetch`: descarga una página (solo `http(s)`, rechaza userinfo y otros esquemas) y devuelve texto legible (título + contenido) con tope de tamaño (`web.max_bytes`) y de caracteres.
  - `web_extract`: devuelve los enlaces (texto + URL) de una página para navegar un sitio.
- [x] **El LLM accede a internet SOLO vía estas tools**: no existe ninguna primitiva de red en el Core; la única salida es `WebClient` (validación de URL -> robots.txt -> rate limit -> caps). Separación LLM / Web Tools.
- [x] **Respeto a robots/ToS/rate limits**: `web.respect_robots` (parser RFC-9309 de robots.txt con `Allow`/`Disallow`, chequeo por host cacheado), `web.min_delay_s` (delay mínimo por host entre peticiones), `User-Agent` identificable (`NOVA/1.0 ...`), timeouts y límites por petición.
- [x] Integración CLI (`/run web_search {...}`), API (`GET /v1/tools` + sesiones cuando `enabled`) y `nova-agent` — siempre bajo permisos y audit.
- [x] Tests dedicados (`tests/test_web_tools.py`): parseo de resultados y páginas, max_results/max_chars, rechazo de esquemas no http(s) y userinfo, robots allow/disallow/allow-override/agent dirigido, off por defecto y denegado por permisos, extracción de enlaces relativos, audit. Total: **201** (180 previos + 21), 2 skips.
- [ ] (Nota) Compresión/resumen semántico de conversaciones largas (compaction de memoria) como tarea de agente — candidata a PHASE 12 o fase propia.

> Separación de responsabilidades: el LLM propone consultas/URLs; las Web Tools ejecutan vía `WebClient` con sus propios límites; el `ToolRunner` decide con el Permission System y audita todo. Ver ADR-016.

## PHASE 10 — Voice

- [ ] STT local (OSS) y TTS local (OSS); pipeline de voz sin APIs de pago.
- [ ] Wake word opcional.
- [ ] Integración con el chat existente (la voz origina texto y las respuestas se hablan).

## PHASE 11 — Plugins

- [ ] Cargador de plugins (`nova/plugins/`) con interfaz clara de registro de tools (ampliar `ToolRegistry`).
- [ ] Plugins de ejemplo: spotify, vscode, home-assistant, discord (opcionales, bajo permisos).
- [ ] Evitar overengineering: primero la interfaz, después los plugins concretos que se necesiten.

## PHASE 12 — Automatización avanzada

- [ ] Tareas/automatizaciones programadas (scheduler sencillo sobre el Core).
- [ ] Eventos y workflows multi-paso con el Permission System como capa de decisión.
- [ ] Prioridad: funcionalidad -> estabilidad -> tests -> docs -> escalabilidad.

---

## Notas

- Fases sujetas a revisión según neceidades reales durante el desarrollo.
- PHASE 2 se priorizó antes que Interface porque las herramientas con permisos son la base de la seguridad del sistema.
- Restricción transversal adoptada en ADR-013: local-first y coste cero — cualquier fase futura prioriza OSS/gratuito y nunca depende de APIs de pago.