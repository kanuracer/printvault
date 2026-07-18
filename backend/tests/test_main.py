from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def test_fastapi_application_is_constructed() -> None:
    app = create_app()

    assert app.title == "PrintVault API"


def test_health_endpoint_reports_ready() -> None:
    response = TestClient(create_app()).get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
