# Decisiones / ADR

Formato ligero de "Architecture Decision Records". Cada decisión importante se documenta aquí.

## ADR-001 — Stack: Python 3.11 + FastAPI

- **Fecha:** 2026-09-06
- **Contexto:** el entorno ya tiene Python 3.11.9 con FastAPI, Pydantic 2, httpx y pytest instalados; además del ecosistema natural para LLMs (Ollama, embeddings).
- **Decisión:** backend en Python 3.11. FastAPI se añadirá en PHASE 4 (API), no antes, por principio de simplicidad.
- **Consecuencias:** Node 24 queda descartado como base del proyecto.
- **Estado:** aceptada (aprobada por el dueño del proyecto).

## ADR-002 — Arquitectura en capas: Inteligencia / Orquestación / Herramientas / Sistema

- **Fecha:** 2026-09-06
- **Contexto:** el LLM no debe tener control directo e ilimitado sobre el ordenador.
- **Decisión:** el LLM solo produce intenciones. Un `Orchestrator` las traduce en acciones propuestas, el `Permission System` decide, y las `Tools` ejecutan. Todo ejecución se registra.
- **Consecuencias:** las herramientas solo se escriben en PHASE 2; en PHASE 1 el Core no ejecuta nada sobre el sistema.
- **Estado:** aceptada. Es el principio fundamental del proyecto.

## ADR-003 — Desacoplamiento del proveedor LLM

- **Fecha:** 2026-09-06
- **Contexto:** Ollama es el proveedor inicial, pero N.O.V.A. no debe acoplarse a él.
- **Decisión:** interfaz `LLMProvider` (`chat`, `list_models`, `health`) + registro por nombre vía `nova.llm.registry`. Todo el código depende solo de la abstracción.
- **Consecuencias:** añadir un proveedor = implementar la interfaz + `registry.register(...)`. Nada en `core`/`cli` conoce a Ollama.
- **Estado:** aceptada.

## ADR-004 — Configuración externa, modelos nunca hardcodeados

- **Fecha:** 2026-09-06
- **Contexto:** la configuración del modelo debe estar separada del código.
- **Decisión:** `config/config.yaml` como fuente principal, overrides vía env `NOVA_*` (`NOVA_LLM_DEFAULT_MODEL`, `NOVA_LOGGING_LEVEL`, …). Tipado con Pydantic.
- **Consecuencias:** un nuevo despliegue solo cambia config/env, nunca el código.
- **Estado:** aceptada.

## ADR-005 — Simplicidad antes que herramientas distribuidas

- **Fecha:** 2026-09-06
- **Contexto:** tentación de adoptar Redis/Kafka/microservicios/vector DB/kubernetes desde el día 1.
- **Decisión:** prohibido introducirlos sin una necesidad real. Preferir SQLite sobre bases vectoriales externas en PHASE 3 (revisable cuando el tamaño lo exija).
- **Consecuencias:** menos infra, menos latencia, menos caos operativo.
- **Estado:** aceptada.

## ADR-006 — Proveedor que habla OpenAI-compatible con httpx (sin SDK "openai")

- **Fecha:** 2026-09-06
- **Contexto:** Ollama expone `/v1/chat/completions` (compatibilidad OpenAI). Añadir el SDK `openai` sería una dependencia más sin valor inmediato.
- **Decisión:** `OllamaProvider` usa `httpx` directamente contra esa API. El modelo de datos (roles/content) es el que todos los providers comparten.
- **Consecuencias:** sin dependencia externa innecesaria; si mañana se quiere el SDK oficial de algún proveedor, es un detalle interno del provider.
- **Estado:** aceptada.

## ADR-007 — Permission System: autonomía + reglas allow/deny, ejecución siempre auditada

