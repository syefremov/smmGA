"""Every test gets a newly named disposable DB, never the database named in the supplied URL."""

import asyncio
import os
import secrets
from collections.abc import AsyncIterator
from dataclasses import dataclass
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from smm_gpt.cli import provision_logins
from smm_gpt.core.config import get_settings
from smm_gpt.domain.access import Principal
from smm_gpt.infrastructure.database import Database
from smm_gpt.infrastructure.models import Identity, Membership, User, Workspace
from smm_gpt.services.access import AccessService

from ..identity_fakes import auth_settings


@dataclass
class TenantFixture:
    admin: Database
    runtime: Database
    worker: Database
    access: AccessService
    owner: Principal
    viewer: Principal
    other: Principal
    workspace: UUID
    other_workspace: UUID


@pytest.fixture
async def tenants(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[TenantFixture]:
    source = os.environ.get("SMM_TEST_DATABASE_URL")
    if not source:
        pytest.skip("SMM_TEST_DATABASE_URL is required for disposable PostgreSQL tests")
    # Explicit opt-in required; no connection fallback to application/production configuration.
    url = make_url(source)
    name = "smm_phase4_test_" + uuid4().hex
    control = create_async_engine(url, isolation_level="AUTOCOMMIT", hide_parameters=True)
    async with control.connect() as conn:
        await conn.execute(text(f'CREATE DATABASE "{name}"'))
    admin_url = url.set(database=name).render_as_string(hide_password=False)
    monkeypatch.setenv("SMM_DATABASE_URL", admin_url)
    monkeypatch.setenv("SMM_AUTH_ENABLED", "false")
    credential = secrets.token_hex(32)
    monkeypatch.setenv("SMM_APP_PASSWORD", credential)
    monkeypatch.setenv("SMM_WORKER_PASSWORD", credential)
    get_settings.cache_clear()
    admin = Database(admin_url, 5)
    runtime = Database(
        url.set(database=name, username="smm_api_login", password=credential).render_as_string(
            hide_password=False
        ),
        5,
    )
    worker = Database(
        url.set(database=name, username="smm_worker_login", password=credential).render_as_string(
            hide_password=False
        ),
        5,
    )
    try:
        # Baseline -> head -> baseline -> head covers a previous-schema snapshot and reversibility.
        await asyncio.to_thread(command.upgrade, Config("alembic.ini"), "0001_phase_two")
        await asyncio.to_thread(command.upgrade, Config("alembic.ini"), "head")
        await asyncio.to_thread(command.downgrade, Config("alembic.ini"), "0001_phase_two")
        await asyncio.to_thread(command.upgrade, Config("alembic.ini"), "head")
        await provision_logins(admin)
        cfg = auth_settings()
        a, b = uuid4(), uuid4()
        people = [Principal(uuid4(), uuid4(), True) for _ in range(3)]
        async with admin.transaction() as s:
            s.add_all(
                [Workspace(id=a, slug="one", name="One"), Workspace(id=b, slug="two", name="Two")]
            )
            s.add_all([User(id=p.user_id, display_name="Synthetic person") for p in people])
            await s.flush()
            for person, subject, role, workspace in zip(
                people,
                ["owner", "viewer", "other"],
                ["owner", "viewer", "owner"],
                [a, a, b],
                strict=True,
            ):
                s.add(
                    Identity(
                        id=person.identity_id,
                        user_id=person.user_id,
                        issuer=cfg.oidc_issuer_url,
                        subject=subject,
                    )
                )
                s.add(Identity(user_id=person.user_id, issuer=cfg.mcp_issuer_url, subject=subject))
                s.add(Membership(workspace_id=workspace, user_id=person.user_id, role=role))
        yield TenantFixture(
            admin, runtime, worker, AccessService(runtime), people[0], people[1], people[2], a, b
        )
    finally:
        await admin.close()
        await runtime.close()
        await worker.close()
        async with control.connect() as conn:
            # Name is generated above, never user-supplied; only this test's database is dropped.
            await conn.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
        await control.dispose()
        get_settings.cache_clear()
