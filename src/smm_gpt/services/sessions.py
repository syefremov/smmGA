"""Opaque, revocable browser sessions; authorization codes never become browser tokens."""

import base64
import hashlib
import secrets
from datetime import timedelta
from urllib.parse import urlencode
from uuid import UUID

from sqlalchemy import delete, select, update

from smm_gpt.core.config import Settings
from smm_gpt.domain.access import AccessDenied, Principal
from smm_gpt.infrastructure.models import Identity, LoginFlow, User, WebSession, utcnow
from smm_gpt.services.access import AccessService, audit, digest
from smm_gpt.services.oidc import OIDCClient


class SessionService:
    def __init__(self, settings: Settings, access: AccessService, oidc: OIDCClient) -> None:
        self.settings, self.access, self.oidc = settings, access, oidc

    async def begin_login(self) -> tuple[str, str]:
        state, browser, verifier, nonce = (secrets.token_urlsafe(32) for _ in range(4))
        metadata = await self.oidc.discovery(self.settings.oidc_issuer_url)
        async with self.access.database.transaction() as s:
            await s.execute(delete(LoginFlow).where(LoginFlow.expires_at < utcnow()))
            s.add(
                LoginFlow(
                    state_hash=digest(state),
                    browser_hash=digest(browser),
                    verifier=verifier,
                    nonce=nonce,
                    expires_at=utcnow() + timedelta(minutes=5),
                )
            )
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(
            b"="
        )
        url = (
            metadata["authorization_endpoint"]
            + "?"
            + urlencode(
                {
                    "response_type": "code",
                    "client_id": self.settings.oidc_client_id,
                    "redirect_uri": self.settings.web_origin + "/api/v1/auth/callback",
                    "scope": "openid",
                    "state": state,
                    "nonce": nonce,
                    "code_challenge": challenge.decode(),
                    "code_challenge_method": "S256",
                }
            )
        )
        return url, browser

    async def finish_login(
        self,
        state: str,
        browser: str,
        code: str,
        request_id: UUID,
        previous: str = "",
    ) -> tuple[str, str]:
        async with self.access.database.transaction() as s:
            flow = await s.scalar(
                select(LoginFlow)
                .where(
                    LoginFlow.state_hash == digest(state),
                    LoginFlow.browser_hash == digest(browser),
                    LoginFlow.expires_at > utcnow(),
                )
                .with_for_update()
            )
            if flow is None:
                raise AccessDenied("invalid_login_state")
            verifier, nonce = flow.verifier, flow.nonce
            await s.delete(flow)
        # Consume the state before I/O, including failed token exchange; no replay on retry.
        verified = await self.oidc.exchange(code, verifier, nonce)
        principal = await self.access.identity(verified.issuer, verified.subject, verified.mfa)
        session_token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        async with self.access.database.transaction() as s:
            if previous:
                await s.execute(
                    update(WebSession)
                    .where(WebSession.token_hash == digest(previous))
                    .values(revoked_at=utcnow())
                )
            s.add(
                WebSession(
                    identity_id=principal.identity_id,
                    token_hash=digest(session_token),
                    csrf_hash=digest(csrf),
                    mfa=principal.mfa,
                    expires_at=utcnow() + timedelta(seconds=self.settings.session_absolute_seconds),
                    last_seen_at=utcnow(),
                )
            )
            audit(s, principal.user_id, None, request_id, "session.login", "allowed")
        return session_token, csrf

    async def authenticate(self, token: str, csrf: str | None = None) -> Principal:
        async with self.access.database.transaction() as s:
            row = (
                await s.execute(
                    select(WebSession, Identity)
                    .join(Identity)
                    .join(User)
                    .where(
                        WebSession.token_hash == digest(token),
                        WebSession.revoked_at.is_(None),
                        WebSession.expires_at > utcnow(),
                        WebSession.last_seen_at
                        > utcnow() - timedelta(seconds=self.settings.session_idle_seconds),
                        Identity.active.is_(True),
                        User.active.is_(True),
                    )
                    .with_for_update(of=WebSession)
                )
            ).first()
            if row is None or (
                csrf is not None and not secrets.compare_digest(row[0].csrf_hash, digest(csrf))
            ):
                raise AccessDenied("invalid_session")
            session, identity = row
            session.last_seen_at = utcnow()
            return Principal(identity.user_id, identity.id, session.mfa)

    async def logout(self, principal: Principal, request_id: UUID) -> None:
        async with self.access.database.transaction() as s:
            # Revoke all local sessions across every linked identity for this person.
            await s.execute(
                update(WebSession)
                .where(
                    WebSession.identity_id.in_(
                        select(Identity.id).where(Identity.user_id == principal.user_id)
                    )
                )
                .values(revoked_at=utcnow())
            )
            audit(s, principal.user_id, None, request_id, "session.revoke_all", "allowed")
