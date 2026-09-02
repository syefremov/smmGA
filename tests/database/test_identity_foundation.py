import asyncio
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import httpx
import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import DBAPIError, IntegrityError

from smm_gpt.application import create_app
from smm_gpt.cli import bootstrap_owner
from smm_gpt.domain.access import AccessDenied
from smm_gpt.infrastructure.database import Base
from smm_gpt.infrastructure.models import (
    AuditEvent,
    FileMetadata,
    Job,
    Membership,
    OutboxEvent,
    WebSession,
    utcnow,
)
from smm_gpt.services.access import AccessService
from smm_gpt.services.sessions import SessionService

from ..identity_fakes import FakeIssuer
from .conftest import TenantFixture

pytestmark = pytest.mark.integration


async def test_schema_rls_composite_fk_and_append_only_audit(tenants: TenantFixture) -> None:
    t = tenants
    await t.runtime.require_restricted_role()
    await t.worker.require_restricted_role()
    with pytest.raises(RuntimeError):
        await t.admin.require_restricted_role()
    async with t.admin.transaction() as s:
        conn = await s.connection()
        changes = await conn.run_sync(
            lambda c: compare_metadata(MigrationContext.configure(c), Base.metadata)
        )
        assert changes == []
    job = await t.access.create_job(t.owner, t.workspace, "first-request", uuid4())
    async with t.runtime.transaction(t.other.user_id, t.other_workspace) as s:
        assert await s.get(Job, job) is None
        with pytest.raises(IntegrityError):
            s.add(
                FileMetadata(
                    workspace_id=t.other_workspace,
                    job_id=job,
                    storage_key="untrusted",
                    content_type="text/plain",
                    sha256="0" * 64,
                    size_bytes=1,
                )
            )
            await s.flush()
    async with t.runtime.transaction() as s:
        assert await s.scalar(select(func.count()).select_from(Job)) == 0
    # A forged workspace alone cannot satisfy the database membership check.
    async with t.runtime.transaction(t.other.user_id, t.workspace) as s:
        assert await s.get(Job, job) is None
    for db in (t.runtime, t.admin):
        for sql in (
            "UPDATE audit_events SET outcome='tampered'",
            "DELETE FROM audit_events",
            "TRUNCATE audit_events",
        ):
            with pytest.raises(DBAPIError):
                async with db.transaction(t.owner.user_id, t.workspace) as s:
                    await s.execute(text(sql))
    with pytest.raises(DBAPIError):
        async with t.worker.transaction() as s:
            await s.execute(text("SELECT * FROM web_sessions"))
    with pytest.raises(DBAPIError):
        async with t.runtime.transaction(t.viewer.user_id, t.workspace) as s:
            await s.execute(update(Membership).values(role="owner"))


async def test_atomic_idempotency_worker_rechecks_and_denied_audit(tenants: TenantFixture) -> None:
    t = tenants
    jobs = await asyncio.gather(
        *(t.access.create_job(t.owner, t.workspace, "retry-request", uuid4()) for _ in range(3))
    )
    assert len(set(jobs)) == 1
    async with t.admin.transaction() as s:
        assert await s.scalar(select(func.count()).select_from(Job)) == 1
        assert await s.scalar(select(func.count()).select_from(OutboxEvent)) == 1
    for person, workspace in ((t.viewer, t.workspace), (t.other, t.workspace)):
        with pytest.raises(AccessDenied):
            await t.access.create_job(person, workspace, "forbidden-request", uuid4())
    worker = AccessService(t.worker)
    with pytest.raises(AccessDenied):
        await worker.run_job(t.other, t.other_workspace, jobs[0], uuid4())
    await worker.run_job(t.owner, t.workspace, jobs[0], uuid4())
    await worker.run_job(t.owner, t.workspace, jobs[0], uuid4())
    async with t.admin.transaction() as s:
        await s.execute(
            update(Membership).where(Membership.user_id == t.owner.user_id).values(active=False)
        )
    with pytest.raises(AccessDenied):
        await worker.run_job(t.owner, t.workspace, jobs[0], uuid4())
    async with t.admin.transaction() as s:
        audits = list((await s.scalars(select(AuditEvent))).all())
        assert sum(a.action == "job.execute" and a.outcome == "allowed" for a in audits) == 1
        assert sum(a.outcome == "denied" for a in audits) == 4
        assert all(a.details == {} for a in audits)


async def test_bootstrap_is_one_time_and_transactions_rollback(tenants: TenantFixture) -> None:
    cfg = FakeIssuer().settings
    user, workspace = await bootstrap_owner(
        tenants.admin, cfg.oidc_issuer_url, "new-owner", cfg.mcp_issuer_url, "new-owner"
    )
    assert user and workspace
    with pytest.raises(ValueError, match="already bootstrapped"):
        await bootstrap_owner(
            tenants.admin, cfg.oidc_issuer_url, "replacement", cfg.mcp_issuer_url, "replacement"
        )
    with pytest.raises(RuntimeError):
        async with tenants.runtime.transaction(tenants.owner.user_id, tenants.workspace) as s:
            s.add(
                Job(
                    workspace_id=tenants.workspace,
                    actor_id=tenants.owner.user_id,
                    kind="diagnostic",
                )
            )
            await s.flush()
            raise RuntimeError("rollback")
    async with tenants.admin.transaction() as s:
        assert await s.scalar(select(func.count()).select_from(Job)) == 0


