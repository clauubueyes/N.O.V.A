# API

Estado: **PHASE 5**. N.O.V.A. expone API **interna** de Python, un CLI, una **API REST** (FastAPI) con interfaz web y **Agents**.

## Proveedor LLM — `nova.llm.base`

```python
class LLMProvider(ABC):
    supports_embedding: bool = False   # True si implementa embed_text
    def chat(self, request: ChatCompletionRequest) -> ChatCompletionResponse: ...
    def list_models(self) -> list[ModelInfo]: ...
    def health(self) -> bool: ...
    def embed_text(self, texts: str | Sequence[str]) -> list[list[float]]: ...
    def close(self) -> None: ...
```

`embed_text` devuelve un vector por entrada (p. ej. `nomic-embed-text` vía `/api/embed`); en proveedores que no la soporten lanza `NOVAProviderError`.

Tipos:

```python
@dataclass
class ChatMessage:
    role: str          # "system" | "user" | "assistant"
    content: str

@dataclass
class ChatCompletionRequest:
    messages: list[ChatMessage]
    model: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None

@dataclass
class ChatCompletionResponse:
    message: ChatMessage
    model: str
    usage: dict | None
```

- `model` y `temperature` nulos caen a la config del settings del provider.
- Los errores se elevan como `NOVAProviderError` con mensaje legible.

## Fábrica — `nova.llm.registry`

```python
from nova.llm import create_provider
provider = create_provider(settings.llm)   # "ollama" registrado en __init__
```

## Configuración — `nova.core.config`

```python
from nova.core.config import load_settings
s = load_settings()          # config/config.yaml + env NOVA_*
s.llm.default_model          # str
s.llm.embedding_model        # str (nomic-embed-text)
s.logging.level              # str
s.session.max_history_messages
s.session.system_prompt
s.memory.db_file             # str (memory/nova.db)
s.memory.session_id          # str
s.memory.max_context         # int
s.memory.similarity_threshold # float
s.api.host                   # str (127.0.0.1)
s.api.port                   # int (8000)
```

Variables de entorno soportadas: `NOVA_LLM_PROVIDER`, `NOVA_LLM_BASE_URL`, `NOVA_LLM_DEFAULT_MODEL`, `NOVA_LLM_EMBEDDING_MODEL`, `NOVA_LLM_TEMPERATURE`, `NOVA_LLM_TIMEOUT_S`, `NOVA_LOGGING_LEVEL`, `NOVA_LOGGING_FILE`, `NOVA_SESSION_MAX_HISTORY_MESSAGES`, `NOVA_SESSION_SYSTEM_PROMPT`, `NOVA_PERMISSIONS_AUTONOMY`, `NOVA_AUDIT_FILE`, `NOVA_MEMORY_DB_FILE`, `NOVA_MEMORY_SESSION_ID`, `NOVA_MEMORY_MAX_CONTEXT`, `NOVA_MEMORY_SIMILARITY_THRESHOLD`, `NOVA_API_HOST`, `NOVA_API_PORT`.

## Contexto — `nova.core.session`

```python
session = ChatSession(max_history_messages=20, system_prompt="...")
session.add_user("hola")
session.add_assistant("hola de vuelta")
session.add_tool("resultado de una herramienta")  # mensaje rol "tool" (Agents)
session.set_system_prompt("nuevo prompt")         # reemplaza el system prompt
session.messages()                    # list[ChatMessage]
session.build_request(model="...")    # ChatCompletionRequest
session.clear()                       # conserva el system prompt
```

## Memoria — `nova.memory`

```python
from nova.memory import MemoryService, MemoryStore

store = MemoryStore("memory/nova.db")                 # SQLite: memories + transcripts
memory = MemoryService(
    store,
    embed=provider.embed_text if provider.supports_embedding else None,
    session_id="default",
    max_context=3,
    similarity_threshold=0.3,
)
```

```python
memory.remember("me llamo Gabriel", kind="fact", source="user")   # persiste un hecho
memory.record("user", "hola")                                     # persiste un turno
memory.record("assistant", "hola de vuelta")
memory.search("Gabriel")            # list[MemoryHit] (memorias + trascripción)
memory.context("Gabriel")           # str lista formateada para inyectar al prompt
memory.recent_memories(limit=10)    # list[MemoryRecord]
memory.close()
```

