from __future__ import annotations

import logging
import tempfile
import unittest
from pathlib import Path

from app.logging_setup import configure_logging


class LoggingSetupTests(unittest.TestCase):
    def test_errors_are_written_to_the_journal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "logs" / "service.log"
            configure_logging("INFO", str(path))
            logging.getLogger("test.error-journal").error("sample adapter failure")
            for handler in logging.getLogger().handlers:
                handler.flush()
            self.assertIn("sample adapter failure", path.read_text(encoding="utf-8"))

    def test_configured_secrets_are_redacted_in_error_journal(self) -> None:
        secret = "sample-private-token"
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "logs" / "service.log"
            configure_logging("INFO", str(path), secrets=(secret,))
            try:
                raise RuntimeError(f"request failed with {secret}")
            except RuntimeError:
                logging.getLogger("test.error-journal").exception("adapter error")
            for handler in logging.getLogger().handlers:
                handler.flush()
            journal = path.read_text(encoding="utf-8")
            self.assertIn("[REDACTED]", journal)
            self.assertNotIn(secret, journal)


if __name__ == "__main__":
    unittest.main()
