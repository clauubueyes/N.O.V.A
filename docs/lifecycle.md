# Mantenimiento de la instalación local

`nova-setup` mantiene el asistente interactivo sin argumentos y los comandos
`auto`, `doctor`, `status` y `autostart --enable 0|1`. Añade:

```cmd
nova-setup install
nova-setup repair            [--non-interactive] [--config ruta]
nova-setup doctor            [--non-interactive] [--json] [--no-color|--ascii]
nova-setup update
nova-setup update --component models
nova-setup update --component ollama
nova-setup update --component both
nova-setup remove
nova-setup autostart --enable
nova-setup autostart --disable
```

`nova-setup repair` (PHASE 14.2) re-audita las dependencias reales
(`nova/setup/dependencies.py`), instala las de Python que falten con el pip del
venv activo, valida el venv (nunca lo recrea por su cuenta), reinicia Ollama si
está instalado pero no responde, re-provisiona solo los defaults seguros que el
usuario no haya definido y reporta los modelos recomendados que falten. No borra
recursos ni toca credenciales. `doctor` es ahora una auditoría completa
REQUIRED/OPTIONAL/WARNINGS/READY; `--json` emite una línea JSON por evento para
pipelines. Detalles de fases y dependencias en [setup.md](setup.md).

## Propiedad y rutas

Los instaladores `install.ps1` e `install.sh` crean las nuevas instalaciones fuera
del repositorio: `%LOCALAPPDATA%\NOVA` en Windows, `~/Library/Application Support/NOVA`
en macOS y `$XDG_DATA_HOME/NOVA` (por defecto `~/.local/share/NOVA`) en Linux.
`NOVA_HOME` permite seleccionar otra ubicación. El entorno está en `venv`, junto
a `config.yaml`, `data`, `logs` y los caches creados por el instalador. Los
ejecutables se encuentran en `venv\Scripts` en Windows y `venv/bin` en POSIX.
El entorno de desarrollo `.venv` del repositorio se conserva.

`installation-state.json` registra fecha, repositorios protegidos, configuración,
hardware, stack, recursos generados, autostart y propiedad de Ollama. Cada modelo
incluye nombre, variante, categoría, fecha, endpoint, propiedad y digest cuando
está disponible. La propiedad se registra al finalizar cada descarga, también en
el asistente y la API. Reutilizar un modelo preexistente nunca transfiere su propiedad.
Los recursos se guardan incrementalmente y el JSON se reemplaza de forma atómica;
un bloqueo entre procesos impide dos operaciones de mantenimiento simultáneas.

`NOVA_CONFIG` selecciona la configuración de ejecución. Sin esa variable se usa
la ruta registrada por el instalador, después el `config.yaml` de la instalación
si existe y, en caso contrario, el tradicional `config/config.yaml`.
Un `--config` explícito sigue teniendo prioridad. La API
de setup comparte esta resolución. Los valores de usuario ajenos al stack se
conservan durante la actualización.

Las instalaciones anteriores sin registro no se adoptan por deducción. Sus
modelos y archivos no pasan a ser propiedad de N.O.V.A. por aparecer en una
configuración. Los recursos situados dentro de un repositorio se muestran como
`KEEP`, incluso si son un entorno virtual o una configuración generada antigua.

## Actualización

Se vuelve a detectar CPU, arquitectura, GPU, RAM, VRAM y espacio del volumen de
modelos de Ollama (`OLLAMA_MODELS` o el directorio de modelos del usuario). La
API local `/api/tags` aporta el inventario y `/api/version` la versión del servidor.
No se administra un servidor remoto. La ausencia de Ollama genera un aviso y
conserva la configuración.

El selector adaptativo recibe el catálogo actualizado, filtra arquitectura y
versión mínima de Ollama, evalúa compatibilidad y compara la propuesta con la
configuración activa. Muestra modelos conservados, sustituciones, roles
desactivados y almacenamiento adicional, deduplicando modelos compartidos entre
roles. El espacio se comprueba para todas las descargas, con 12 GB de reserva;
si no se puede verificar, la descarga se bloquea. Los modelos ya presentes no
necesitan espacio adicional ni se descargan otra vez.

Después de confirmar se comprueba de nuevo cada modelo antes de descargar. Si
hay un fallo, la configuración anterior sigue activa y las descargas completadas
permanecen registradas para reanudar. La configuración se cambia solo cuando los
modelos seleccionados están presentes. Se detecta si el usuario editó el YAML
mientras se descargaba. **`update` nunca borra modelos anteriores.**

La versión estable de Ollama se consulta en las releases oficiales de GitHub,
comprobando plataforma, arquitectura y activo de instalación disponible. Su
actualización tiene confirmación independiente. En Windows se utiliza winget;
si no está disponible se descarga el instalador oficial de la versión seleccionada.
En Linux/macOS se indica el enlace oficial para actualizar con el mecanismo del
sistema. Elegir ambos componentes vuelve a evaluar los modelos tras actualizar
Ollama; se usa la versión que realmente publica el servidor.