- `MemoryHit`: `content`, `source` (`memory`/`transcript`), `kind`, `created_at`, `similarity`; `.to_dict()`.
- Ranking por similitud coseno sobre embeddings si hay provider; si no hay embeddings o fallan, búsqueda por keywords.
- La recuperación nunca lanza: un fallo de embedding degrada a keywords.
- Herramientas bajo el Permission System: `remember` y `memory_search` (`nova.memory.tools`), registrables igual que las estándar.

## Herramientas — `nova.tools`

```python
from nova.tools import ToolRunner, registry
from nova.core.config import PermissionSettings, AutonomyLevel
```

### `.tools.base`

```python
class BaseTool(ABC):
    name: ClassVar[str]                 # identificador único
    description: ClassVar[str]
    input_schema: ClassVar[type[BaseModel]]  # schema Pydantic de argumentos

    def validate(self, args: dict | None) -> BaseModel:  # lanza ToolArgumentError
    def execute(self, params: BaseModel) -> ToolResult:  # abstracto

@dataclass
class ToolResult:
    tool: str
    ok: bool
    message: str = ""
    data: dict | None = None
```

Errores: `ToolError` (base), `ToolArgumentError` (validación de argumentos).

### `.tools.permissions`

```python
class PermissionSystem:
    def __init__(self, settings: PermissionSettings) -> None: ...
    def authorize(self, tool_name: str) -> Authorization: ...
```

`AutonomyLevel`: `off` (solo `allow`), `ask` (lo no regulado pregunta), `full` (solo `deny` bloquea). La precedencia es `deny` > `allow` > autonomía. `Authorization` trae `decision` (`ALLOW`/`DENY`/`ASK`) y `reason`.

### `.tools.runner`

```python
runner = ToolRunner(
    registry=registry,
    permissions=PermissionSystem(settings.permissions),
    audit=AuditLog(settings.audit.file),         # opcional
    confirm=lambda q: input(q).strip() == "y",   # opcional (resuelve ASK)
)
result = runner.run("calculate", {"expression": "2+2"})
# result.ok, result.message, result.data
```

`ToolRunner.run` audita cada paso (decisión, validación, ejecución, duración) en `logs/audit.nova.jsonl`.

### Herramientas estándar

| Nombre | Argumentos | Descripción |
|---|---|---|
| `calculate` | `expression: str` | Aritmética segura (AST whitelist: `+ - * / % **`, `pi/e`, `abs/round/min/max/int/float/sqrt`). |
| `date_time` | `format: str = "%Y-%m-%d %H:%M:%S %z"` | Fecha/hora local formateada. |
| `list_dir` | `path: str = "."`, `show_hidden: bool = False` | Lista de entradas de un directorio (solo lectura). |

Registro de plugins: `from nova.tools.registry import registry; registry.register(MiTool())`.

### `nova.core.audit`

```python
audit = AuditLog("logs/audit.nova.jsonl")
audit.record(tool="calculate", decision="allow", args={...}, ok=True, duration_ms=1.2)
```

JSONL rotativo (2 MB x 3 backups). `record` nunca lanza: fallos de escritura se loggean y no rompen la ejecución.

## Agents — `nova.agents` (PHASE 5)

Presets paramétricos sobre una única clase `Agent`; el LLM **propone** tool-calls y N.O.V.A. las **ejecuta** vía `ToolRunner`.

```python
from nova.agents import agent_presets
from nova.llm import create_provider
from nova.memory import MemoryService, MemoryStore
from nova.tools import ToolRunner
from nova.agents import create_agent

agent = create_agent(
    "coding",                       # general | coding | research | system | automation
    provider=provider,
    runner=tools_runner,            # ToolRunner (Permission System + audit)
    memory=memory,                  # opcional: inyección de contexto + registro
    model="llama3.1:8b",            # opcional
    max_steps=4,                    # tope de tool-calls por turno
)
result = agent.act("refactoriza esto")
# AgentResult: .answer (str), .model (str), .steps (list[AgentStep])
for step in result.steps:           # cada tool-call propuesta por el LLM
    print(step.tool, step.args, step.ok, step.message, step.data)
```

