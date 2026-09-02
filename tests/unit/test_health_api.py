from fastapi.testclient import TestClient

from smm_gpt.api.dependencies import get_system_status_service
from smm_gpt.application import create_app
from smm_gpt.core.config import Settings
from smm_gpt.integrations.base import ConnectorRegistry
from smm_gpt.integrations.fake import FakeSocialConnector
from smm_gpt.services.system_status import SystemStatusService
from ..fakes import FakeProbe


def make_service(*, database: bool = True, redis: bool = True) -> SystemStatusService:
    return SystemStatusService(
        Settings(env="test"),
        (FakeProbe("postgresql", database), FakeProbe("redis", redis)),
        ConnectorRegistry((FakeSocialConnector(),)),
    )


def test_liveness_does_not_require_infrastructure() -> None:
    app = create_app(make_service(database=False, redis=False))

    with TestClient(app) as client:
        response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readiness_requires_postgresql_and_redis() -> None:
    app = create_app(make_service(redis=False))

    with TestClient(app) as client:
        response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json()["state"] == "degraded"
    assert response.json()["dependencies"][1]["state"] == "unavailable"


def test_versioned_status_uses_same_service_dependency() -> None:
    service = make_service()
    app = create_app(service)
    app.dependency_overrides[get_system_status_service] = lambda: service

    with TestClient(app) as client:
        response = client.get("/api/v1/system/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["state"] == "ready"
    assert payload["connectors"] == [
        {
            "name": "fake-social",
            "state": "ready",
            "can_publish": False,
            "mode": "fake-read-only",
        }
    ]
