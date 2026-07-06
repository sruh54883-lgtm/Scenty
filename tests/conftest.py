"""Shared pytest fixtures. Adds api/ to the import path and boots the app once."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "api"))

# Safe local defaults (CI sets the real values via job env)
os.environ.setdefault("UPLOADS_DIR", str(ROOT / ".test_uploads"))

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="session")
def client():
    import main  # imported after sys.path/env are set
    with TestClient(main.app) as c:   # context manager runs startup/shutdown (DB pool)
        yield c
