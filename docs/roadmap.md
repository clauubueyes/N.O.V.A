# Roadmap

Fases progresivas. Cada fase pasa por: ANALIZAR -> IMPLEMENTAR -> TESTEAR -> REVISAR -> DOCUMENTAR.

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

> Nota: la suma/resumen semántico de conversaciones largas (compresión de memoria) se puede abordar en PHASE 5 (Agents) con una etapa de "compaction".

## PHASE 4 — Interface ✅

- [x] API REST (FastAPI) en `nova/api`: chat stateless y por sesión, modelos, herramientas, memoria, salud.
- [x] Interfaz web (HTML/JS vanilla servida por la API en `/`).
- [x] Comunicación con el Core: la API reutiliza `ChatSession`, `MemoryService` y `ToolRunner` con el mismo Permission System y audit.
- [x] Documentación OpenAPI automática en `/docs`.

> Nota: interfaz de escritorio (sistemas) se puede abordar más adelante como cliente de esta API; la web cubre el caso funcionante.

## PHASE 5 — Agents

- [ ] General Agent.
- [ ] Coding Agent.
- [ ] Research Agent.
- [ ] System Agent.
- [ ] Automation Agent.

## PHASE 6 — Voice

- [ ] Speech-to-Text.
- [ ] Text-to-Speech.
- [ ] Wake word.
- [ ] Pipeline de voz.

## PHASE 7 — Automation

- [ ] Automatizaciones.
- [ ] Tasks.
- [ ] Eventos.
- [ ] Schedulers.

## PHASE 8 — Plugins

- [ ] Sistema de plugins.
- [ ] Integraciones externas.
- [ ] Nuevas herramientas.

## PHASE 9 — Advanced Autonomy

- [ ] Planificación.
- [ ] Ejecución de tareas complejas.
- [ ] Multi-step workflows.
- [ ] Autonomía configurable.

---

## Notas

- Fases sujetas a revisión según neceidades reales durante el desarrollo.
- PHASE 2 se priorizó antes que Interface porque las herramientas con permisos son la base de la seguridad del sistema.