- **Protocolo de tool-call**: el modelo responde SOLO con `{"tool": "<nombre>", "args": {...}}` (JSON, tolera code fences) o con texto plano como respuesta final. `nova.agents.parse_tool_call` extrae la propuesta.
- El sistema inyecta en el prompt el perfil del agente + los schemas JSON de sus tools (`BaseTool.json_schema()`); los resultados vuelven como mensajes `tool`.
- `create_agent` comparte el `MemoryService` y el `ToolRunner` ya construidos (misma memoria y permisos que el chat normal).
- Presets en `nova.agents.presets`:

| Preset | Tools |
|---|---|
| `general` | todas |
| `coding` | `calculate`, `list_dir`, `date_time`, `remember`, `memory_search` |
| `research` | `list_dir`, `date_time`, `memory_search`, `remember` |
| `system` | `date_time`, `list_dir` |
| `automation` | `calculate`, `date_time`, `list_dir`, `remember`, `memory_search` |

## Uso como librería (ejemplo)

```python
from nova.core.audit import AuditLog
from nova.core.config import load_settings
from nova.core.session import ChatSession
from nova.llm import create_provider
from nova.tools import ToolRunner, registry
from nova.tools.permissions import PermissionSystem

settings = load_settings()
session = ChatSession(
    max_history_messages=settings.session.max_history_messages,
    system_prompt=settings.session.system_prompt,
)

provider = create_provider(settings.llm)
session.add_user("Calcula 2+2")
response = provider.chat(session.build_request())
print(response.message.content)
session.add_assistant(response.message.content)
provider.close()

tools = ToolRunner(
    registry=registry,
    permissions=PermissionSystem(settings.permissions),
    audit=AuditLog(settings.audit.file),
)
result = tools.run("calculate", {"expression": "2+2"})
print(result.data)   # {"expression": "2+2", "result": 4}
```

## Endpoint HTTP — `nova.api` (PHASE 4 + 5)

Arranque:

```powershell
.\.venv\Scripts\nova-api        # usa config/config.yaml -> api.host/api.port
# o
python -m nova.api.server
```

Interfaz web en `/` y OpenAPI en `/docs`. `create_app(settings, provider=...)` permite inyectar dependencias en tests.

| Método y ruta | Descripción |
|---|---|
| `GET /healthz` | Salud: `provider`, `status`, `version`. |
| `GET /v1/models` | Modelos del proveedor (`name`, `size`, `modified_at`). |
| `GET /v1/tools` | Herramientas registradas (estándar + `remember`/`memory_search`). |
| `POST /v1/chat` | Completado stateless: `{"messages":[{"role","content"}], "model", "temperature", "max_tokens"}`. |
| `POST /v1/sessions` | Crea una sesión: `{}` o `{"agent": "research"}` -> `{"session_id", "model", "agent"}`. |
| `POST /v1/sessions/{id}/chat` | Turno con sesión+memoria: `{"message", "model"}` -> `{"reply", "context", "steps", ...}`. |
| `GET /v1/sessions/{id}/messages` | Historial de la sesión. |
| `DELETE /v1/sessions/{id}` | Elimina la sesión. |
| `POST /v1/sessions/{id}/run` | Ejecuta una herramienta bajo permisos: `{"tool", "args"}` -> `{"result": ToolResult}`. |
| `POST /v1/sessions/{id}/remember` | Guarda un hecho: `{"content", "kind", "source"}`. |
| `GET /v1/sessions/{id}/memory?q=` | Sin `q`: memorias recientes; con `q`: búsqueda con contexto. |
| `GET /v1/agents` | Presets de agentes disponibles (`name`, `description`). |
| `POST /v1/agents/{name}/chat` | Turno con el agente persistente por nombre: `{"message", "model"}` -> `{"agent", "reply", "model", "steps"}`. |

Notas:

- El endpoint de sesión reutiliza `ChatSession` + `MemoryService`: cada turno se persiste y se inyecta el contexto relevante antes de llamar al LLM.
- Si la sesión se creó con `agent`, `/chat` usa `Agent.act` y devuelve `steps` (una entrada por tool-call: nombre, args, ok, mensaje, datos) además de la respuesta.
- Los agentes estatelés persisten por nombre en el `AppState` (`nova.api.app`): mantienen sesión, memoria propia y historial entre llamadas.
- La API nunca pregunta interactivamente: un permiso `ASK` se resuelve denegado (`result.ok == false`). Las reglas `allow` siguen ejecutando directo (p. ej. `calculate`).
- Errores del proveedor -> `502`; sesión/agente inexistente -> `404`.