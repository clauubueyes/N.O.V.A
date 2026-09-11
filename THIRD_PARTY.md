# Componentes distribuidos

N.O.V.A. se publica bajo la licencia del archivo LICENSE. El ejecutable incluye Python
(PSF), PySide6/Qt (LGPLv3 y componentes con licencias adicionales), Chromium/Qt WebEngine
(BSD y licencias de terceros), PyInstaller (GPL con excepción de distribución), FastAPI
(MIT), Uvicorn (BSD), httpx (BSD), Pydantic (MIT), PyYAML (MIT), Rich (MIT), pypdf (BSD),
python-docx (MIT), lxml (BSD) y Pillow (HPND).

Qt se distribuye como bibliotecas dinámicas, separadas del ejecutable. Deben conservarse
sus avisos de copyright, licencias y derechos de sustitución/relinkado. Los avisos de Qt
y Chromium están disponibles en https://doc.qt.io/qt-6/licenses.html y
https://doc.qt.io/qt-6/qtwebengine-licensing.html. Fuentes de la versión de Qt:
https://download.qt.io/official_releases/qt/ y https://code.qt.io/.

Ollama se descarga desde su distribución oficial y conserva su propio instalador,
avisos y licencia. Cada modelo tiene su licencia; seleccionar un modelo no cambia
sus condiciones. Antes de publicar una release, conservar los avisos de todas las
dependencias transitivas y comprobar las licencias del catálogo distribuido.
