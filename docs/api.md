# API

Estado: **PHASE 1**. Por ahora N.O.V.A. expone únicamente una API **interna** de Python (y un CLI). La API REST (FastAPI) llega en **PHASE 4** junto con la interfaz web/escritorio.

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

Variables de entorno soportadas: `NOVA_LLM_PROVIDER`, `NOVA_LLM_BASE_URL`, `NOVA_LLM_DEFAULT_MODEL`, `NOVA_LLM_TEMPERATURE`, `NOVA_LLM_TIMEOUT_S`, `NOVA_LOGGING_LEVEL`, `NOVA_LOGGING_FILE`, `NOVA_SESSION_MAX_HISTORY_MESSAGES`, `NOVA_SESSION_SYSTEM_PROMPT`.

## Contexto — `nova.core.session`

```python
session = ChatSession(max_history_messages=20, system_prompt="...")
session.add_user("hola")
session.add_assistant("hola de vuelta")
session.messages()                    # list[ChatMessage]
session.build_request(model="...")    # ChatCompletionRequest
session.clear()                       # conserva el system prompt
```

## Uso como librería (ejemplo)

```python
from nova.core.config import load_settings
from nova.core.session import ChatSession
from nova.llm import create_provider

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
```

## Endpoint HTTP

No existe aún. Diseño previsto para PHASE 4:

```
POST /v1/chat     {"model": "...", "messages": [...]}
GET  /v1/models
GET  /healthz
```