async def test_browser_pkce_sessions_csrf_origin_rotation_and_revocation(
    tenants: TenantFixture,
) -> None:
    issuer = FakeIssuer()
    sessions = SessionService(issuer.settings, tenants.access, issuer.client())
    app = create_app(settings=issuer.settings, sessions=sessions)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url=issuer.settings.web_origin
    ) as client:
        response = await client.get("/api/v1/auth/login")
        params = parse_qs(urlsplit(response.headers["location"]).query)
        assert params["code_challenge_method"] == ["S256"]
        assert "HttpOnly" in response.headers["set-cookie"]
        assert "Secure" in response.headers["set-cookie"]
        issuer.nonce = params["nonce"][0]
        callback = "/api/v1/auth/callback?code=synthetic&state=" + params["state"][0]
        assert (await client.get(callback)).status_code == 303
        old = client.cookies.get("__Host-smm-session")
        assert old
        assert (await client.get(callback)).status_code == 401
        assert (await client.get("/api/v1/auth/session")).json()["user_id"] == str(
            tenants.owner.user_id
        )
        assert (
            await client.get(f"/api/v1/workspaces/{tenants.other_workspace}")
        ).status_code == 403
        path = f"/api/v1/workspaces/{tenants.workspace}/diagnostic-jobs"
        payload = {"idempotency_key": "api-request"}
        assert (await client.post(path, json=payload)).status_code == 403
        headers = {"origin": "https://evil.test", "x-csrf-token": client.cookies["__Host-smm-csrf"]}
        assert (await client.post(path, json=payload, headers=headers)).status_code == 403
        headers["origin"] = issuer.settings.web_origin
        invalid = await client.post(
            path, json={**payload, "role": "owner", "password": "never-echo"}, headers=headers
        )
        assert invalid.status_code == 422
        assert "never-echo" not in invalid.text
        assert (await client.post(path, json=payload, headers=headers)).status_code == 201
        # Another successful login rotates and invalidates the previous identifier.
        login = await client.get("/api/v1/auth/login")
        params = parse_qs(urlsplit(login.headers["location"]).query)
        issuer.nonce = params["nonce"][0]
        assert (
            await client.get(
                "/api/v1/auth/callback", params={"code": "next", "state": params["state"][0]}
            )
        ).status_code == 303
        assert client.cookies["__Host-smm-session"] != old
        with pytest.raises(AccessDenied):
            await sessions.authenticate(old)
        headers["x-csrf-token"] = client.cookies["__Host-smm-csrf"]
        assert (await client.post("/api/v1/auth/logout", headers=headers)).status_code == 204
        assert (await client.get("/api/v1/auth/session")).status_code == 401
    await sessions.oidc.close()


async def test_expired_session_and_browser_state_binding(tenants: TenantFixture) -> None:
    issuer = FakeIssuer()
    sessions = SessionService(issuer.settings, tenants.access, issuer.client())
    url, browser = await sessions.begin_login()
    params = parse_qs(urlsplit(url).query)
    issuer.nonce = params["nonce"][0]
    with pytest.raises(AccessDenied):
        await sessions.finish_login(params["state"][0], "different-browser", "code", uuid4())
    token, _ = await sessions.finish_login(params["state"][0], browser, "code", uuid4())
    async with tenants.admin.transaction() as s:
        await s.execute(update(WebSession).values(last_seen_at=utcnow() - timedelta(hours=2)))
    with pytest.raises(AccessDenied):
        await sessions.authenticate(token)
    await sessions.oidc.close()


async def test_mcp_http_auth_metadata_and_cross_workspace(tenants: TenantFixture) -> None:
    issuer = FakeIssuer()
    sessions = SessionService(issuer.settings, tenants.access, issuer.client())
    app = create_app(settings=issuer.settings, sessions=sessions)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app),
            base_url=issuer.settings.web_origin,
            headers={"Accept": "application/json, text/event-stream"},
        ) as client,
    ):
        unauth = await client.post("/mcp/", json={})
        assert unauth.status_code == 401
        assert (
            'resource_metadata="https://smm.example.test/.well-known/oauth-protected-resource/mcp/"'
            in unauth.headers["www-authenticate"]
        )
        metadata = await client.get("/.well-known/oauth-protected-resource/mcp/")
        assert metadata.json()["resource"] == issuer.settings.mcp_resource_url
        token = issuer.token()
        client.headers["Authorization"] = "Bearer " + token

        async def call(workspace: str) -> httpx.Response:
            return await client.post(
                "/mcp/",
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": "workspace_read", "arguments": {"workspace_id": workspace}},
                },
            )

        allowed = await call(str(tenants.workspace))
        assert allowed.status_code == 200
        assert allowed.json()["result"]["structuredContent"]["name"] == "One"
        denied = await call(str(tenants.other_workspace))
        assert denied.json()["result"]["isError"] is True
        issuer.active = False
        assert (await call(str(tenants.workspace))).status_code == 401
