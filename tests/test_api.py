from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)  # no "with": skips startup, so no DB tables are created


def test_health():
    assert client.get("/health").json() == {"status": "ok"}


def test_invalid_job_type_is_rejected():
    r = client.post("/jobs", json={"type": "not_a_real_type"})
    assert r.status_code == 422


def test_unknown_job_returns_404():
    r = client.get("/jobs/00000000-0000-0000-0000-000000000000")
    assert r.status_code == 404