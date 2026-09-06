# Modelos

## Política

**Local-first, gratuito y privado (ADR-013).** La inferencia corre en el dispositivo del usuario
(Ollama). Las APIs cloud son opcionales y solo si el usuario las configura; el Core nunca depende de ellas.

## Cómo funciona hoy (PHASE 5)

- `nova.llm.base.LLMProvider`: interfaz única (chat, listado, health, embeddings). El resto del
  código nunca conoce el proveedor concreto.
- Proveedor registrado: `ollama` (`OllamaProvider`), vía `create_provider(settings.llm)`.
- El modelo por defecto se configura en `config/config.yaml` -> `llm.default_model` (nunca en código).
- En sesión, `/model <nombre>` cambia el modelo del chat; `/models` lista los disponibles.
- `llm.embedding_model` se usa para **memoria** (PHASE 3): `nomic-embed-text` vía `/api/embed`.
  Si el provider no soporta embeddings o fallan, la memoria degrada a búsqueda por keywords.

### Modelos de referencia (probados en el entorno)

| Uso | Modelo | Tamaño aprox. | Notas |
|---|---|---|---|
| Chat general | `llama3.1:8b` | ~4.7 GB | Buen equilibio calidad/recursos |
| Coding (agente `coding`) | `qwen2.5-coder:7b` | ~4.7 GB | Modelo especializado en código |
| Embeddings (memoria) | `nomic-embed-text` | ~274 MB | Vector para recuperación |

```powershell
ollama pull llama3.1:8b
ollama pull qwen2.5-coder:7b
ollama pull nomic-embed-text
```

No están hardcodeados: si un modelo no está descargado, Ollama devuelve error y N.O.V.A. lo muestra
sin romper la sesión.

## Criterio de selección (hoy)

1. Se usa `default_model` para el chat normal y para los agentes (`Agent.model`).
2. El agente `coding` recomienda `qwen2.5-coder:7b` (consejo de config, no imposición).
3. La memoria usa `embedding_model`.

## Model Router (PHASE 7, objetivo)

`ModelRouter` elegirá el modelo por **tarea, recurso disponible y privacidad**:

| Factor | Consideración |
|---|---|
| Complejidad de la tarea | simple -> modelo pequeño; compleja -> modelo mayor |
| Tipo de tarea | código -> modelo de código; visión -> multimodal local si existe |
| Recursos del dispositivo | RAM/VRAM/CPU/GPU disponibles (`ResourceManager`) |
| Latencia deseada | tamaño del modelo y carga |
| Privacidad | por defecto local; cloud solo si el usuario lo perfila explícitamente |

Catálogo configurable en `config/config.yaml` (p. ej. `llm.catalog: { small, default, coding,
vision, embedding }`), con degradación elegante: si no hay un modelo adecuado, se usa `default_model`.
Diseño detallado se decidirá en PHASE 7 (nuevo ADR, sin romper la interfaz `LLMProvider`).

## Reglas transversales

- Los modelos y URLs viven en config/env (`NOVA_LLM_*`), nunca en código.
- No se asumen APIs de pago. Una integración cloud futura será un proveedor opcional más en el
  registry, que requiere configuración explícita del usuario y queda fuera del camino por defecto.
- Si una tarea exige un modelo que no existe localmente, N.O.V.A. responde según sus capacidades
  reales y sugiere descargarlo (`ollama pull <modelo>`), sin inventar resultados.