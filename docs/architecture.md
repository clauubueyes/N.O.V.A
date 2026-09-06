# Arquitectura

## Principio

N.O.V.A. separa estrictamente:

- **INTELIGENCIA** — el LLM (propuesta de intención).
- **ORQUESTACIÓN** — el `Orchestrator` (traduce intención en acciones).
- **HERRAMIENTAS** — las `Tools` (capacidades: archivos, comandos, web…).
- **SISTEMA** — el ordenador real.

El LLM **propone**; el sistema **decide**. El `Permission System` media entre ambos y registra todo.

```
Usuario
  |
  v
N.O.V.A. (Core: contexto, sesión, telemetría)
  |
  v
LLM Provider (Ollama hoy; otros proveedores mañana)
  |
  v
Orchestrator (traduce intención -> acción propuesta)
  |
  v
Permission System (¿permitido? según nivel de autonomía)
  |
  v
Tool (schema + validación + ejecución + resultado)
  |
  v
Sistema      -> Resultado -> N.O.V.A. -> Usuario
```

## Capas actuales (PHASE 1 + 2 + 3 + 4 + 5)

| Módulo | Responsabilidad |
|---|---|
| `nova.core.config` | Carga de configuración (YAML + env `NOVA_*`), tipada con Pydantic. |
| `nova.core.logging` | Logging estructurado: consola + archivo rotativo. |
| `nova.core.session` | Contexto de conversación: historial acotado + system prompt (+ `set_system_prompt`/`add_tool` para agents). |
| `nova.core.audit` | Audit log de ejecuciones de herramientas (`logs/audit.nova.jsonl`, JSON lines rotativo). |
| `nova.llm.base` | Interfaz `LLMProvider` (chat, listado, health, embeddings) + tipos `ChatMessage`, `ChatCompletionRequest/Response`, `NOVAProviderError`. |
| `nova.llm.ollama` | `OllamaProvider`: API compatible OpenAI (`/v1/chat/completions`), `/api/tags` y embeddings (`/api/embed`). |
| `nova.llm.registry` | Registro de proveedores por nombre; `create_provider` es la única fábrica usada por todo el código. |
| `nova.memory.store` | `MemoryStore`: SQLite local (hechos + trascripción de conversación, embeddings opcionales). |
| `nova.memory.retriever` | Recuperación por similitud coseno sobre embeddings, con fallback a keywords. |
| `nova.memory.service` | `MemoryService`: fachada `remember`/`record`/`search`/`context` para el CLI, la API y los Agents. |
| `nova.memory.tools` | Herramientas `remember` y `memory_search` (bajo el Permission System). |
| `nova.tools.base` | `BaseTool` (schema Pydantic + `execute` + `json_schema()`), `ToolResult`, `ToolError`. |
| `nova.tools.standard` | Herramientas de ejemplo: `calculate`, `date_time`, `list_dir`. |
| `nova.tools.registry` | Registro de herramientas por nombre. |
| `nova.tools.permissions` | `PermissionSystem`: autonomía (`off`/`ask`/`full`) + reglas allow/deny. |
| `nova.tools.runner` | `ToolRunner`: permiso -> validación -> ejecución, auditando cada paso. |
| `nova.agents.core` | `Agent`: loop acotado (`max_steps`) que propone tools por JSON estructurado, delega en `ToolRunner` y devuelve `AgentResult` con steps. |
| `nova.agents.presets` | `AgentPreset` + 5 presets (`general`, `coding`, `research`, `system`, `automation`) + `create_agent`. |
| `nova.api.app` | API REST (FastAPI): `create_app` reutiliza el Core; sesiones con memoria y tools por petición, agentes persistentes por nombre. |
| `nova.api.server` | Entry point `nova-api`: levanta `uvicorn` con `APISettings`. |
| `nova.cli.chat` | Shell de conversación interactiva (chat + memoria + `/tools` + `/run` + `/agents` + `/agent`). |

