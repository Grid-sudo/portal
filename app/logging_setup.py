from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import traceback


class SecretRedactionFilter(logging.Filter):
    def __init__(self, secrets: tuple[str, ...]):
        super().__init__()
        self.secrets = tuple(value for value in secrets if value)

    def _redact(self, value: str) -> str:
        for secret in self.secrets:
            value = value.replace(secret, "[REDACTED]")
        return value

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = self._redact(record.getMessage())
        record.args = ()
        if record.exc_info:
            formatted = "".join(traceback.format_exception(*record.exc_info))
            record.exc_text = self._redact(formatted)
            record.exc_info = None
        return True


def configure_logging(level: str, log_file: str, secrets: tuple[str, ...] = ()) -> None:
    path = Path(log_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    handlers = [
        logging.StreamHandler(),
        RotatingFileHandler(path, maxBytes=5_000_000, backupCount=3, encoding="utf-8"),
    ]
    redaction_filter = SecretRedactionFilter(secrets)
    for handler in handlers:
        handler.addFilter(redaction_filter)
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
        force=True,
    )
