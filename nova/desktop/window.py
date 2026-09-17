from __future__ import annotations

import hashlib
import json
import time

import httpx
from PySide6.QtCore import QRectF, QTimer, QUrl, Qt
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineScript, QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QApplication, QLabel, QMainWindow, QMenu, QPushButton, QSystemTrayIcon, QVBoxLayout, QWidget

from nova import __version__
from nova.core.paths import installation_home
from nova.desktop.configuration import initialize
from nova.desktop.runtime import connection, spawn


def nova_icon() -> QIcon:
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor('#7fd8c4'))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(QRectF(5, 5, 54, 54), 11, 11)
    painter.setPen(QColor('#0c1016'))
    font = painter.font()
    font.setBold(True)
    font.setPixelSize(36)
    painter.setFont(font)
    painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, 'N')
    painter.end()
    return QIcon(pixmap)


class LocalPage(QWebEnginePage):
    def __init__(self, profile, base, parent):
        super().__init__(profile, parent)
        self.base = base

    def acceptNavigationRequest(self, url, navigation_type, is_main_frame):
        return url.toString().startswith(self.base + '/')


class DesktopWindow(QMainWindow):
    def __init__(self, hidden=False):
        super().__init__()
        self.setWindowTitle(f'N.O.V.A. {__version__}')
        self.resize(1200, 820)
        self.setMinimumSize(700, 520)
        self.setWindowIcon(nova_icon())
        self.setStyleSheet('QMainWindow, QWidget {background:#11151b;color:#edf0f5;font-family:Segoe UI;font-size:15px;} QPushButton {padding:12px;border:1px solid #39414c;border-radius:8px;}')
        self.base = ''
        self.token = ''
        self.view = None
        self.profile = None
        self.waiting = QWidget()
        layout = QVBoxLayout(self.waiting)
        layout.addStretch()
        self.label = QLabel('Preparando tu espacio…')
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.label)
        self.retry = QPushButton('Reintentar')
        self.retry.clicked.connect(self.start_core)
        self.retry.hide()
        layout.addWidget(self.retry)
        layout.addStretch()
        self.setCentralWidget(self.waiting)
        self.tray = QSystemTrayIcon(nova_icon(), self)
        self.tray.setToolTip(f'N.O.V.A. {__version__} · Tu asistente local')
        menu = QMenu()
        for title, callback in [('Abrir N.O.V.A.', self.open), ('Nueva conversación', lambda: self.open('new')),
                                ('Estado', lambda: self.open('status')), ('Pausar / reanudar', self.pause),
                                ('Ajustes', lambda: self.open('settings')), ('Salir', self.quit)]:
            action = QAction(title, self)
            action.triggered.connect(callback)
            menu.addAction(action)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda reason: self.open() if reason == QSystemTrayIcon.ActivationReason.Trigger else None)
        self.tray.show()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.connect_core)
        self.start_core()
        if not hidden or not QSystemTrayIcon.isSystemTrayAvailable():
            self.show()

    def start_core(self):
        self.retry.hide()
        self.label.setText('Abriendo N.O.V.A.…')
        self.started = time.monotonic()
        found = connection()
        if not found and not (installation_home() / 'core-runtime.json').exists():
            self.process = spawn('--core')
        self.timer.start(500)

    def connect_core(self):
        found = connection()
        if not found:
            if time.monotonic() - self.started > 45:
                self.timer.stop()
                self.label.setText('N.O.V.A. no ha podido iniciar el núcleo local.')
                self.retry.show()
            return
        self.timer.stop()
        self.base, self.token = found
        self.profile = QWebEngineProfile(self)
        self.profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.MemoryHttpCache)
        self.profile.downloadRequested.connect(lambda request: request.cancel())
        self.view = QWebEngineView(self)
        page = LocalPage(self.profile, self.base, self.view)
        script = QWebEngineScript()
        script.setName('NOVA local session')
        script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
        script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
        script.setRunsOnSubFrames(False)
        script.setSourceCode('if(location.origin===' + json.dumps(self.base) + '){sessionStorage.setItem("nova.token",' + json.dumps(self.token) + ');}')
        page.scripts().insert(script)
        page.settings().setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanOpenWindows, False)
        self.view.setPage(page)
        self.setCentralWidget(self.view)
        self.view.load(QUrl(self.base + '/'))

    def open(self, section=''):
        self.showNormal()
        self.raise_()
        self.activateWindow()
        if self.view and isinstance(section, str) and section:
            self.view.page().runJavaScript('window.novaNavigate && window.novaNavigate(' + json.dumps(section) + ')')

    def pause(self):
        if not self.base:
            return
        try:
            with httpx.Client(base_url=self.base, headers={'Authorization': 'Bearer ' + self.token}, timeout=3, trust_env=False) as client:
                state = client.get('/v1/desktop/status').json()
                client.post('/v1/desktop/pause', json={'paused': not state['paused']}).raise_for_status()
        except (httpx.HTTPError, ValueError, KeyError):
            self.open('status')

    def quit(self):
        if self.base:
            try:
                httpx.post(self.base + '/v1/desktop/exit', headers={'Authorization': 'Bearer ' + self.token}, timeout=3, trust_env=False)
            except httpx.HTTPError:
                pass
        self.tray.hide()
        QApplication.instance().quit()

    def closeEvent(self, event):
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.hide()
            event.ignore()
        else:
            event.accept()
            QApplication.instance().quit()


def run_window(hidden=False) -> int:
    app = QApplication.instance() or QApplication([])
    app.setApplicationName('N.O.V.A.')
    app.setQuitOnLastWindowClosed(False)
    initialize()
    name = 'NOVA-Desktop-' + hashlib.sha256(str(installation_home()).encode()).hexdigest()[:20]
    socket = QLocalSocket()
    socket.connectToServer(name)
    if socket.waitForConnected(300):
        socket.write(b'open')
        socket.flush()
        socket.waitForBytesWritten(300)
        return 0
    server = QLocalServer()
    QLocalServer.removeServer(name)
    server.listen(name)
    window = DesktopWindow(hidden)
    def receive():
        peer = server.nextPendingConnection()
        if peer:
            peer.disconnectFromServer()
            peer.deleteLater()
        window.open()
    server.newConnection.connect(receive)
    return app.exec()
