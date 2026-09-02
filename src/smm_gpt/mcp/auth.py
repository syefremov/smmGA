"""MCP SDK token verifier: signed audience-bound tokens plus live IdP revocation checks."""

import httpx
import jwt
from mcp.server.auth.provider import AccessToken

from smm_gpt.core.request_context import request_id
from smm_gpt.domain.access import AccessDenied
from smm_gpt.services.access import AccessService
from smm_gpt.services.oidc import OIDCClient


class MCPVerifier:
    def __init__(self, oidc: OIDCClient, access: AccessService) -> None:
        self.oidc, self.access = oidc, access

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            identity = await self.oidc.mcp_identity(token)
            await self.access.identity(identity.issuer, identity.subject, identity.mfa)
            return AccessToken(
                token=token,
                client_id=self.oidc.settings.mcp_client_id,
                subject=identity.subject,
                expires_at=identity.expires_at,
                scopes=sorted(identity.scopes),
                resource=self.oidc.settings.mcp_resource_url,
                claims={"iss": identity.issuer, "mfa": identity.mfa},
            )
        except (AccessDenied, httpx.HTTPError, jwt.PyJWTError, KeyError, ValueError, TypeError):
            await self.access.record_denial(None, request_id(), "mcp.authenticate")
            return None
