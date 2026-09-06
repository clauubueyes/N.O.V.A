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

## Capas actuales (PHASE 1)

| Módulo | Responsabilidad |
|---|---|
| `nova.core.config` | Carga de configuración (YAML + env `NOVA_*`), tipada con Pydantic. |
| `nova.core.logging` | Logging estructurado: consola + archivo rotativo. |
| `nova.core.session` | Contexto de conversación: historial acotado + system prompt. |
| `nova.llm.base` | Interfaz `LLMProvider` + tipos `ChatMessage`, `ChatCompletionRequest/Response`, `NOVAProviderError`. |
| `nova.llm.ollama` | `OllamaProvider`: implementación sobre la API compatible OpenAI (`/v1/chat/completions`) y `/api/tags`. |
| `nova.llm.registry` | Registro de proveedores por nombre; `create_provider` es la única fábrica usada por todo el código. |
| `nova.cli.chat` | Shell de conversación interactiva. |

## Desacoplamiento del proveedor LLM

El resto del código **nunca** conoce a Ollama: solo depende de `nova.llm.base.LLMProvider` y del settings `llm.provider`. Añadir un proveedor = implementar la interfaz + `registry.register("nombre", Clase)`. Ver [decisions.md](decisions.md) ADR-003.

## Flujo de una conversación

1. `nova.cli.chat` construye la sesión y pide entrada al usuario.
2. `ChatSession` añade `user`, acota el historial y compone `ChatCompletionRequest`.
3. `create_provider(settings.llm)` entrega el proveedor configurado.
4. El proveedor envía la petición y devuelve `ChatCompletionResponse`.
5. La respuesta se añade a la sesión y se muestra; todo se loggea.

## Caminos futuros (incremental)

- **PHASE 2** — Tool System: `nova.tools` con schemas Pydantic, validación y `Permission System`.
- **PHASE 3** — Memory: `nova.memory` (persistencia y recuperación, embeddings locales vía `nomic-embed-text`).
- **PHASE 4** — API: `nova.api` (FastAPI, no añadido aún por principio de simplicidad).
- **PHASE 5-9** — Agents, voz, automatizaciones, plugins y autonomía avanzada.

## Restricciones de diseño

- Sin Redis/Kafka/microservicios/vector DB hasta que exista una necesidad real.
- Arquitectura sencilla que funcione, antes que enorme que no lo haga.
- La configuración de modelos vive en `config/config.yaml` (o env), nunca en código.