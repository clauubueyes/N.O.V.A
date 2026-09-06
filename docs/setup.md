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

```powershell
.\.venv\Scripts\nova
# o
.\.venv\Scripts\python -m nova
```

Comandos dentro del chat: `/exit`, `/clear`, `/models`, `/model <nombre>`, `/tools`, `/run <tool> <json>`, `/remember <text>`, `/memory [query]`, `/help`.

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