## Desacoplamiento del proveedor LLM

El resto del código **nunca** conoce a Ollama: solo depende de `nova.llm.base.LLMProvider` y del settings `llm.provider`. Añadir un proveedor = implementar la interfaz + `registry.register("nombre", Clase)`. Ver [decisions.md](decisions.md) ADR-003.

## Flujo de una conversación

1. `nova.cli.chat` construye la sesión y pide entrada al usuario.
2. `ChatSession` añade `user`, acota el historial y compone `ChatCompletionRequest`.
3. `nova.memory` persiste el turno y recupera la memoria relevante (`MemoryService.context`), inyectada como mensaje `system` antes del turno del usuario (si la hay).
4. `create_provider(settings.llm)` entrega el proveedor configurado.
5. El proveedor envía la petición y devuelve `ChatCompletionResponse`.
6. La respuesta se añade a la sesión, se persiste en la memoria y se muestra; todo se loggea.

> Si el proveedor soporta embeddings (`supports_embedding`), la recuperación usa similitud coseno sobre `nomic-embed-text`; si no, cae a keywords. La memoria nunca bloquea el chat.

## Ejecución de una herramienta (`ToolRunner.run`)

1. `nova.tools.registry` localiza la herramienta por nombre.
2. `PermissionSystem.authorize(name)` decide: `ALLOW`, `DENY` o `ASK` (deny > allow > autonomía).
3. Si `ASK`, el `confirm` callback pregunta al usuario (p. ej. en el CLI: `[y/N]`).
4. `BaseTool.validate` valida los argumentos contra el schema Pydantic.
5. `BaseTool.execute` ejecuta y devuelve un `ToolResult` tipado; los errores se envuelven como `failure`.
6. `nova.core.audit` registra cada ejecución y decisión en `logs/audit.nova.jsonl`.

```
Usuario -> (/run manual o /agente vía Agent)
  v
ToolRunner
  v
PermissionSystem (allow / deny / ask)
  v
BaseTool.validate (schema Pydantic)
  v
BaseTool.execute -> ToolResult
  v
AuditLog (JSON lines) + Resultado -> Usuario
```

## Agentes (PHASE 5)

Los agentes son una única clase `Agent` (loop acotado por `max_steps`) instanciada con un
`AgentPreset` (perfil + system prompt + conjunto de tools). El LLM **propone** tool-calls con
**JSON estructurado** (`{"tool": "<name>", "args": {...}}`, tolera code fences); N.O.V.A.
**decide** delegando en el `ToolRunner` (Permission System + audit) y alimenta los resultados
como mensajes `tool` hasta que el modelo responde en texto plano o se agota el presupuesto.
Cada paso queda registrado en `AgentResult.steps`.

```
Usuario -> Agent.act(texto)
  v
Agent(session + memoria + tools prompt en system)
  v
LLMProvider (responde texto plano O {"tool":..., "args":...})
  v
parse_tool_call -> ToolRunner.run (permisos + audit)
  v
resultado -> mensaje "tool" -> vuelve al LLM (máx. max_steps)
  v
respuesta final -> se persiste en memoria -> AgentResult
```

Los 5 presets (`general`, `coding`, `research`, `system`, `automation`) en `nova.agents.presets`
comparten el `MemoryService` y el `ToolRunner`: inyección de contexto y registro de conversación
igual que el chat normal. Añadir un agente = añadir un `AgentPreset` (ver ADR-012).

## API REST y web (PHASE 4)

`nova-api` sirve la web y la API. Cada sesión de API lleva un `ChatSession` + un `MemoryService` (mismo `MemoryStore`, `session_id` propio) + su `ToolRunner`:

```
Web / cliente -> FastAPI (nova.api.app)
  |-> POST /v1/sessions/{id}/chat -> ChatSession + MemoryService + Provider (misma lógica que el CLI)
  |-> POST /v1/sessions/{id}/run   -> ToolRunner (Permission System + audit)
  |-> POST /v1/chat                -> Provider directo (stateless)
```

