"""Authorized transactions, durable audit and an idempotent diagnostic job workflow."""

import hashlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID, uuid4

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from smm_gpt.domain.access import AccessDenied, Conflict, Permission, Principal, authorize
from smm_gpt.infrastructure.database import Database
from smm_gpt.infrastructure.models import (
    AuditEvent,
    IdempotencyKey,
    Identity,
    Job,
    Membership,
    OutboxEvent,
    User,
    Workspace,
)


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def audit(
    session: AsyncSession,
    actor: UUID | None,
    workspace: UUID | None,
    request: UUID,
    action: str,
    outcome: str,
    target: UUID | None = None,
) -> None:
    # Deliberately no arbitrary metadata/payload argument: allowlist is empty in phase four.
    session.add(
        AuditEvent(
            actor_id=actor,
            workspace_id=workspace,
            request_id=request,
            action=action,
            outcome=outcome,
            target_id=target,
            details={},
        )
    )


class AccessService:
    def __init__(self, database: Database) -> None:
        self.database = database

    async def identity(self, issuer: str, subject: str, mfa: bool) -> Principal:
        async with self.database.transaction() as session:
            identity = await session.scalar(
                select(Identity)
                .join(User)
                .where(
                    Identity.issuer == issuer,
                    Identity.subject == subject,
                    Identity.active.is_(True),
                    User.active.is_(True),
                )
            )
            if identity is None:
                raise AccessDenied("identity_not_enrolled")
            return Principal(identity.user_id, identity.id, mfa)

    async def record_denial(self, actor: UUID | None, request: UUID, action: str) -> None:
        # Independent transaction survives a rollback. Unknown workspace is not FK-trusted.
        async with self.database.transaction() as session:
            audit(session, actor, None, request, action, "denied")

    @asynccontextmanager
    async def authorized(
        self, principal: Principal, workspace_id: UUID, permission: Permission, request_id: UUID
    ) -> AsyncIterator[AsyncSession]:
        try:
            async with self.database.transaction(principal.user_id, workspace_id) as session:
                role = await session.scalar(
                    select(Membership.role)
                    .join(User)
                    .where(
                        Membership.user_id == principal.user_id,
                        Membership.workspace_id == workspace_id,
                        Membership.active.is_(True),
                        User.active.is_(True),
                    )
                )
                identity = await session.scalar(
                    select(Identity.id).where(
                        Identity.id == principal.identity_id,
                        Identity.user_id == principal.user_id,
                        Identity.active.is_(True),
                    )
                )
                if role is None or identity is None:
                    raise AccessDenied("access_denied")
                authorize(role, permission, mfa=principal.mfa)
                yield session
        except AccessDenied:
            await self.record_denial(principal.user_id, request_id, permission.value)
            raise

    async def workspace(
        self, principal: Principal, workspace_id: UUID, request_id: UUID
    ) -> dict[str, str]:
        async with self.authorized(principal, workspace_id, Permission.READ, request_id) as session:
            workspace = await session.get(Workspace, workspace_id)
            if workspace is None:
                raise AccessDenied("access_denied")
            return {"id": str(workspace.id), "name": workspace.name, "timezone": workspace.timezone}

    async def create_job(
        self, principal: Principal, workspace_id: UUID, key: str, request_id: UUID
    ) -> UUID:
        """Only the non-external diagnostic job exists. Key is scoped by actor and operation."""
        if not 8 <= len(key) <= 128:
            raise Conflict("invalid_idempotency_key")
        async with self.authorized(principal, workspace_id, Permission.RUN_JOB, request_id) as s:
            key_hash = digest(key)
            lock = digest(f"{workspace_id}:{principal.user_id}:diagnostic:{key_hash}")
            await s.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": int(lock[:15], 16)})
            previous = await s.scalar(
                select(IdempotencyKey).where(
                    IdempotencyKey.workspace_id == workspace_id,
                    IdempotencyKey.actor_id == principal.user_id,
                    IdempotencyKey.operation == "diagnostic",
                    IdempotencyKey.key_hash == key_hash,
                )
            )
            if previous is not None:
                if previous.request_hash != digest("diagnostic:v1"):
                    raise Conflict("idempotency_conflict")
                return previous.job_id
            job_id = uuid4()
            s.add(
                Job(
                    id=job_id,
                    workspace_id=workspace_id,
                    actor_id=principal.user_id,
                    kind="diagnostic",
                )
            )
            await s.flush()
            s.add(
                IdempotencyKey(
                    workspace_id=workspace_id,
                    actor_id=principal.user_id,
                    operation="diagnostic",
                    key_hash=key_hash,
                    request_hash=digest("diagnostic:v1"),
                    job_id=job_id,
                )
            )
            s.add(OutboxEvent(workspace_id=workspace_id, job_id=job_id, kind="diagnostic"))
            audit(s, principal.user_id, workspace_id, request_id, "job.create", "allowed", job_id)
            return job_id

    async def run_job(
        self, principal: Principal, workspace_id: UUID, job_id: UUID, request_id: UUID
    ) -> None:
        """Worker rechecks current membership; payload cannot replace the recorded actor."""
        async with self.authorized(principal, workspace_id, Permission.RUN_JOB, request_id) as s:
            job = await s.scalar(
                select(Job)
                .where(
                    Job.id == job_id,
                    Job.workspace_id == workspace_id,
                    Job.actor_id == principal.user_id,
                )
                .with_for_update()
            )
            if job is None or job.kind != "diagnostic":
                raise AccessDenied("access_denied")
            if job.state == "succeeded":
                return
            job.state = "succeeded"
            audit(s, principal.user_id, workspace_id, request_id, "job.execute", "allowed", job.id)
