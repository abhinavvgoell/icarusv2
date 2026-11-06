"""Keep pytest runs away from the developer's persistent demo database/files."""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

_test_data_root: Path | None = None


def pytest_configure(config):
    global _test_data_root
    if "APP_DATABASE_PATH" in os.environ and "APP_UPLOAD_DIR" in os.environ:
        return
    _test_data_root = Path(tempfile.mkdtemp(prefix="icarus-pytest-"))
    os.environ.setdefault("APP_DATABASE_PATH", str(_test_data_root / "prototype.db"))
    os.environ.setdefault("APP_UPLOAD_DIR", str(_test_data_root / "uploads"))


def pytest_unconfigure(config):
    if _test_data_root is not None:
        shutil.rmtree(_test_data_root, ignore_errors=True)
