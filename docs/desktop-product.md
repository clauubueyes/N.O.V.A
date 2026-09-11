# N.O.V.A. Desktop — arquitectura y entrega

## Auditoría previa (11 de septiembre de 2026)

Se han revisado la estructura completa, los contratos de todas las capas, la documentación
de `/docs`, los puntos de entrada, configuración, persistencia, frontend, setup y mantenimiento.
Las rutas de API comentadas se consideran parte del contrato activo.

| Componente actual | Ejecución y dependencias | Decisión |
|---|---|---|
| Core, agentes, permisos, herramientas, automatización | Python 3.11+, Pydantic, YAML, httpx | Reutilizar; empaquetar el intérprete |
| API | FastAPI + Uvicorn, puerto local 8000 | Reutilizar; proceso independiente autenticado en Desktop |
| CLI y `nova-agent` | Python, Rich; entrada de terminal | Conservar como herramientas de desarrollo |
| `web/` | HTML, CSS y JavaScript sin build | Rehacer experiencia; ningún Node/npm en el producto |
| Ollama | Servicio HTTP local 11434, modelos en `.ollama/models` o `OLLAMA_MODELS` | Detectar, iniciar y preparar desde GUI |
| OpenCode | Servidor opcional 4096, autenticación externa | Conservar opt-in; no instalar Node ni OpenCode para chat local |
| Voz | numpy, Vosk, sounddevice, pyttsx3 + modelo STT | Extra opcional; ocultar micrófono si no está disponible |
| Memoria | SQLite, hechos y transcripciones; embeddings locales opcionales | Reutilizar; añadir índice persistente de conversaciones |
| Preferencias | YAML + overrides `NOVA_*` | Edición validada y atómica desde Ajustes |
| Instalación | `install.ps1`, `install.sh`, `nova.setup` | Conservar CLI; sustituir distribución editable por bundle |
| Mantenimiento | Estado de propiedad, bloqueos, modelos, actualización y borrado | Reutilizar sin adoptar recursos externos |
| Vercel | Cliente estático | Sin Ollama, archivos, ejecución ni memoria del PC |

Problemas encontrados: instalación `pip -e` ligada al repositorio y extras dev innecesarios;
frontend moderno fuera del paquete (fallback HTML antiguo); sesiones solo en RAM;
historial limitado mostrado incluyendo el system prompt; configuración guardada sin aplicar
al proceso activo; selección de último recurso que ignora incompatibilidad de RAM; detección
Ollama mezcla inventario CLI con endpoint HTTP; UI pide token/comandos; adjuntos inexistentes;
autostart abre un CLI; descarga mutable por GET; no existe artefacto de escritorio ni actualización
de binarios de la aplicación. Riesgos de distribución: arquitectura, espacio en volumen correcto,
proxies/red interrumpida, antivirus/firma, runtime gráfico, DLL y recursos ausentes, rutas con
espacios y usuario sin permisos administrativos.

## Arquitectura elegida

`NOVA-Setup.exe` → bundle autocontenido → `NOVA.exe` (Qt/PySide6 + WebEngine)
→ Core (FastAPI, proceso independiente en loopback con token)
→ proveedores locales / ToolRunner → Windows.

Qt aporta ventana, bandeja e instalador gráfico. PyInstaller incorpora Python, Qt y los assets;
no se instalan Python, pip, Node ni npm en el equipo destinatario. El mayor tamaño del bundle
es el coste de incluir WebEngine y evitar depender de un navegador instalado. Separar
GUI/Core permite cerrar la ventana conservando automatizaciones en la sesión del usuario.
Se elige un proceso de usuario, no LocalSystem: los servicios Windows en sesión 0 no son
adecuados para abrir aplicaciones en el escritorio interactivo. La ejecución sin iniciar
sesión requeriría un servicio separado, sin herramientas de interfaz gráfica.

Datos y configuración se almacenan fuera del bundle. Actualizar binarios no debe borrar
conversaciones, preferencias ni modelos. Cada tarea de preparación informa su estado real;
los porcentajes representan bytes descargados/copias, nunca tiempo simulado. Solo se declara
listo después de verificar el motor y el modelo seleccionado. Reintentar reutiliza descargas.

## Privacidad y Remote

Chat, memoria, adjuntos, inferencia, herramientas y automatización permanecen en el PC.
La preparación inicial descarga componentes/modelos; después el chat local funciona offline.
Web Tools y OpenCode requieren red y consentimiento explícito. Los adjuntos se procesan
localmente y nunca activan fallback cloud por sí solos.

Remote requiere una futura pasarela de conexiones salientes WSS/TLS desde el PC: ningún
port-forward, UPnP ni enlace HTTP al PC desde Vercel. El relay no puede autorizar herramientas.
Contrato de orden: `request_id`, `device_id`, sesión, caducidad, nonce, acción y argumentos;
el Core verifica identidad, revocación, caducidad, antirrepetición y permisos. Emparejamiento
con aprobación local, credenciales distintas por dispositivo guardadas como hashes, revocación
inmediata, permisos limitados y auditoría del origen. Las acciones administrativas y destructivas
requieren autorización local; nunca se convierten en permisos por petición del modelo.
Hasta disponer de relay y protocolo verificables, Remote permanece desactivado.

## Implementación incremental y validación

1. Empaquetado, preparación conservadora y rutas independientes del repositorio.
2. Desktop/Core/bandeja y configuración gráfica con estado real.
3. Persistencia, adjuntos, permisos y nueva interfaz.
4. Tests de regresión, build Windows y smoke tests aislados.

La firma de código y la prueba en una máquina Windows limpia son requisitos de publicación:
generar un ejecutable local no demuestra compatibilidad con todos los equipos. La actualización
futura debe usar un manifiesto HTTPS, versión/arquitectura, hash y firma del editor, descarga
temporal, verificación, sustitución con rollback y conservación de datos. No se ejecutará
automáticamente un binario obtenido de un enlace arbitrario.

Referencias de empaquetado: [Qt y PyInstaller](https://doc.qt.io/qtforpython-6/deployment/deployment-pyinstaller.html),
[recursos congelados](https://pyinstaller.org/en/stable/runtime-information.html),
[Ollama en Windows](https://docs.ollama.com/windows).
