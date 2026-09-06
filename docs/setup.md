# Setup

## Requisitos

- **Python** 3.11+ (verificado: 3.11.9).
- **Ollama** corriendo en `http://localhost:11434` (instalado: 0.33.3).
- Modelos descargados, p. ej.:
  ```powershell
  ollama pull llama3.1:8b
  ollama pull qwen2.5-coder:7b
  ollama pull nomic-embed-text   # embeddings para la memoria (PHASE 3)
  ```
  Si falta el modelo de embeddings, N.O.V.A. funciona igual con búsqueda por keywords.

## Instalación

Desde la raíz del proyecto:

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install --upgrade pip
.\.venv\Scripts\python -m pip install -e ".[dev]"
```

Instala el paquete `nova` en modo editable + dependencias de desarrollo (`pytest`).

## Configuración

Copia/edita `config/config.yaml`:

```yaml
llm:
  provider: ollama
  base_url: http://localhost:11434
  default_model: llama3.1:8b
  embedding_model: nomic-embed-text
  temperature: 0.7
```

Cualquier valor se puede sobrescribir con variables de entorno `NOVA_*`:

```powershell
$env:NOVA_LLM_DEFAULT_MODEL = "qwen2.5-coder:7b"   # PowerShell
set NOVA_LLM_DEFAULT_MODEL=qwen2.5-coder:7b          # cmd
```

## Ejecución

CLI interactivo:

```powershell
.\.venv\Scripts\nova
# o
.\.venv\Scripts\python -m nova
```

API REST + interfaz web (PHASE 4):

```powershell
.\.venv\Scripts\nova-api
# o
.\.venv\Scripts\python -m nova.api.server
```

Abre `http://127.0.0.1:8000/` (chat web) o `http://127.0.0.1:8000/docs` (OpenAPI). Host/puerto en `config/config.yaml` -> `api` o con `NOVA_API_HOST` / `NOVA_API_PORT`.

Comandos dentro del chat: `/exit`, `/clear`, `/models`, `/model <nombre>`, `/tools`, `/run <tool> <json>`, `/remember <text>`, `/memory [query]`, `/agents`, `/agent <nombre> <texto>`, `/help`.

### Agentes (PHASE 5)

```powershell
# listar los presets disponibles
/agents
# pasar un texto a un agente (las tools las propone el LLM, N.O.V.A. las ejecuta)
/agent research qué sabemos del proyecto?
/agent coding refactoriza nova/tools/standard.py
```

Cada agente mantiene su propia sesión compartiendo memoria y permisos. Para que el LLM pueda
ejecutar tools sin preguntar, añade sus nombres a `permissions.allow` en `config/config.yaml`
o sube el nivel de autonomía:

```yaml
permissions:
  autonomy: full   # off | ask | full  (full = todo lo no denegado corre directo)
  allow:
    - date_time
    - calculate
```

### Ejemplos de la API

```powershell
# Salud
Invoke-RestMethod -Uri http://127.0.0.1:8000/healthz
# Crear sesión y chatear
$s = Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/v1/sessions
$body = @{ message = "Hola" } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/v1/sessions/$($s.session_id)/chat" -ContentType "application/json" -Body $body
```

API de agentes:

```powershell
# listar agentes
Invoke-RestMethod -Uri http://127.0.0.1:8000/v1/agents
# crear una sesión ligada a un agente (responde con "steps")
$s = Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/v1/sessions -ContentType "application/json" -Body '{"agent":"research"}'
# turno con el agente persistente por nombre
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/v1/agents/coding/chat -ContentType "application/json" -Body '{"message":"¿cuánto es 2+2?"}'
```

### Ejemplos de herramientas

```text
/run calculate {"expression":"2+2"}
/run date_time {"format":"%Y-%m-%d %H:%M:%S"}
/run list_dir {"path":"C:/Users/Usuario/Desktop"}
```

Si la herramienta no está en `permissions.allow`, el Permission System preguntará antes de ejecutarla (`[y/N]`).

## Tests

```powershell
.\.venv\Scripts\python -m pytest
```

El test de integración contra Ollama se omite automáticamente si Ollama no responde.

## Logs

- `logs/nova.log` — logging de la aplicación (archivo rotativo, 2 MB x 3 backups). Nivel configurable en `config/config.yaml` -> `logging.level`.
- `logs/audit.nova.jsonl` — audit de cada ejecución de herramienta (decisión, argumentos, resultado, duración; rotativo 2 MB x 3).
- `memory/nova.db` — base SQLite de memoria (hechos + trascripción de conversación; no versionar).