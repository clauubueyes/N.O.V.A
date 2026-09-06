# API

Estado: **PHASE 2**. N.O.V.A. expone API **interna** de Python (y un CLI). La API REST (FastAPI) llega en **PHASE 4** junto con la interfaz web/escritorio.

## Proveedor LLM — `nova.llm.base`

```python
class LLMProvider(ABC):
    def chat(self, request: ChatCompletionRequest) -> ChatCompletionResponse: ...
    def list_models(self) -> list[ModelInfo]: ...
    def health(self) -> bool: ...
    def close(self) -> None: ...
```

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
s.logging.level              # str
s.session.max_history_messages
s.session.system_prompt
```

Variables de entorno soportadas: `NOVA_LLM_PROVIDER`, `NOVA_LLM_BASE_URL`, `NOVA_LLM_DEFAULT_MODEL`, `NOVA_LLM_TEMPERATURE`, `NOVA_LLM_TIMEOUT_S`, `NOVA_LOGGING_LEVEL`, `NOVA_LOGGING_FILE`, `NOVA_SESSION_MAX_HISTORY_MESSAGES`, `NOVA_SESSION_SYSTEM_PROMPT`, `NOVA_PERMISSIONS_AUTONOMY`, `NOVA_AUDIT_FILE`.

## Contexto — `nova.core.session`

```python
session = ChatSession(max_history_messages=20, system_prompt="...")
session.add_user("hola")
session.add_assistant("hola de vuelta")
session.messages()                    # list[ChatMessage]
session.build_request(model="...")    # ChatCompletionRequest
session.clear()                       # conserva el system prompt
```

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

## Endpoint HTTP

No existe aún. Diseño previsto para PHASE 4:

```
POST /v1/chat     {"model": "...", "messages": [...]}
GET  /v1/models
GET  /healthz
```