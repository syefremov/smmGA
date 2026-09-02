from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import jwt
import pytest
from pydantic import ValidationError

from smm_gpt.api.routes.identity import DiagnosticRequest
from smm_gpt.cli import main
from smm_gpt.domain.access import GRANTS, MFA_ROLES, AccessDenied, Permission, Role, authorize
from smm_gpt.mcp.auth import MCPVerifier

from ..identity_fakes import FakeIssuer, auth_settings


@pytest.mark.parametrize("role", list(Role))
@pytest.mark.parametrize("permission", list(Permission))
def test_exact_role_matrix(role: Role, permission: Permission) -> None:
    if permission in GRANTS[role]:
        authorize(role, permission, mfa=True)
    else:
        with pytest.raises(AccessDenied):
            authorize(role, permission, mfa=True)


@pytest.mark.parametrize("role", list(MFA_ROLES))
def test_privileged_roles_require_mfa_even_for_reads(role: Role) -> None:
    with pytest.raises(AccessDenied):
        authorize(role, Permission.READ, mfa=False)


def test_unrecognized_role_and_payload_escalation_denied() -> None:
    with pytest.raises(AccessDenied):
        authorize("superadmin", Permission.READ, mfa=True)
    with pytest.raises(ValidationError):
        DiagnosticRequest.model_validate({"idempotency_key": "request-one", "role": "owner"})


def test_https_and_exact_resource_required() -> None:
    for values in (
        {"web_origin": "http://smm.example.test"},
        {"mcp_resource_url": "https://other.example.test/mcp/"},
        {"session_idle_seconds": 0},
    ):
        with pytest.raises(ValidationError):
            type(auth_settings()).model_validate({**auth_settings().model_dump(), **values})


@pytest.mark.parametrize(
    "claim,value",
    [
        ("iss", "https://evil.example/"),
        ("aud", "other-service"),
        ("exp", 1),
        ("iat", 99999999999),
        ("scope", "other:scope"),
        ("azp", "other-client"),
        ("sub", None),
        ("amr", "mfa"),
    ],
)
async def test_wrong_claims_rejected(claim: str, value: Any) -> None:
    issuer = FakeIssuer()
    client = issuer.client()
    try:
        with pytest.raises((AccessDenied, jwt.PyJWTError)):
            await client.mcp_identity(issuer.token(**{claim: value}))
    finally:
        await client.close()


async def test_signature_revocation_and_nonce_checked() -> None:
    issuer = FakeIssuer()
    client = issuer.client()
    try:
        assert (await client.mcp_identity(issuer.token())).mfa
        issuer.active = False
        with pytest.raises(AccessDenied):
            await client.mcp_identity(issuer.token())
        with pytest.raises(jwt.PyJWTError):
            await client.mcp_identity(FakeIssuer().token())
        with pytest.raises(AccessDenied):
            await client.exchange("synthetic-code", "v" * 43, "wrong-nonce")
    finally:
        await client.close()


async def test_mcp_denials_are_audited_without_token() -> None:
    issuer = FakeIssuer()
    access = AsyncMock()
    verifier = MCPVerifier(issuer.client(), access)
    try:
        token = issuer.token(aud="wrong")
        assert await verifier.verify_token(token) is None
        access.record_denial.assert_awaited_once()
        assert token not in str(access.mock_calls)
    finally:
        await verifier.oidc.close()


async def test_discovery_never_follows_untrusted_endpoint() -> None:
    issuer = FakeIssuer()
    original = issuer.respond

    def unsafe(request: httpx.Request) -> httpx.Response:
        response = original(request)
        data = response.json()
        data["jwks_uri"] = "https://attacker.test/key"
        return httpx.Response(200, json=data)

    client = issuer.client()
    await client.close()
    client.http = httpx.AsyncClient(transport=httpx.MockTransport(unsafe))
    try:
        with pytest.raises(AccessDenied):
            await client.mcp_identity(issuer.token())
        assert len(issuer.requests) == 1
    finally:
        await client.close()


def test_operator_cli_defaults_to_no_mutation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.argv", ["smm", "disable-user", "--user-id", str(uuid4())])
    assert main() == 0
