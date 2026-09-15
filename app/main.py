from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from app.logging_setup import get_logger, setup_logging
from app.ui.main_window import MainWindow
from app.ui.theme import apply_theme


def main() -> int:
    setup_logging()
    log = get_logger("track_cut")
    log.info("Starting Track Cut")
    app = QApplication(sys.argv)
    app.setApplicationName("Track Cut")
    app.setApplicationDisplayName("Track Cut")
    apply_theme(app)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
