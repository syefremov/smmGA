"""Synthetic issuer with real ephemeral RSA signatures; no real identity-provider calls."""

import json
import time
from typing import Any
from urllib.parse import parse_qs

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from pydantic import SecretStr

from smm_gpt.core.config import Settings
from smm_gpt.services.oidc import OIDCClient


def auth_settings() -> Settings:
    return Settings(
        _env_file=None,
        env="test",
        auth_enabled=True,
        web_origin="https://smm.example.test",
        oidc_issuer_url="https://id.example.test/application/o/web/",
        oidc_client_id="smm-web",
        oidc_client_secret=SecretStr("synthetic-test-only"),
        mcp_issuer_url="https://id.example.test/application/o/mcp/",
        mcp_client_id="smm-mcp",
        mcp_resource_url="https://smm.example.test/mcp/",
    )


class FakeIssuer:
    def __init__(self) -> None:
        self.settings = auth_settings()
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self.key.public_key()))
        self.jwk.update(kid="test-rsa", use="sig", alg="RS256")
        self.active = True
        self.nonce = ""
        self.subject = "owner"
        self.requests: list[httpx.Request] = []

    def token(self, *, web: bool = False, **overrides: Any) -> str:
        claims = {
            "iss": self.settings.oidc_issuer_url if web else self.settings.mcp_issuer_url,
            "aud": self.settings.oidc_client_id if web else self.settings.mcp_resource_url,
            "sub": self.subject,
            "exp": int(time.time()) + 300,
            "iat": int(time.time()),
            "amr": ["pwd", "mfa"],
            "nonce": self.nonce,
            "azp": self.settings.oidc_client_id if web else self.settings.mcp_client_id,
            "scope": "smm:access",
        }
        claims.update(overrides)
        return jwt.encode(claims, self.key, algorithm="RS256", headers={"kid": "test-rsa"})

    def respond(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path.endswith("openid-configuration"):
            issuer = str(request.url).removesuffix(".well-known/openid-configuration")
            return httpx.Response(
                200,
                json={
                    "issuer": issuer,
                    "authorization_endpoint": "https://id.example.test/authorize",
                    "token_endpoint": "https://id.example.test/token",
                    "introspection_endpoint": "https://id.example.test/introspect",
                    "jwks_uri": "https://id.example.test/jwks",
                    "code_challenge_methods_supported": ["S256"],
                },
            )
        if path == "/jwks":
            return httpx.Response(200, json={"keys": [self.jwk]})
        if path == "/introspect":
            return httpx.Response(200, json={"active": self.active})
        if path == "/token":
            body = parse_qs(request.content.decode())
            assert body["grant_type"] == ["authorization_code"]
            assert len(body["code_verifier"][0]) >= 43
            assert body["redirect_uri"] == [self.settings.web_origin + "/api/v1/auth/callback"]
            return httpx.Response(200, json={"id_token": self.token(web=True)})
        raise AssertionError("Unexpected synthetic IdP endpoint")

    def client(self) -> OIDCClient:
        return OIDCClient(
            self.settings, httpx.AsyncClient(transport=httpx.MockTransport(self.respond))
        )
