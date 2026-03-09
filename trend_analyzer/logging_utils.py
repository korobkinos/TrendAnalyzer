from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .storage import APP_DIR


def setup_logging() -> None:
    log_dir = APP_DIR / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "trend_analyzer.log"

    root = logging.getLogger()
    for handler in root.handlers:
        if isinstance(handler, RotatingFileHandler) and Path(getattr(handler, "baseFilename", "")) == log_path:
            return

    file_handler = RotatingFileHandler(
        filename=str(log_path),
        maxBytes=2_000_000,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    )

    root.setLevel(logging.INFO)
    root.addHandler(file_handler)

    old_hook = sys.excepthook

    def _hook(exc_type, exc_value, exc_tb):
        logging.getLogger("trend_analyzer").exception(
            "Unhandled exception",
            exc_info=(exc_type, exc_value, exc_tb),
        )
        old_hook(exc_type, exc_value, exc_tb)

    sys.excepthook = _hook

