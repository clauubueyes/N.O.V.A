from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--core', action='store_true')
    parser.add_argument('--hidden', action='store_true')
    parser.add_argument('--prepare-report')
    parser.add_argument('--smoke', action='store_true')
    args = parser.parse_args()
    for stream in ('stdout', 'stderr'):
        if getattr(sys, stream) is None:
            setattr(sys, stream, open(os.devnull, 'w', encoding='utf-8'))
    if args.smoke:
        from nova.api.app import _resolve_static_dir
        from PySide6.QtWebEngineWidgets import QWebEngineView
        from nova import __version__
        if not (_resolve_static_dir() / 'app.js').is_file():
            return 2
        return 0
    if args.core:
        from nova.desktop.runtime import serve
        return serve()
    if args.prepare_report:
        from nova.desktop.configuration import initialize
        from nova.setup.desktop import Preparation
        import time
        preparation = Preparation(initialize())
        preparation.start()
        report = Path(args.prepare_report)
        while True:
            result = preparation.status()
            temporary = report.with_suffix('.tmp')
            temporary.write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')
            temporary.replace(report)
            if result['status'] in ('ready', 'error'):
                return 0 if result['status'] == 'ready' else 1
            time.sleep(0.25)
    from nova.desktop.window import run_window
    return run_window(hidden=args.hidden)


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception:
        from nova.core.paths import installation_home
        import traceback
        log = installation_home() / 'logs' / 'startup-error.log'
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(traceback.format_exc(), encoding='utf-8')
        if '--core' not in sys.argv and '--prepare-report' not in sys.argv:
            import ctypes
            if os.name == 'nt':
                ctypes.windll.user32.MessageBoxW(None, 'N.O.V.A. no ha podido iniciar. Vuelve a abrir la aplicación. Los detalles se han guardado en Diagnóstico.', 'N.O.V.A.', 0x10)
        raise SystemExit(1)
