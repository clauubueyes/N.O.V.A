# Setup

## Requisitos

- **Python** 3.11+ (verificado: 3.11.9).
- **Ollama** corriendo en `http://localhost:11434` (instalado: 0.33.3).
- Un modelo descargado, p. ej.:
  ```powershell
  ollama pull llama3.1:8b
  ollama pull qwen2.5-coder:7b
  ```

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

Comandos dentro del chat: `/exit`, `/clear`, `/models`, `/model <nombre>`, `/help`.

## Tests

```powershell
.\.venv\Scripts\python -m pytest
```

El test de integración contra Ollama se omite automáticamente si Ollama no responde.

## Logs

`logs/nova.log` (archivo rotativo, 2 MB x 3 backups). Nivel configurable en `config/config.yaml` -> `logging.level`.