# Troubleshooting

## `Could not reach Ollama at http://localhost:11434`

Ollama no está corriendo o usa otro puerto.

```powershell
ollama serve          # arrancar en primer plano
ollama list           # comprobar que responde
```

Si Ollama corre en otro host/puerto, ajusta `config/config.yaml` -> `llm.base_url` o la env `NOVA_LLM_BASE_URL`.

## `Unknown LLM provider: '...'`

El valor `llm.provider` de la config no coincide con ninguno registrado. Valores válidos hoy: `ollama`. Revisa `config/config.yaml`.

## `model_not_found` / salida extraña del modelo

El modelo configurado no está descargado en Ollama:

```powershell
ollama pull llama3.1:8b
```

## El chat no emite respuestas y no hay error

Comprueba `logs/nova.log` y sube el nivel: `NOVA_LOGGING_LEVEL=DEBUG`. La API compatible de Ollama devuelve el texto en `choices[0].message.content`.

## "python3" devuelve un stub de la Microsoft Store (Windows)

El `python3` del PATH puede apuntar al instalador de la Store. Usa siempre `.venv\Scripts\python`. Si falta, crea el venv con la ruta completa del intérprete real:

```powershell
& "C:\Users\Usuario\AppData\Local\Programs\Python\Python311\python.exe" -m venv .venv
```

## Tests de integración que se "saltan" (skip)

El test `test_ollama_integration_reachable` se omite si Ollama no responde. Es intencional: los tests unitarios usan clientes simulados. Si quieres forzar integración, ten Ollama corriendo.

## `config.yaml` no se encuentra

La ruta por defecto es relativa al directorio de trabajo. Ejecuta desde la raíz del proyecto, o pasa la ruta:

```python
from nova.core.config import load_settings
settings = load_settings(path="C:/ruta/a/config.yaml")
```

## La consola muestra caracteres raros

El CLI usa solo ASCII de forma deliberada (compatibilidad con consolas Windows cp1252). No es un bug.

## `nova-api` no arranca / puerto ocupado

El puerto por defecto es `8000`. Si está ocupado, cambia `config/config.yaml` -> `api.port` o usa la env `NOVA_API_PORT`:

```powershell
$env:NOVA_API_PORT = "8080"
.\.venv\Scripts\nova-api
```