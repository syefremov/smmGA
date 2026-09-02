"""Pinned-issuer OIDC client and MCP JWT+online revocation validation."""

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx
import jwt

from smm_gpt.core.config import Settings
from smm_gpt.domain.access import AccessDenied


@dataclass(frozen=True)
class VerifiedIdentity:
    issuer: str
    subject: str
    mfa: bool
    expires_at: int
    scopes: frozenset[str] = frozenset()


class OIDCClient:
    def __init__(self, settings: Settings, http: httpx.AsyncClient | None = None) -> None:
        self.settings = settings
        self.http = http or httpx.AsyncClient(timeout=5, follow_redirects=False, trust_env=False)

    async def close(self) -> None:
        await self.http.aclose()

    async def discovery(self, issuer: str) -> dict[str, Any]:
        response = await self.http.get(issuer.rstrip("/") + "/.well-known/openid-configuration")
        response.raise_for_status()
        data: dict[str, Any] = response.json()
        if data.get("issuer") != issuer or "S256" not in data.get(
            "code_challenge_methods_supported", []
        ):
            raise AccessDenied("invalid_provider_metadata")
        for name in (
            "authorization_endpoint",
            "token_endpoint",
            "jwks_uri",
            "introspection_endpoint",
        ):
            endpoint = urlsplit(data.get(name, ""))
            if (
                endpoint.scheme != "https"
                or endpoint.netloc != urlsplit(issuer).netloc
                or endpoint.username
                or endpoint.password
                or endpoint.fragment
            ):
                raise AccessDenied("invalid_provider_metadata")
        return data

    async def decode(self, token: str, issuer: str, audience: str) -> dict[str, Any]:
        if not 1 <= len(token) <= 16384:
            raise AccessDenied("invalid_token")
        metadata = await self.discovery(issuer)
        response = await self.http.get(metadata["jwks_uri"])
        response.raise_for_status()
        header = jwt.get_unverified_header(token)
        keys = [
            key
            for key in response.json()["keys"]
            if key.get("kid") == header.get("kid")
            and key.get("kty") == "RSA"
            and key.get("use", "sig") == "sig"
            and key.get("alg", "RS256") == "RS256"
        ]
        if len(keys) != 1:
            raise AccessDenied("invalid_token")
        # Never follow token-supplied jku/x5u or accept token-selected algorithms.
        return jwt.decode(
            token,
            jwt.PyJWK(keys[0], algorithm="RS256"),
            algorithms=["RS256"],
            issuer=issuer,
            audience=audience,
            options={"require": ["iss", "sub", "aud", "exp", "iat"]},
        )

    @staticmethod
    def verified(claims: dict[str, Any]) -> VerifiedIdentity:
        amr = claims.get("amr", [])
        if not isinstance(amr, list) or not isinstance(claims.get("scope", ""), str):
            raise AccessDenied("invalid_token")
        return VerifiedIdentity(
            claims["iss"],
            claims["sub"],
            "mfa" in amr or ("pwd" in amr and "otp" in amr),
            int(claims["exp"]),
            frozenset(claims.get("scope", "").split()),
        )

    async def exchange(self, code: str, verifier: str, nonce: str) -> VerifiedIdentity:
        cfg = self.settings
        metadata = await self.discovery(cfg.oidc_issuer_url)
        response = await self.http.post(
            metadata["token_endpoint"],
            auth=(cfg.oidc_client_id, cfg.oidc_client_secret.get_secret_value()),
            data={
                "grant_type": "authorization_code",
                "code": code,
                "code_verifier": verifier,
                "redirect_uri": cfg.web_origin + "/api/v1/auth/callback",
            },
        )
        response.raise_for_status()
        claims = await self.decode(
            response.json()["id_token"], cfg.oidc_issuer_url, cfg.oidc_client_id
        )
        if (
            claims.get("nonce") != nonce
            or claims.get("azp", cfg.oidc_client_id) != cfg.oidc_client_id
        ):
            raise AccessDenied("invalid_token")
        return self.verified(claims)

    async def mcp_identity(self, token: str) -> VerifiedIdentity:
        cfg = self.settings
        claims = await self.decode(token, cfg.mcp_issuer_url, cfg.mcp_resource_url)
        if claims.get("client_id", claims.get("azp")) != cfg.mcp_client_id:
            raise AccessDenied("invalid_token")
        metadata = await self.discovery(cfg.mcp_issuer_url)
        response = await self.http.post(
            metadata["introspection_endpoint"],
            auth=(cfg.oidc_client_id, cfg.oidc_client_secret.get_secret_value()),
            data={"token": token, "token_type_hint": "access_token"},
        )
        response.raise_for_status()
        if response.json().get("active") is not True:
            raise AccessDenied("invalid_token")
        identity = self.verified(claims)
        if "smm:access" not in identity.scopes:
            raise AccessDenied("insufficient_scope")
        return identity
