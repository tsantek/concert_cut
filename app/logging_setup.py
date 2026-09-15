from __future__ import annotations

import logging
import sys


class _FlushHandler(logging.StreamHandler):
    def emit(self, record: logging.LogRecord) -> None:
        super().emit(record)
        self.flush()


def setup_logging(level: int = logging.INFO) -> None:
    """Log to stderr so `python -m app.main` shows fetch/download diagnostics."""
    root = logging.getLogger()
    if root.handlers:
        root.setLevel(level)
        return
    handler = _FlushHandler(sys.stderr)
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%H:%M:%S",
        )
    )
    root.addHandler(handler)
    root.setLevel(level)


def get_logger(name: str = "concert_cut") -> logging.Logger:
    return logging.getLogger(name)
