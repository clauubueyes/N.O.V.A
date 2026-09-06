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