El contexto recuperado se inyecta igual que en el CLI. En la API un `ASK` se deniega (no hay confirmación interactiva); lo que esté en `permissions.allow` corre directo.

## API REST, web y agents (PHASE 4 + 5)

`nova-api` sirve la web y la API. Cada sesión de API lleva un `ChatSession` + un `MemoryService` (mismo `MemoryStore`, `session_id` propio) + su `ToolRunner`, opcionalmente vinculada a un agente:

```
Web / cliente -> FastAPI (nova.api.app)
  |-> POST /v1/sessions/{id}/chat -> ChatSession + MemoryService + Provider (misma lógica que el CLI)
  |-> POST /v1/sessions/{id}/run   -> ToolRunner (Permission System + audit)
  |-> POST /v1/chat                -> Provider directo (stateless)
  |-> GET /v1/agents               -> presets disponibles
  |-> POST /v1/agents/{name}/chat  -> Agent persistente por nombre (sesión + memoria + tools)
  |-> POST /v1/sessions {agent}    -> sesión ligada a un agente (responde con `steps`)
```

Crear una sesión con `{"agent": "research"}` hace que `/chat` use `Agent.act` y devuelva los `steps`
de cada tool además de la respuesta. En la web, el selector de agente decide con qué presets habla la sesión.

## Arquitectura objetivo (evolución incremental)

La visión de producto mapea sobre esta estructura sin saltos de arquitectura:

```
                     N.O.V.A.
                        │
                 N.O.V.A. Core  (orquestación, contexto, sesión, audit)
                        │
         ┌──────────────┼──────────────┐
         │              │              │
      Memory          Agents         Tools
         │              │              │
         └──────────────┼──────────────┘
                        │
                  Model Router  (PHASE 7)
                        │
          ┌─────────────┼─────────────┐
          │             │             │
       Ollama       Other Local    Optional
       Models        Providers      Cloud APIs
                                      │
                             SOLO si el usuario lo configura (ADR-013)
```

Estado de cada pieza hoy: **Memory** ✅ (PHASE 3, SQLite + embeddings), **Agents** ✅ (PHASE 5),
**Tools + Permission System + audit** ✅ (PHASE 2), **Model Router** ⏳ (PHASE 7), **Cloud** ❌ no está
presente ni se asume — el Core funciona 100% local y gratuito.

El **Desktop Agent** (PHASE 6) conecta la misma ruta `Usuario -> Core -> Permission System -> Tool`
desde el ordenador del usuario (host), reutilizando el `ToolRunner` existente; en PHASE 8 el móvil
accederá a través de la API con el Desktop Agent como brazo de ejecución del host.

## Caminos futuros (incremental)

- **PHASE 6-12** — Desktop Agent (control del host seguro), Model Router + Resource Manager, acceso remoto con auth, web tools, voz, plugins y automatización avanzada. Detalle en [roadmap.md](roadmap.md).

## Restricciones de diseño

- Sin Redis/Kafka/microservicios/vector DB hasta que exista una necesidad real (SQLite + embeddings locales cubren PHASE 3 — ADR-005/ADR-010; FastAPI se añadió en PHASE 4 — ADR-011).
- Arquitectura sencilla que funcione, antes que enorme que no lo haga.
- La configuración de modelos vive en `config/config.yaml` (o env), nunca en código.
- Las herramientas solo se ejecutan tras la decisión explícita o permitida del `PermissionSystem`; toda ejecución queda auditada.
- La memoria nunca interrumpe el diálogo: si el embedding falla o no existe, se degrada a keywords.
- La API (y la web) solo hablan con el Core: nunca conocen los detalles de Ollama ni de las herramientas.
- Los Agents son config, no herencia: una clase `Agent` + presets paramétricos (ADR-012). El LLM propone, el `ToolRunner` decide y audita.