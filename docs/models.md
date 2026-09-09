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
| Chat general / local | `llama3.1:8b` | ~4.7 GB | Buen equilibio calidad/recursos |
| Respuestas rápidas / small | `llama3.2:1b` | ~1.3 GB | Modelo ligero para tareas simples y downshift |
| Coding (agente `coding`) | `qwen2.5-coder:7b` | ~4.7 GB | Modelo especializado en código |
| Embeddings (memoria) | `nomic-embed-text` | ~274 MB | Vector para recuperación |

```powershell
ollama pull llama3.1:8b
ollama pull llama3.2:1b
ollama pull qwen2.5-coder:7b
ollama pull nomic-embed-text
```

No están hardcodeados: si un modelo no está descargado, Ollama devuelve error y N.O.V.A. lo muestra
sin romper la sesión.

## Criterio de selección (hoy)

1. Se usa `default_model` para el chat normal y para los agentes (`Agent.model`).
2. El agente `coding` recomienda `qwen2.5-coder:7b` (consejo de config, no imposición).
3. La memoria usa `embedding_model`.

## Model Router (PHASE 7, implementado)

`ModelRouter` (`nova/llm/router.py`) elige el modelo por **tarea, recurso disponible y privacidad**;
el LLM nunca elige su propio modelo — esa es una decisión del Core.

| Factor | Consideración |
|---|---|
| Complejidad de la tarea | simple -> modelo `small`; compleja/heavy -> modelo `local` |
| Tipo de tarea | código -> `coding`; visión -> `vision` (si existe local) |
| Recursos del dispositivo | RAM/GPU/batería vía `ResourceManager`; si faltan -> *downshift* a `small` |
| Latencia deseada | tamaño del modelo y carga |
| Privacidad | por defecto local; cloud solo si `model_router.cloud_enabled: true` (ADR-013) |

Catálogo configurable en `config/config.yaml` -> `llm.models` (roles `small`, `local`, `coding`,
`vision`, `embedding`). Degradación elegante: si el rol no está configurado, se usa `default_model`.

Clasificación de tareas: `simple`, `coding`, `vision`, `heavy` y `general` (regex de pistas en
`router.py`). Con RAM disponible < `model_router.min_ram_gb` (o batería < 20 % sin AC, si
`model_router.battery: true`), las tareas `heavy`/`coding` caen a `small` si existe.

### Uso

- CLI: `/route <texto>` muestra la decisión y `/catalog` lista el catálogo; el chat y los agentes
  se rutean automáticamente por turno.
- API: `POST /v1/route` devuelve `{task_kind, provider, role, model, reason}`;
  `POST /v1/sessions/{id}/chat` rutea el modelo por turno salvo que se pase `model` explícito.
- `nova-agent` rutea cada mensaje igual que el chat.

### Escenarios

| Texto (pista) | task_kind | role | provider | modelo (ejemplo) |
|---|---|---|---|---|
| "hola!" | simple | small | ollama | `llama3.2:1b` |
| "escribe una funcion en python" | coding | coding | ollama | `qwen2.5-coder:7b` (downshift a small si RAM baja) |
| "mira esta imagen" | vision | vision | ollama | `llama3.2-vision` (si está configurado) |
| "haz un analisis complejo" | heavy | local | ollama | `llama3.1:8b` |
| "haz un analisis complejo" (RAM baja, HIBRID) | heavy | local | opencode | `openai/gpt-4o` (fallback cloud) |
| "cuéntame una historia" | general | local | ollama | `llama3.1:8b` |

## Cloud fallback (MODO HYBRID, PHASE 14)

Cuando `ai.mode: hybrid` y `ai.privacy: cloud_allowed`, las tareas **`heavy`** con recursos locales
insuficientes pueden ruteo a **OpenCode** (`providers.opencode`). Reglas:

- **Local-first**: nunca se elige cloud si el modelo local basta (ADR-013/ADR-020).
- **Privacy**: con `local_only` (defecto) NO existe fallback cloud, aunque haya `open_code.models`.
- **Solo tareas pesadas**: `simple`/`general` jamás salen del dispositivo.
- **Catálogo cloud en config** (`open_code.models` por rol `local`/`reasoning`/`coding`/`vision`) y
  `open_code.default_model` como modelo genérico. Se prefiere el rol `local`/`reasoning` para heavy.
- **ModelInfo con coste desconocido**: no se hardcodean listas de modelos "gratuitos"; si no hay
  forma fiable de saber el coste, se reporta `unknown`.
- **Fallback bidireccional**: si el proveedor cloud falla (auth, rate limit, timeout, red, modelo no
  disponible), el CLI/API reintentan con el modelo local. Todo se registra en el log.

La memoria sigue siempre local (SQLite + embeddings Ollama); N.O.V.A. nunca sube sus base de datos
de memoria a un proveedor cloud.

## Reglas transversales

- Los modelos y URLs viven en config/env (`NOVA_LLM_*`, `NOVA_OPEN_CODE_*`), nunca en código.
- No se asumen APIs de pago. OpenCode es una integración opcional más en el
  registry (`opencode`), que requiere configuración explícita del usuario y queda fuera del camino
  por defecto. N.O.V.A. lo detecta (`nova-setup doctor`) pero jamás lo instala.
- Si una tarea exige un modelo que no existe localmente, N.O.V.A. responde según sus capacidades
  reales y sugiere descargarlo (`ollama pull <modelo>`), sin inventar resultados.