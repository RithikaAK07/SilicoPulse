"""Test configuration: an isolated temporary data dir, a small benchmark and no network AI calls.

Environment variables must be set before `app` is imported (config reads them at import time).
"""
import os
import tempfile
import time

import pytest

os.environ["SILICOPULSE_DATA_DIR"] = tempfile.mkdtemp(prefix="silicopulse-test-")
os.environ["SILICOPULSE_DEFAULT_RUNS"] = "3000"
os.environ["GEMINI_API_KEY"] = ""  # set (empty) so backend/.env is not used: tests never call Gemini
os.environ["JWT_SECRET"] = "test-secret"

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.store import store  # noqa: E402

CREDENTIALS = {
    "admin": ("admin@sandisk.com", "admin123"),
    "engineer": ("engineer@sandisk.com", "eng123"),
    "executive": ("executive@sandisk.com", "exec123"),
}


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        t0 = time.time()
        while store.warm_status["state"] in ("idle", "warming") and time.time() - t0 < 300:
            time.sleep(0.5)
        yield c


@pytest.fixture(scope="session")
def headers(client):
    out = {}
    for role, (email, pw) in CREDENTIALS.items():
        r = client.post("/api/login", json={"email": email, "password": pw})
        assert r.status_code == 200, r.text
        out[role] = {"Authorization": f"Bearer {r.json()['access_token']}"}
    return out


@pytest.fixture(scope="session")
def h(headers):
    return headers["engineer"]
