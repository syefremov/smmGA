"""Tests executed only after the Compose stack reports healthy."""

import os

import httpx
import pytest
from mcp import Client


@pytest.mark.integration
def test_api_and_readiness_from_reverse_proxy() -> None:
    base_url = os.getenv("SMM_TEST_BASE_URL", "http://127.0.0.1:8080")

    live = httpx.get(f"{base_url}/health/live", timeout=5)
    ready = httpx.get(f"{base_url}/health/ready", timeout=5)

    assert live.json() == {"status": "ok"}
    assert ready.status_code == 200
    assert ready.json()["state"] == "ready"


@pytest.mark.integration
async def test_mcp_streamable_http_handshake() -> None:
    base_url = os.getenv("SMM_TEST_BASE_URL", "http://127.0.0.1:8080")

    async with Client(f"{base_url}/mcp/") as client:
        tools = await client.list_tools()

    assert "system_status" in {tool.name for tool in tools.tools}