## Catálogo remoto

Por defecto se consulta la biblioteca pública de Ollama y las páginas de tags:
se descubren familias recientes, variantes y cuantizaciones. La exploración
está acotada a 16 familias, con timeouts y límite de tamaño. Las cuantizaciones
de modelos conocidos heredan sus requisitos y se reestiman por tamaño; las de
mayor precisión se proponen solo cuando el selector considera que encajan.

**Un nombre nuevo no demuestra mejor calidad ni compatibilidad.** Las familias
desconocidas se muestran como candidatos pendientes de revisión; no desplazan
automáticamente modelos conocidos. Para publicar recomendaciones revisadas de
familias nuevas se puede proporcionar un feed JSON HTTPS con
`--catalog-url` o `NOVA_MODEL_CATALOG_URL`. No existe un servicio de catálogo
propio de N.O.V.A. desplegado por este cambio.

Formato del feed (nombres ilustrativos):

```json
{
  "schema_version": 1,
  "models": [{
    "name": "example:8b-q5_K_M",
    "role": "general",
    "params_b": 8,
    "quant": "Q5_K_M",
    "context": 8192,
    "weights_gb": 6,
    "memory_gb": 9,
    "min_ram_gb": 12,
    "priority": 0,
    "min_ollama": "0.12.0",
    "architectures": ["AMD64", "x86_64", "arm64", "aarch64"],
    "verified": true,
    "description": "Recomendación revisada para chat general"
  }]
}
```

Los roles son `embedding`, `general`, `fast`, `coding`, `reasoning` y `vision`.
Una prioridad menor representa mayor preferencia, siempre sujeta a la evaluación
de recursos. El feed es una fuente de recomendaciones de confianza elegida por
el usuario; no contiene comandos ejecutables. Se valida antes de incorporarlo.
El cache `model-catalog.json` permite mantener las recomendaciones sin conexión;
un fallo remoto muestra `Could not refresh model catalog. Using local catalog.`.
El catálogo incluido en el paquete sigue siendo el último recurso.

## Eliminación

`remove` muestra rutas exactas, modelos y componentes, y pide dos confirmaciones
con respuesta predeterminada negativa. Desinstalar Ollama requiere una tercera
confirmación específica. No hay un `--yes` que salte estas preguntas.

La comprobación de rutas bloquea el repositorio, sus descendientes, los directorios
que lo contienen, repositorios anidados, enlaces y junctions. Se repite justo
antes de cada borrado, incluyendo la limpieza diferida del entorno en Windows.
Un repositorio de dotfiles en el directorio de usuario no convierte automáticamente
todo AppData en código fuente; los repositorios de la instalación y el directorio
de ejecución siguen protegidos explícitamente.

Solo se eliminan modelos registrados como propios, mediante `/api/delete`.
Si su digest registrado cambió, se conservan. La ausencia de Ollama detiene la
operación antes de borrar datos cuando todavía hay modelos pendientes.
Ollama preexistente siempre se conserva. Incluso cuando lo instaló N.O.V.A.,
se conserva si contiene modelos externos. La desinstalación de Windows utiliza
su desinstalador instalado y verifica el directorio registrado. No se borra
recursivamente la carpeta compartida `.ollama`.

El autostart se elimina únicamente si coincide con la entrada registrada.
Se detienen procesos de los entornos propios por su ruta de ejecutable, sin
terminar procesos por un nombre genérico. Este instalador utiliza autostart de
usuario y **no crea servicios del sistema ni tareas programadas**; tampoco elimina
servicios o tareas desconocidos. El desinstalador oficial de Ollama administra
las integraciones creadas por su propio instalador.

En Windows un Python externo al entorno ejecuta la limpieza diferida cuando el
entorno actual está bloqueado. Conserva el registro hasta terminar y comprueba
que el estado no haya cambiado. Si no puede terminar deja un archivo
`installation-state.cleanup-error.txt`; cerrar procesos restantes y repetir
`remove` permite reintentarlo. Los errores de permisos se informan sin elevar
privilegios silenciosamente. Una eliminación repetida sin estado responde
`N.O.V.A. installation not found. Nothing to remove.`

## Validación

```cmd
.venv\Scripts\python -m pytest
.venv\Scripts\python -m pytest tests\test_lifecycle.py
```

Las pruebas de mantenimiento usan directorios temporales y sustituyen red,
procesos y desinstaladores. Incluyen protección byte a byte del repositorio,
propiedad de modelos, actualizaciones con hardware distinto, catálogo sin red,
falta de espacio, confirmaciones, idempotencia y reanudación tras fallos.
No desinstalan Ollama ni el entorno real del desarrollador.

Referencias de los adaptadores: [biblioteca y tags](https://ollama.com/library),
[API de eliminación](https://docs.ollama.com/api/delete),
[instalación Windows](https://docs.ollama.com/windows) y
[releases de Ollama](https://github.com/ollama/ollama/releases).
