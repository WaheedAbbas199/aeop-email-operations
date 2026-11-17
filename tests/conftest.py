import os
import sys
import tempfile
from pathlib import Path

_d = tempfile.mkdtemp(prefix="aeop-test-")
os.environ.update(AEOP_DATA_DIR=_d, WORKER_ENABLED="0", ALLOW_SIGNUP="1", ADMIN_PASSWORD="AdminPass12345", GEMINI_API_KEY="",
                  LLM_PROVIDER="none", AEOP_ENV="development")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.security import limiter


def login(c, email, pw):
    r = c.post("/api/v1/auth/login", json={"email": email, "password": pw})
    assert r.status_code == 200, r.text
    c.headers["X-CSRF-Token"] = c.cookies.get("aeop_csrf")
    return r.json()


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="session")
def admin(client):
    login(client, "admin@aeop.local", "AdminPass12345")
    return client


def new_client():
    return TestClient(app)


@pytest.fixture(autouse=True)
def _reset_limits():
    limiter.reset()
