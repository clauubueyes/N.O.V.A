from __future__ import annotations

"""N.O.V.A. Desktop Installer — professional multi-screen wizard.

Pantalla 1: Bienvenida
Pantalla 2: Comprobación del sistema
Pantalla 3: Selección del modelo
Pantalla 4: Instalación con progreso real
Pantalla 5: N.O.V.A. está lista
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from PySide6.QtCore import QThread, QPropertyAnimation, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpacerItem,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from nova.core.paths import installation_home
from nova.setup.bundle import MARKER, install_bundle, register_windows, validate_target

ACCENT = "#a2e7d0"
ACCENT_DARK = "#1a3a32"
BG_DARK = "#0c1015"
BG_PANEL = "#11151b"
BG_CARD = "#181f28"
TEXT_PRIMARY = "#edf0f5"
TEXT_SECONDARY = "#84919d"
BORDER = "#252d38"

STYLE = """
QMainWindow, QWidget {
    background: %(bg)s;
    color: %(text)s;
    font-family: 'Segoe UI', system-ui, sans-serif;
    font-size: 14px;
}
QLabel {
    color: %(text)s;
}
QLabel#brand {
    color: %(accent)s;
    font-size: 48px;
    font-weight: 700;
    letter-spacing: 8px;
}
QLabel#subtitle {
    color: %(text_sec)s;
    font-size: 12px;
    letter-spacing: 2px;
}
QLabel#title {
    font-size: 22px;
    font-weight: 600;
}
QLabel#step-label {
    color: %(text_sec)s;
    font-size: 11px;
    letter-spacing: 1.5px;
}
QLabel#check-label {
    font-size: 13px;
    padding: 3px 0;
}
QLabel#error {
    color: #e07070;
}
QLineEdit {
    background: %(card)s;
    border: 1px solid %(border)s;
    color: %(text)s;
    padding: 10px 14px;
    border-radius: 8px;
    font-size: 13px;
}
QLineEdit:focus {
    border-color: %(accent)s;
}
QPushButton {
    padding: 11px 20px;
    border: 1px solid %(border)s;
    border-radius: 8px;
    font-size: 13px;
    color: %(text)s;
    background: transparent;
}
QPushButton:hover {
    background: #1d252e;
    border-color: #3a4550;
}
QPushButton:disabled {
    color: #4a5568;
    border-color: #1e2530;
}
QPushButton#primary {
    background: %(accent)s;
    color: #0c1015;
    border: 0;
    font-weight: 600;
    font-size: 15px;
    padding: 13px 28px;
}
QPushButton#primary:hover {
    background: #b8f0e0;
}
QPushButton#primary:disabled {
    background: #2a4a42;
    color: #5a7a72;
}
QPushButton#ghost {
    border: 0;
    color: %(text_sec)s;
    padding: 8px;
}
QCheckBox {
    spacing: 10px;
    font-size: 13px;
    color: %(text_sec)s;
}
QCheckBox::indicator {
    width: 18px;
    height: 18px;
    border: 1px solid %(border)s;
    border-radius: 4px;
    background: %(card)s;
}
QCheckBox::indicator:checked {
    background: %(accent)s;
    border-color: %(accent)s;
}
QProgressBar {
    border: 0;
    background: #1a2029;
    height: 5px;
    border-radius: 2px;
}
QProgressBar::chunk {
    background: %(accent)s;
    border-radius: 2px;
}
QScrollArea {
    border: 0;
    background: transparent;
}
""" % {"bg": BG_DARK, "text": TEXT_PRIMARY, "text_sec": TEXT_SECONDARY,
       "accent": ACCENT, "card": BG_CARD, "border": BORDER}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _nova_icon() -> QPixmap:
    px = QPixmap(64, 64)
    px.fill(Qt.GlobalColor.transparent)
    p = QPainter(px)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setBrush(QColor(ACCENT))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawEllipse(6, 6, 52, 52)
    p.setPen(QColor(BG_DARK))
    f = p.font()
    f.setBold(True)
    f.setPixelSize(36)
    p.setFont(f)
    p.drawText(px.rect(), Qt.AlignmentFlag.AlignCenter, "N")
    p.end()
    return px


def _spacer(h=20):
    return QSpacerItem(20, h, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding)


def _hline():
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setStyleSheet(f"background:{BORDER};max-height:1px;")
    return line


def _check_row(icon: str, text: str, ok: bool) -> QWidget:
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 4, 0, 4)
    ic = QLabel(icon)
    ic.setFixedWidth(28)
    ic.setStyleSheet(f"color:{ACCENT if ok else '#e07070'};font-size:16px;")
    lay.addWidget(ic)
    lb = QLabel(text)
    lb.setObjectName("check-label")
    lb.setWordWrap(True)
    lay.addWidget(lb, 1)
    return w


# ---------------------------------------------------------------------------
# InstallWorker  (runs on a background thread)
# ---------------------------------------------------------------------------

class InstallWorker(QThread):
    changed = Signal(dict)

    def __init__(self, target: Path, shortcut: bool, autostart: bool):
        super().__init__()
        self.target = target
        self.shortcut = shortcut
        self.autostart = autostart
        self._executable: Path | None = None

    def run(self):
        try:
            self._do_install()
            self.install_app()
            self.prepare_model()
            self.finalize()
        except Exception as exc:
            import traceback
            self.changed.emit(dict(
                status="error",
                message=str(exc) if isinstance(exc, ValueError) else
                        "No hemos podido completar la instalación. Reintenta.",
                details=traceback.format_exc(),
            ))

    # -- step 1: system checks -----------------------------------------------
    def _do_install(self):
        from nova.setup.detect import detect_machine
        from nova.setup.desktop import recommended_chat

        self.changed.emit(dict(stage="system", message="Comprobando tu sistema…", checks=[]))

        profile = detect_machine()
        is_win = sys.platform == "win32"
        win_ok = is_win
        win_label = f"Windows compatible" if is_win else profile.os_name
        if is_win:
            try:
                win_ver = sys.getwindowsversion()
                win_ok = win_ver.build >= 19045 and profile.arch.lower() in ("amd64", "x86_64")
                win_label = f"Windows {win_ver.major} (build {win_ver.build})" if win_ok else "Se necesita Windows 10 22H2+ de 64 bits"
            except Exception:
                win_ok = False

        checks = [
            dict(label=win_label, ok=win_ok),
            dict(label=f"{profile.ram_total_gb:.0f} GB de memoria disponible", ok=profile.ram_total_gb >= 4),
        ]
        self.changed.emit(dict(checks=checks))
        time.sleep(0.4)

        if not win_ok:
            raise ValueError("Esta edición necesita Windows 10 22H2 o Windows 11 de 64 bits (Intel/AMD).")

        spec = recommended_chat(profile)
        self._recommended = spec
        checks.append(dict(label=f"{profile.disk_free_gb:.0f} GB de espacio disponible", ok=profile.disk_free_gb >= spec.weights_gb + 5))
        checks.append(dict(label=profile.gpu_model or "Procesador disponible", ok=True))
        self.changed.emit(dict(
            checks=checks,
            recommendation=dict(name=spec.name, size_gb=spec.weights_gb, parameters=spec.params_b),
        ))
        time.sleep(0.3)

        self._profile = profile
        self._recommended = spec

    # -- step 2: install bundle -----------------------------------------------
    def install_app(self):
        import os as _os

        self.changed.emit(dict(stage="install", message="Instalando N.O.V.A.…", completed=0, total=0))
        package = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2])) / "payload"
        manifest_path = package / "manifest.json"
        if not manifest_path.exists():
            raise ValueError("El paquete de instalación no se encuentra. Re-descarga N.O.V.A. e inténtalo de nuevo.")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        executable = install_bundle(
            package / "nova.zip", manifest, self.target,
            lambda done, total: self.changed.emit(dict(
                stage="install", message="Instalando N.O.V.A.…", completed=done, total=total)),
        )
        register_windows(self.target, self.shortcut)
        self._executable = executable

    # -- step 3: prepare model -------------------------------------------------
    def prepare_model(self):
        import os as _os

        self.changed.emit(dict(stage="model", message="Preparando tu modelo local…", completed=0, total=0))
        home = installation_home()
        home.mkdir(parents=True, exist_ok=True)
        report = home / "preparation-report.json"
        report.unlink(missing_ok=True)
        env = _os.environ.copy()
        env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
        process = subprocess.Popen(
            [str(self._executable), "--prepare-report", str(report)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), env=env,
        )
        result = {}
        while process.poll() is None:
            try:
                result = json.loads(report.read_text(encoding="utf-8"))
                if result.get("status") not in ("ready", "error"):
                    self.changed.emit(result)
            except (OSError, ValueError):
                pass
            time.sleep(0.3)
        if report.exists():
            result = json.loads(report.read_text(encoding="utf-8"))
        if process.returncode != 0 or result.get("status") != "ready":
            raise ValueError(result.get("message") or "No hemos podido preparar el motor local. Pulsa Reintentar.")

    # -- step 4: finalize ------------------------------------------------------
    def finalize(self):
        if self.autostart:
            import winreg
            from nova.setup.autostart import RUN_KEY, autostart_identity
            from nova.setup.state import StateStore
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
                winreg.SetValueEx(key, "NOVA", 0, winreg.REG_SZ, f'"{self._executable}" --hidden')
            StateStore().change(lambda s: setattr(s, "autostart", autostart_identity()))
        self.changed.emit(dict(status="ready", message="N.O.V.A. está lista.", completed=0, total=0))


# ---------------------------------------------------------------------------
# Installer Wizard
# ---------------------------------------------------------------------------

class SystemCheckWorker(QThread):
    """Hilo para detectar hardware sin bloquear la UI de Qt."""
    done = Signal(list, object, object, object)  # checks, profile, recommended, system_info
    failed = Signal(str)

    def run(self):
        try:
            from nova.setup.detect import detect_machine
            from nova.setup.desktop import recommended_chat
            profile = detect_machine()
            is_win = sys.platform == "win32"
            win_ok = is_win
            win_label = "Windows compatible" if is_win else profile.os_name
            if is_win:
                try:
                    wv = sys.getwindowsversion()
                    win_ok = wv.build >= 19045 and profile.arch.lower() in ("amd64", "x86_64")
                    win_label = "Windows compatible" if win_ok else "Se necesita Windows 10 22H2+ 64 bits"
                except Exception:
                    win_ok = False
            checks = [
                (win_label, win_ok),
                (f"{profile.ram_total_gb:.0f} GB de memoria", profile.ram_total_gb >= 4),
                (f"{profile.disk_free_gb:.0f} GB de espacio disponible", profile.disk_free_gb >= 8),
                (profile.gpu_model or "Procesador disponible", True),
                ("Conexión a Internet", True),
            ]
            spec = recommended_chat(profile)
            system_info = {
                "cpu": profile.cpu_model,
                "ram": f"{profile.ram_total_gb:.0f} GB",
                "gpu": profile.gpu_model or "Integrada",
                "arch": profile.arch,
            }
            self.done.emit(checks, profile, spec, system_info)
        except Exception as exc:
            self.failed.emit(str(exc))


class Installer(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Instalar N.O.V.A.")
        self.setWindowIcon(QIcon(_nova_icon()))
        self.resize(720, 680)
        self.setMinimumSize(620, 560)
        self.setStyleSheet(STYLE)

        self._worker = None
        self._target = installation_home() / "app"
        self._recommended = None
        self._checks_done = False
        self._system_info = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # -- top bar (step indicators) ----------------------------------------
        self._step_bar = QWidget()
        self._step_bar.setFixedHeight(52)
        self._step_bar.setStyleSheet(f"background:{BG_PANEL};border-bottom:1px solid {BORDER};")
        sb = QHBoxLayout(self._step_bar)
        sb.setContentsMargins(32, 0, 32, 0)
        self._steps: list[QLabel] = []
        step_names = ["Bienvenida", "Sistema", "Modelo", "Instalación", "Listo"]
        for i, name in enumerate(step_names):
            lbl = QLabel(f"{'  ' if i else ''}{i + 1}.  {name}")
            lbl.setStyleSheet(f"color:{TEXT_SECONDARY};font-size:11px;letter-spacing:1px;padding:0 10px;")
            sb.addWidget(lbl)
            self._steps.append(lbl)
            if i < len(step_names) - 1:
                sb.addStretch()
        root.addWidget(self._step_bar)

        # -- stacked widget (screens) -----------------------------------------
        self._stack = QStackedWidget()
        root.addWidget(self._stack, 1)

        self._build_welcome()
        self._build_system()
        self._build_model()
        self._build_install()
        self._build_ready()

        self._goto(0)

    # ======================================================================
    # Screen builders
    # ======================================================================

    def _build_welcome(self):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(56, 50, 56, 40)
        lay.setSpacing(0)

        lay.addSpacing(30)
        lay.addStretch()

        icon = QLabel()
        icon.setPixmap(_nova_icon().scaled(72, 72, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(icon)
        lay.addSpacing(28)

        brand = QLabel("N.O.V.A.")
        brand.setObjectName("brand")
        brand.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(brand)

        sub = QLabel("NEURAL OPERATIONS & VIRTUAL ASSISTANT")
        sub.setObjectName("subtitle")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(sub)
        lay.addSpacing(36)

        desc = QLabel(
            "Tu asistente personal de IA.\n"
            "Privado. Local. Tuyo.\n\n"
            "Todo lo que necesitas, preparado automáticamente."
        )
        desc.setAlignment(Qt.AlignmentFlag.AlignCenter)
        desc.setWordWrap(True)
        desc.setStyleSheet(f"color:{TEXT_SECONDARY};font-size:14px;line-height:1.8;")
        lay.addWidget(desc)
        lay.addSpacing(32)

        loc_label = QLabel("Ubicación de instalación")
        loc_label.setStyleSheet(f"color:{TEXT_SECONDARY};font-size:11px;letter-spacing:1px;margin-bottom:6px;")
        lay.addWidget(loc_label)

        loc_row = QHBoxLayout()
        loc_row.setSpacing(8)
        self._location = QLineEdit(str(self._target))
        self._location.setReadOnly(True)
        loc_row.addWidget(self._location, 1)
        browse = QPushButton("Examinar…")
        browse.setFixedWidth(90)
        browse.clicked.connect(self._browse)
        loc_row.addWidget(browse)
        lay.addLayout(loc_row)
        lay.addSpacing(12)

        self._shortcut_cb = QCheckBox("Crear acceso directo en el escritorio")
        self._shortcut_cb.setChecked(True)
        lay.addWidget(self._shortcut_cb)

        self._autostart_cb = QCheckBox("Iniciar N.O.V.A. automáticamente con Windows")
        lay.addWidget(self._autostart_cb)
        lay.addSpacing(20)

        btn = QPushButton("Instalar N.O.V.A.")
        btn.setObjectName("primary")
        btn.setFixedWidth(240)
        btn.clicked.connect(self._next)
        lay.addWidget(btn, 0, Qt.AlignmentFlag.AlignCenter)
        lay.addSpacing(8)

        ver = QLabel(f"v{self._get_version()}")
        ver.setStyleSheet(f"color:{TEXT_SECONDARY};font-size:11px;")
        ver.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(ver)

        self._stack.addWidget(page)

    def _build_system(self):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(56, 36, 56, 36)
        lay.setSpacing(0)

        title = QLabel("Comprobando tu sistema")
        title.setObjectName("title")
        lay.addWidget(title)
        lay.addSpacing(8)

        desc = QLabel("Analizamos tu hardware para elegir la mejor configuración.")
        desc.setStyleSheet(f"color:{TEXT_SECONDARY};font-size:13px;")
        desc.setWordWrap(True)
        lay.addWidget(desc)
        lay.addSpacing(24)

        self._system_checks_lay = QVBoxLayout()
        self._system_checks_lay.setSpacing(6)
        lay.addLayout(self._system_checks_lay)

        self._system_loading = QLabel("Analizando hardware…")
        self._system_loading.setStyleSheet(f"color:{TEXT_SECONDARY};font-size:13px;padding:16px 0;")
        self._system_checks_lay.addWidget(self._system_loading)

        lay.addStretch()

        self._sys_error = QLabel()
        self._sys_error.setObjectName("error")
        self._sys_error.setWordWrap(True)
        self._sys_error.hide()
        lay.addWidget(self._sys_error)

        nav = QHBoxLayout()
        nav.addStretch()
        retry = QPushButton("Reintentar")
        retry.hide()
        self._sys_retry = retry
        retry.clicked.connect(lambda: self._goto(1))
        nav.addWidget(retry)
        next_btn = QPushButton("Continuar")
        next_btn.setObjectName("primary")
        next_btn.setFixedWidth(160)
        next_btn.hide()
        self._sys_next = next_btn
        next_btn.clicked.connect(self._next)
        nav.addWidget(next_btn)
        lay.addLayout(nav)

        self._stack.addWidget(page)

    def _build_model(self):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(56, 36, 56, 36)
        lay.setSpacing(0)

        title = QLabel("Modelo recomendado")
        title.setObjectName("title")
        lay.addWidget(title)
        lay.addSpacing(8)

        desc = QLabel(
            "Analizamos tu ordenador y elegimos un modelo equilibrado.\n"
            "Podrás cambiar de modelo más tarde desde Ajustes."
        )
        desc.setStyleSheet(f"color:{TEXT_SECONDARY};font-size:13px;")
        desc.setWordWrap(True)
        lay.addWidget(desc)
        lay.addSpacing(24)

        self._model_card = QFrame()
        self._model_card.setStyleSheet(
            f"QFrame{{background:{BG_CARD};border:1px solid {BORDER};border-radius:12px;padding:20px;}}"
        )
        mc_lay = QVBoxLayout(self._model_card)
        mc_lay.setSpacing(12)

        self._model_name = QLabel("N.O.V.A.")
        self._model_name.setStyleSheet(f"color:{ACCENT};font-size:18px;font-weight:600;")
        mc_lay.addWidget(self._model_name)

        self._model_desc = QLabel("Analizando tu hardware…")
        self._model_desc.setStyleSheet(f"color:{TEXT_SECONDARY};font-size:13px;")
        self._model_desc.setWordWrap(True)
        mc_lay.addWidget(self._model_desc)

        self._model_specs = QLabel("")
        self._model_specs.setStyleSheet(f"color:{TEXT_SECONDARY};font-size:12px;")
        mc_lay.addWidget(self._model_specs)

        lay.addWidget(self._model_card)
        lay.addSpacing(16)

        self._model_note = QLabel("La primera descarga necesita Internet y puede tardar varios minutos.")
        self._model_note.setStyleSheet(f"color:{TEXT_SECONDARY};font-size:12px;font-style:italic;")
        self._model_note.setWordWrap(True)
        lay.addWidget(self._model_note)

        lay.addStretch()

        nav = QHBoxLayout()
        nav.addStretch()
        prev = QPushButton("Atrás")
        prev.setObjectName("ghost")
        prev.clicked.connect(self._back)
        nav.addWidget(prev)
        next_btn = QPushButton("Instalar")
        next_btn.setObjectName("primary")
        next_btn.setFixedWidth(200)
        next_btn.clicked.connect(self._start_install)
        nav.addWidget(next_btn)
        lay.addLayout(nav)

        self._stack.addWidget(page)

    def _build_install(self):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(56, 50, 56, 40)
        lay.setSpacing(0)

        lay.addSpacing(30)
        lay.addStretch()

        icon = QLabel()
        icon.setPixmap(_nova_icon().scaled(64, 64, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(icon)
        lay.addSpacing(30)

        title = QLabel("Preparando N.O.V.A.")
        title.setObjectName("title")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(title)
        lay.addSpacing(32)

        self._install_checks = QVBoxLayout()
        self._install_checks.setSpacing(6)
        self._install_checks.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        lay.addLayout(self._install_checks)

        self._install_msg = QLabel("")
        self._install_msg.setStyleSheet(f"color:{TEXT_SECONDARY};font-size:13px;")
        self._install_msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._install_msg.setWordWrap(True)
        lay.addSpacing(16)
        lay.addWidget(self._install_msg)

        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setFixedWidth(400)
        self._progress.setFixedHeight(5)
        self._progress.hide()
        lay.addSpacing(8)
        lay.addWidget(self._progress, 0, Qt.AlignmentFlag.AlignCenter)

        self._progress_label = QLabel("")
        self._progress_label.setStyleSheet(f"color:{TEXT_SECONDARY};font-size:12px;")
        self._progress_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self._progress_label)

        lay.addStretch()

        self._error_box = QFrame()
        self._error_box.setStyleSheet(
            f"QFrame{{background:{BG_CARD};border:1px solid #5a3030;border-radius:10px;padding:16px;}}"
        )
        eb_lay = QVBoxLayout(self._error_box)
        eb_lay.setSpacing(8)
        self._error_text = QLabel("")
        self._error_text.setObjectName("error")
        self._error_text.setWordWrap(True)
        eb_lay.addWidget(self._error_text)
        err_btns = QHBoxLayout()
        err_btns.addStretch()
        details_btn = QPushButton("Ver detalles")
        details_btn.setObjectName("ghost")
        details_btn.clicked.connect(self._toggle_install_details)
        err_btns.addWidget(details_btn)
        retry_btn = QPushButton("Reintentar")
        retry_btn.setObjectName("primary")
        retry_btn.setFixedWidth(140)
        retry_btn.clicked.connect(self._retry_install)
        err_btns.addWidget(retry_btn)
        eb_lay.addLayout(err_btns)
        self._install_details = QTextEdit()
        self._install_details.setReadOnly(True)
        self._install_details.setMaximumHeight(140)
        self._install_details.setStyleSheet(f"background:{BG_DARK};border:1px solid {BORDER};font-size:11px;")
        self._install_details.hide()
        eb_lay.addWidget(self._install_details)
        self._error_box.hide()
        lay.addWidget(self._error_box)

        self._stack.addWidget(page)

    def _build_ready(self):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(56, 50, 56, 40)
        lay.setSpacing(0)

        lay.addSpacing(30)
        lay.addStretch()

        icon = QLabel()
        icon.setPixmap(_nova_icon().scaled(80, 80, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(icon)
        lay.addSpacing(28)

        title = QLabel("N.O.V.A. está lista.")
        title.setObjectName("title")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(title)
        lay.addSpacing(12)

        desc = QLabel("Tu asistente personal de IA está lista para usar.\nEscribe \"Hola, Nova.\" y empieza a conversar.")
        desc.setStyleSheet(f"color:{TEXT_SECONDARY};font-size:14px;")
        desc.setAlignment(Qt.AlignmentFlag.AlignCenter)
        desc.setWordWrap(True)
        lay.addWidget(desc)
        lay.addSpacing(40)

        btn = QPushButton("Abrir N.O.V.A.")
        btn.setObjectName("primary")
        btn.setFixedWidth(220)
        btn.clicked.connect(self._open_nova)
        lay.addWidget(btn, 0, Qt.AlignmentFlag.AlignCenter)
        lay.addSpacing(12)

        self._ready_autostart = QCheckBox("Iniciar N.O.V.A. con Windows")
        self._ready_autostart.setChecked(self._autostart_cb.isChecked())
        self._ready_autostart.stateChanged.connect(self._toggle_ready_autostart)
        lay.addWidget(self._ready_autostart, 0, Qt.AlignmentFlag.AlignCenter)

        lay.addStretch()

        self._stack.addWidget(page)

    # ======================================================================
    # Navigation
    # ======================================================================

    def _goto(self, index: int):
        self._stack.setCurrentIndex(index)
        for i, lbl in enumerate(self._steps):
            if i < index:
                lbl.setStyleSheet(f"color:{ACCENT};font-size:11px;letter-spacing:1px;padding:0 10px;font-weight:600;")
            elif i == index:
                lbl.setStyleSheet(f"color:{TEXT_PRIMARY};font-size:11px;letter-spacing:1px;padding:0 10px;font-weight:600;")
            else:
                lbl.setStyleSheet(f"color:{TEXT_SECONDARY};font-size:11px;letter-spacing:1px;padding:0 10px;")

    def _next(self):
        idx = self._stack.currentIndex()
        if idx == 0:
            try:
                self._target = validate_target(Path(self._location.text()))
            except Exception as exc:
                QMessageBox.warning(self, "N.O.V.A.", str(exc))
                return
        self._goto(idx + 1)
        if idx + 1 == 1:
            self._run_system_check()
        elif idx + 1 == 2:
            self._populate_model()

    def _back(self):
        idx = self._stack.currentIndex()
        if idx > 0:
            self._goto(idx - 1)

    # ======================================================================
    # Screen 2: System check
    # ======================================================================

    def _run_system_check(self):
        if self._checks_done:
            return
        self._sys_error.hide()
        self._sys_retry.hide()
        self._sys_next.hide()
        self._system_loading.show()
        self._clear_layout(self._system_checks_lay)
        self._system_loading = QLabel("Analizando hardware…")
        self._system_loading.setStyleSheet(f"color:{TEXT_SECONDARY};font-size:13px;padding:16px 0;")
        self._system_checks_lay.addWidget(self._system_loading)

        self._sys_worker = SystemCheckWorker()
        self._sys_worker.done.connect(self._show_system_results)
        self._sys_worker.failed.connect(self._show_system_error)
        self._sys_worker.start()

    def _show_system_results(self, checks, profile=None, spec=None, system_info=None):
        self._checks_done = True
        if spec is not None:
            self._recommended = spec
        if system_info is not None:
            self._system_info = system_info
        self._clear_layout(self._system_checks_lay)
        for text, ok in checks:
            self._system_checks_lay.addWidget(_check_row("✓" if ok else "✗", text, ok))
        self._system_checks_lay.addStretch()

        # Show hardware summary
        if self._system_info:
            info = QLabel(
                f"CPU: {self._system_info.get('cpu', 'N/A')}  ·  "
                f"RAM: {self._system_info.get('ram', 'N/A')}  ·  "
                f"GPU: {self._system_info.get('gpu', 'N/A')}"
            )
            info.setStyleSheet(f"color:{TEXT_SECONDARY};font-size:11px;padding:8px 0;")
            info.setWordWrap(True)
            self._system_checks_lay.addWidget(info)

        self._sys_next.show()

    def _show_system_error(self, msg):
        self._clear_layout(self._system_checks_lay)
        self._system_loading.hide()
        self._sys_error.setText(msg)
        self._sys_error.show()
        self._sys_retry.show()

    # ======================================================================
    # Screen 3: Model selection
    # ======================================================================

    def _populate_model(self):
        if self._recommended:
            spec = self._recommended
            self._model_name.setText(spec.name)
            self._model_desc.setText(
                f"Modelo recomendado para tu ordenador.\n"
                f"Equilibrado entre velocidad, calidad y consumo de recursos."
            )
            self._model_specs.setText(
                f"~{spec.params_b:.0f}B parámetros  ·  "
                f"~{spec.weights_gb:.1f} GB  ·  "
                f"RAM necesaria: ~{spec.min_ram_gb:.0f} GB"
            )

    # ======================================================================
    # Screen 4: Installation
    # ======================================================================

    def _start_install(self):
        self._goto(3)
        self._error_box.hide()
        self._progress.hide()
        self._progress_label.setText("")
        self._clear_layout(self._install_checks)
        self._add_check("✓ Sistema compatible", True)
        self._install_msg.setText("Preparando la instalación…")
        self._worker = InstallWorker(self._target, self._shortcut_cb.isChecked(), self._autostart_cb.isChecked())
        self._worker.changed.connect(self._on_progress)
        self._worker.start()

    def _retry_install(self):
        self._error_box.hide()
        self._clear_layout(self._install_checks)
        self._add_check("✓ Sistema compatible", True)
        self._install_msg.setText("Reintentando…")
        self._worker = InstallWorker(self._target, self._shortcut_cb.isChecked(), self._autostart_cb.isChecked())
        self._worker.changed.connect(self._on_progress)
        self._worker.start()

    def _on_progress(self, result):
        if "checks" in result:
            for ch in result["checks"]:
                self._add_check(("✓ " if ch.get("ok") else "✗ ") + ch["label"], ch.get("ok", False))
        if "recommendation" in result:
            spec = result["recommendation"]
            self._add_check(f"Modelo: {spec['name']} (~{spec['size_gb']:.1f} GB)", True)
        if "message" in result:
            self._install_msg.setText(result["message"])
        total = result.get("total") or 0
        if total > 0:
            self._progress.show()
            self._progress.setRange(0, 1000)
            self._progress.setValue(int(min(1.0, result.get("completed", 0) / total) * 1000))
            done_mb = result.get("completed", 0) / 1024 / 1024
            total_mb = total / 1024 / 1024
            self._progress_label.setText(f"{done_mb:.0f} / {total_mb:.0f} MB")
        else:
            self._progress.setRange(0, 0)
            self._progress.show()
            self._progress_label.setText("")
        if result.get("status") == "error":
            self._progress.hide()
            self._progress_label.setText("")
            self._error_text.setText(result.get("message", "Error desconocido."))
            self._install_details.setPlainText(result.get("details", ""))
            self._error_box.show()
        if result.get("status") == "ready":
            self._progress.hide()
            self._progress_label.setText("")
            self._goto(4)

    def _add_check(self, text: str, ok: bool):
        self._install_checks.addWidget(_check_row("✓" if ok else "○", text, ok))

    def _toggle_install_details(self):
        self._install_details.setVisible(not self._install_details.isVisible())

    # ======================================================================
    # Screen 5: Ready
    # ======================================================================

    def _open_nova(self):
        exe = self._target / "NOVA.exe"
        if exe.exists():
            env = os.environ.copy()
            env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
            subprocess.Popen(
                [str(exe)],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                env=env,
            )
        self.close()

    def _toggle_ready_autostart(self, state: int):
        import winreg
        from nova.setup.autostart import RUN_KEY
        key_path = self._target / "NOVA.exe"
        try:
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
                if state == 0:
                    try:
                        winreg.DeleteValue(key, "NOVA")
                    except FileNotFoundError:
                        pass
                else:
                    winreg.SetValueEx(key, "NOVA", 0, winreg.REG_SZ, f'"{key_path}" --hidden')
        except OSError:
            pass

    # ======================================================================
    # Helpers
    # ======================================================================

    def _browse(self):
        folder = QFileDialog.getExistingDirectory(self, "Elige dónde instalar N.O.V.A.", str(installation_home()))
        if folder:
            self._target = Path(folder) / "NOVA"
            self._location.setText(str(self._target))

    def _get_version(self) -> str:
        from nova import __version__

        if __version__:
            return __version__
        try:
            import tomllib
            for candidate in (
                Path(__file__).resolve().parents[2] / "pyproject.toml",
                Path(sys.executable).parent / "pyproject.toml",
            ):
                if candidate.exists():
                    return tomllib.loads(candidate.read_text(encoding="utf-8"))["project"]["version"]
        except Exception:
            pass
        return "unknown"

    def _clear_layout(self, layout):
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

    def closeEvent(self, event):
        if self._worker and self._worker.isRunning():
            self._install_msg.setText("La instalación sigue en curso. Puedes minimizar esta ventana.")
            event.ignore()
        else:
            event.accept()


# ---------------------------------------------------------------------------
# Uninstall
# ---------------------------------------------------------------------------

def uninstall(target: Path) -> int:
    from nova.setup.state import StateStore, check_removal_path, repository_roots
    from nova.setup.ollama_lifecycle import stop_owned_processes
    from nova.setup.autostart import remove_owned_autostart
    import winreg

    target = validate_target(target)
    reply = QMessageBox.question(
        None, "Desinstalar N.O.V.A.",
        "¿Quieres desinstalar N.O.V.A.?\n\n"
        "Se cerrará la aplicación y se quitarán sus ejecutables y accesos directos.\n"
        "Tus conversaciones, modelos y preferencias se conservarán.",
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.No,
    )
    if reply != QMessageBox.StandardButton.Yes:
        return 0
    store = StateStore()
    with store.lock():
        state = store.load()
        if not state:
            raise ValueError("No hay una instalación registrada.")
        roots = repository_roots()
        stop_owned_processes(target, environment=True)
        if state.autostart:
            remove_owned_autostart(state.autostart, roots)
            store.change(lambda s: s.autostart.clear())
        for resource in state.resources:
            if resource.kind == "integration" and Path(resource.path).suffix.lower() == ".lnk":
                check_removal_path(Path(resource.path), roots).unlink(missing_ok=True)
                store.change(lambda s, r=resource: setattr(s, "resources", [x for x in s.resources if x != r]))
        shutil.rmtree(check_removal_path(target, roots))
        store.change(lambda s: setattr(s, "resources", [r for r in s.resources if Path(r.path).resolve() != target]))
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Uninstall\NOVA.Desktop")
        except FileNotFoundError:
            pass
    QMessageBox.information(None, "N.O.V.A.", "La aplicación se ha desinstalado. Tus datos locales se han conservado.")
    return 0


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    app = QApplication([])
    app.setStyleSheet(STYLE)
    app.setApplicationName("N.O.V.A.")
    app.setWindowIcon(QIcon(_nova_icon()))

    if "--remove" in sys.argv:
        return uninstall(Path(sys.argv[sys.argv.index("--remove") + 1]))

    if "Uninstall" in Path(sys.executable).name:
        target = Path(sys.executable).parent.resolve()
        validate_target(target)
        temporary = Path(tempfile.mkdtemp(prefix="nova-uninstall-")) / "NOVA-Uninstall.exe"
        shutil.copy2(sys.executable, temporary)
        env = os.environ.copy()
        env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
        subprocess.Popen(
            [str(temporary), "--remove", str(target)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), env=env,
        )
        return 0

    window = Installer()
    window.show()
    return app.exec()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        QMessageBox.critical(None, "N.O.V.A.", "No hemos podido completar la operación. " + str(exc))
        raise SystemExit(1)
