import json
import shutil
from pathlib import Path

import httpx
import pytest

from scripts import employee


def test_export_is_plan_by_default_and_never_overwrites(tmp_path: Path) -> None:
    destination = tmp_path / "greenaurum-smm"
    url = "https://smm.example.test/mcp/"
    employee.package(destination, url, "public-client")
    assert not destination.exists()
    employee.package(destination, url, "public-client", apply=True)
    manifest = json.loads((destination / ".codex-plugin/plugin.json").read_text(encoding="utf-8"))
    assert manifest["name"] == "greenaurum-smm"
    config = json.loads((destination / ".mcp.json").read_text())
    assert config == {
        "mcpServers": {"smm": {"type": "http", "url": url, "oauth": {"clientId": "public-client"}}}
    }
    with pytest.raises(ValueError, match="never overwritten"):
        employee.package(destination, url, "other-client", apply=True)


@pytest.mark.parametrize(
    "url",
    [
        "http://smm.test/mcp/",
        "https://user:private@smm.test/mcp/",
        "https://smm.test/mcp/?key=private",
        "https://smm.test/",
        "https://smm.invalid/mcp/",
        "https://smm.test/mcp/#private",
    ],
)
def test_unsafe_endpoint_refused(url: str) -> None:
    with pytest.raises(ValueError):
        employee.endpoint(url)


def test_doctor_checks_challenge_but_does_not_claim_oauth(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    url = "https://smm.example.test/mcp/"

    def reply(request: httpx.Request) -> httpx.Response:
        assert "authorization" not in request.headers
        if request.url.path.startswith("/.well-known/"):
            return httpx.Response(
                200,
                json={
                    "resource": url,
                    "authorization_servers": ["https://id.example.test/application/o/smm/"],
                    "scopes_supported": ["smm:access"],
                },
            )
        return httpx.Response(
            401,
            headers={
                "www-authenticate": 'Bearer resource_metadata="https://smm.example.test/.well-known/oauth-protected-resource/mcp/"'
            },
        )

    monkeypatch.setattr(shutil, "which", lambda name: "codex")
    with httpx.Client(transport=httpx.MockTransport(reply)) as client:
        assert employee.doctor(url, client)
    result = json.loads(capsys.readouterr().out)
    assert result["personal_oauth_verified"] is False


def test_doctor_errors_never_echo_response(capsys: pytest.CaptureFixture[str]) -> None:
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(500, text="never-echo-response"))
    ) as client:
        assert not employee.doctor("https://smm.example.test/mcp/", client)
    assert "never-echo-response" not in capsys.readouterr().out