- **Fecha:** 2026-09-06
- **Contexto:** PHASE 2 introduce la ejecución de herramientas; sin control, el LLM/el usuario podrían ejecutar cualquier acción sobre el sistema.
- **Decisión:** toda ejecución pasa por `PermissionSystem.authorize(tool)`. Nivel de autonomía por config: `off` (solo `allow`), `ask` (lo no regulado pregunta), `full` (solo `deny` bloquea). Precedencia: `deny` > `allow` > autonomía. Nada se ejecuta fuera de `ToolRunner`.
- **Consecuencias:** añadir una herramienta no la habilita sola; hay que configurar su regla en `config/config.yaml` -> `permissions`. El runner es el único punto que consulta permisos + audita.
- **Estado:** aceptada.

## ADR-008 — Audit log en JSONL local (rotativo), no una base de datos

- **Fecha:** 2026-09-06
- **Contexto:** se necesita un registro fiable de cada ejecución/decisión (quién, qué, argumentos, resultado, duración) para revisión y trazabilidad.
- **Decisión:** audit como archivo append-only de líneas JSON (`logs/audit.nova.jsonl`), rotación tipo RotatingFileHandler (2 MB x 3). `AuditLog.record` nunca lanza: un fallo de escritura se loggea y no interrumpe la ejecución.
- **Consecuencias:** sin dependencias nuevas. Si en PHASE 3 o PHASE 4 se necesita consulta/analítica pesada, se puede migrar a SQLite sin cambiar el runner.
- **Estado:** aceptada.

## ADR-009 — La selección de herramientas por el LLM se deja para Agents (PHASE 5)

- **Fecha:** 2026-09-06
- **Contexto:** PHASE 2 construye el sistema de herramientas y permisos. Hacer que el LLM decida qué herramienta llamar (function calling) añade complejidad de parsing/prompting y no es requisito del roadmap del Tool System.
- **Decisión:** en PHASE 2 el usuario invoca herramientas explícitamente (CLI `/run`) o vía API Python (`ToolRunner.run`). La orquestación automática intención->herramienta llega con los Agents (PHASE 5).
- **Consecuencias:** el Core de herramientas queda listo y testeable sin depender de un LLM; cuando lleguen los Agents solo habrá que conectar su salida a `ToolRunner.run`.
- **Estado:** aceptada.

## ADR-010 — Memoria en SQLite local + embeddings del proveedor con fallback a keywords

- **Fecha:** 2026-09-07
- **Contexto:** PHASE 3 necesita persistir conversación y hechos y recuperar contexto relevante. ADR-005 prohibe vector DB externas sin necesidad real.
- **Decisión:** `MemoryStore` sobre **SQLite** (`memory/nova.db`, stdlib `sqlite3`) con dos tablas (`memories`, `transcripts`). Los embeddings (`LLMProvider.embed_text` vía `/api/embed`, `nomic-embed-text`) se guardan como JSON por fila y la recuperación rankea por **similitud coseno** en Python (O(n) por consulta, suficiente a esta escala). Si el proveedor no soporta embeddings o fallan, se cae a **keywords**; la memoria nunca interrumpe el diálogo.
- **Consecuencias:** cero dependencias nuevas; migrar a una vector DB real en el futuro solo toca `MemoryRetriever`. El contexto recuperado se inyecta como mensaje `system` antes del turno del usuario.
- **Estado:** aceptada.

## ADR-011 — API REST stateless + sesiones con estado del Core por petición

- **Fecha:** 2026-09-07
- **Contexto:** PHASE 4 necesita exponer N.O.V.A. como servicio (API + web) sin duplicar la lógica del Core.
- **Decisión:** FastAPI en `nova/api` (ADR-001 lo fijaba para esta fase). Dos modos: `POST /v1/chat` stateless (mensajes completos, passthrough al provider) y `POST /v1/sessions/{id}/chat` que reutiliza `ChatSession` + `MemoryService` + `ToolRunner` (una sesión de API = un `ChatSession` + una memoria con `session_id` propio). El contexto se inyecta igual que en el CLI. La API nunca interactúa en vivo: un `ASK` se deniega (confirm = False).
- **Consecuencias:** añadir un cliente (web, escritorio, script) = llamar a la API; la lógica (permisos, audit, memoria) sigue viviendo en el Core y se testea con `fastapi.testclient` inyectando un provider falso.
- **Estado:** aceptada.