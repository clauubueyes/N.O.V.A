# N.O.V.A.

**N.O.V.A. = Neural Operations & Virtual Assistant**

![Versión](https://img.shields.io/badge/versi%C3%B3n-0.17.0-1e88e5)
![Licencia](https://img.shields.io/badge/licencia-MIT-4caf50)

Tu asistente personal de IA. **Privado. Local. Tuyo.**

N.O.V.A. es un asistente de IA completo que corre **en tu propio ordenador**. No necesitas cuentas de servicios externos, no pagas nada, y tus datos nunca salen de tu máquina. Piensa en algo como JARVIS, pero real, gratuito y tuyo.

---

## Qué puede hacer

- **Conversar** con modelos de IA locales (sin Internet, sin APIs de pago)
- **Recordar** lo que le dices (memoria persistente entre sesiones)
- **Buscar en Internet** (opcional, solo si tú lo activas)
- **Ejecutar herramientas** (abrir apps, archivos, convertir unidades, etc.)
- **Hablar** por voz (100% local, sin servicios externos)
- **Compartir acceso** con otra persona vía un enlace temporal (sin configurar nada)
- **Automatizar** tareas repetitivas con reglas y workflows

Todo corre en tu PC. La IA propone, N.O.V.A. decide y ejecuta bajo tu supervisión.

---

## Instalación más fácil: un solo archivo

Si solo quieres usar N.O.V.A. sin tocar código:

1. **Descarga** la última versión (**N.O.V.A. 0.17.0**): [`NOVA-Setup.exe`](https://github.com/clauubueyes/N.O.V.A/releases/latest) (~400 MB) desde GitHub Releases
    - SHA256: `d2ac93cbe96717f5e118f1dabee577620411955503b8046018ace191565fc9f3`
2. **Ejecútalo** — no necesita Python ni nada instalado
3. El instalador comprueba tu hardware, elige el mejor modelo para tu PC, y lo prepara automáticamente
4. En unos minutos tienes N.O.V.A. lista

Para quitarla: ejecuta `NOVA-Uninstall.exe` (está en la carpeta de instalación).

> **Requisitos:** Windows 10 22H2+ (64 bits), 4+ GB de RAM, conexión a Internet solo la primera vez (para descargar el modelo).

---

## Instalación desde código (desarrolladores)

```powershell
# Clona el repositorio
git clone https://github.com/clauubueyes/N.O.V.A
cd N.O.V.A

# Instala con el instalador automático (recomendado)
.\install.ps1

# O manualmente
python -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev]"
.\.venv\Scripts\nova
```

```bash
# Linux / macOS
./install.sh
```

### Comandos principales

| Comando | Qué hace |
|---|---|
| `nova` | Abre el chat en terminal |
| `nova-desktop` | Abre la app de escritorio (Qt/PySide6) |
| `nova-setup` | Asistente de instalación y configuración |
| `nova-api` | Sirve la API REST + interfaz web |
| `nova-agent` | Agente local (process background) |

---

## Cómo funciona (la idea fundamental)

```
Tú → N.O.V.A. → Modelo de IA (local) → N.O.V.A. Core → Permisos → Herramienta → Resultado → Tú
```

El modelo de IA **propone** qué hacer. N.O.V.A. **decide** si está permitido. Las herramientas **ejecutan** bajo permisos. El LLM nunca tiene control directo de tu ordenador.

---

## Compartir acceso con alguien

Si quieres que otra persona use tu N.O.V.A. a distancia:

1. Abre Ajustes en la app de escritorio
2. Haz clic en **"Crear enlace público"**
3. Copia el enlace y envíaselo a quien quieras
4. Esa persona abre el enlace en su navegador y ya puede chatear contigo

No necesita instalar nada. No necesita clonar el repo. Solo un navegador.

El enlace es temporal y se cierra cuando tú lo desactivas.

---

## Privacidad

- **Todo es local**: el modelo de IA corre en tu PC, no en la nube
- **Sin cuentas obligatorias**: funciona sin APIs de pago ni registros
- **Tus datos son tuyos**: conversaciones, memoria y archivos quedan en tu máquina
- **Nada sale sin tu permiso**: Internet solo se usa si activas las web tools o el túnel
- **Tú controlas**: el Permission System decide qué herramientas puede usar la IA

---

## Modelo de seguridad

N.O.V.A. tiene un sistema de permisos estricto:

- Las herramientas peligrosas (ejecutar archivos, acceder a Internet) están **desactivadas por defecto**
- Puedes activar herramientas específicas en `config/config.yaml`
- Cada acción queda registrada en un log de auditoría
- La IA nunca puede auto-otorgarse permisos

---

## Hardware mínimo recomendado

| RAM | Modelo que se instala | Experiencia |
|---|---|---|
| 4–8 GB | llama3.2:3b | Rápido, básico |
| 8–16 GB | llama3.1:8b | Equilibrado |
| 16+ GB | llama3.1:8b + coder | Completo |

El instalador automático detecta tu hardware y elige el mejor modelo.

---

## Modelos disponibles

N.O.V.A. usa **Ollama** para ejecutar modelos locales. Puedes cambiar de modelo desde Ajustes o desde el chat:

```text
/route escribe una función en python   # ver qué modelo se elige
/catalog                                # listar modelos disponibles
```

---

## Estructura del proyecto

```
config/config.yaml    Configuración (modelos, permisos, API, router)
nova/
  core/              Config, logging, contexto, auditoría
  llm/               Proveedor LLM (Ollama) + Model Router
  tools/             Herramientas + Permission System
  agents/            5 agentes paramétricos (general, coding, research, system, automation)
  memory/            Memoria persistente SQLite + embeddings
  desktop/           App de escritorio (PySide6/Qt) + túnel
  api/               API REST (FastAPI) + frontend web
  cli/               Chat por terminal
  automation/        Scheduler + workflows
  voice/             Voz 100% local (STT Vosk + TTS pyttsx3)
  plugins/           Sistema de plugins extensible
  setup/             Instalador y configurador
web/                 Frontend web (HTML/CSS/JS, sin build)
tests/               Tests (pytest)
docs/                Documentación completa
scripts/             Scripts de build (PyInstaller)
```

---

## Para desarrolladores

### Requisitos

- Python 3.11+
- Ollama (para modelos locales)
- Windows, Linux o macOS

### Instalación en desarrollo

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev,desktop]"
.\.venv\Scripts\nova
```

### Tests

```powershell
.\.venv\Scripts\pytest          # ejecutar todos
.\.venv\Scripts\pytest tests/test_desktop_product.py  # tests del desktop
```

### Build del instalador Windows

```powershell
.\.venv\Scripts\python -m pip install -e ".[build,desktop]"
.\.venv\Scripts\python scripts\build_windows.py
# Produces: dist/NOVA-Setup.exe, dist/NOVA-Uninstall.exe
```

### Comandos de desarrollo

| Comando | Qué hace |
|---|---|
| `nova` | Chat en terminal |
| `nova-setup` | Configuración inicial |
| `nova-setup doctor` | Diagnóstico del sistema |
| `nova-api` | API REST + UI web en `http://127.0.0.1:8000` |
| `nova-agent` | Agente local |

---

## Documentación

| Documento | Descripción |
|---|---|
| [docs/architecture.md](docs/architecture.md) | Arquitectura y capas |
| [docs/roadmap.md](docs/roadmap.md) | Roadmap por fases |
| [docs/setup.md](docs/setup.md) | Instalación y configuración |
| [docs/development.md](docs/development.md) | Guía de desarrollo |
| [docs/decisions.md](docs/decisions.md) | Decisiones arquitectónicas (ADR) |
| [docs/security.md](docs/security.md) | Modelo de seguridad y permisos |
| [docs/tools.md](docs/tools.md) | Catálogo de herramientas |
| [docs/models.md](docs/models.md) | Modelos y política de selección |
| [docs/troubleshooting.md](docs/troubleshooting.md) | Problemas comunes |
| [docs/api.md](docs/api.md) | APIs internas y externas |
| [docs/desktop-product.md](docs/desktop-product.md) | Arquitectura del desktop |
| [docs/lifecycle.md](docs/lifecycle.md) | Ciclo de vida e instalación |
| [CHANGELOG.md](CHANGELOG.md) | Historial de cambios |

---

## Licencia

MIT — libre de usar, modificar y distribuir. Ver [LICENSE](LICENSE).

---

**N.O.V.A.** — Neural Operations & Virtual Assistant. Tu IA, tu máquina, tus reglas.
