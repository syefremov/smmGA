"""Shared REST/MCP application queries and transactional work-item commands."""

from uuid import UUID

from sqlalchemy import select, text

from smm_gpt.domain.access import GRANTS, AccessDenied, Permission, Principal, Role, authorize
from smm_gpt.domain.operations import (
    TRANSITIONS,
    AuditView,
    CatalogKind,
    CatalogView,
    CreateWorkItem,
    OperationError,
    Page,
    SessionView,
    TransitionWorkItem,
    WorkItemView,
    WorkspaceView,
    WorkState,
)
from smm_gpt.infrastructure.models import (
    AuditEvent,
    Brand,
    Identity,
    Product,
    Source,
    User,
    WorkItem,
)
from smm_gpt.services.access import AccessService, audit, digest


class Operations:
    def __init__(self, access: AccessService):
        self.access = access

    async def session(self, actor: Principal) -> SessionView:
        async with self.access.database.transaction(user_id=actor.user_id) as s:
            user = await s.scalar(
                select(User)
                .join(Identity, Identity.user_id == User.id)
                .where(
                    User.id == actor.user_id,
                    User.active.is_(True),
                    Identity.id == actor.identity_id,
                    Identity.active.is_(True),
                )
            )
            if user is None:
                raise AccessDenied("access_denied")
            rows = (await s.execute(text("SELECT * FROM smm_my_workspaces()"))).mappings().all()
            if len(rows) > 100:
                raise OperationError("workspace_limit_exceeded", 422)
            workspaces = []
            for row in rows:
                try:
                    authorize(row["role"], Permission.READ, mfa=actor.mfa)
                except AccessDenied:
                    continue
                workspaces.append(
                    WorkspaceView(
                        id=row["id"],
                        name=row["name"],
                        timezone=row["timezone"],
                        permissions=sorted(GRANTS[Role(row["role"])]),
                    )
                )
            fingerprint = "|".join(w.model_dump_json() for w in workspaces)
            return SessionView(
                user_id=actor.user_id,
                display_name=user.display_name,
                mfa=actor.mfa,
                workspaces=workspaces,
                access_version=digest(fingerprint),
            )

    async def catalog(
        self,
        actor: Principal,
        wid: UUID,
        kind: CatalogKind,
        request: UUID,
        limit: int = 25,
        cursor: UUID | None = None,
    ) -> Page[CatalogView]:
        self.page_size(limit)
        models: dict[CatalogKind, type[Brand] | type[Product] | type[Source]] = {
            CatalogKind.BRANDS: Brand,
            CatalogKind.PRODUCTS: Product,
            CatalogKind.SOURCES: Source,
        }
        model = models[kind]
        async with self.access.authorized(actor, wid, Permission.READ, request) as s:
            query = select(model).where(model.workspace_id == wid)
            if cursor:
                query = query.where(model.id > cursor)
            rows = (await s.scalars(query.order_by(model.id).limit(limit + 1))).all()
            entries = [CatalogView.model_validate(r) for r in rows[:limit]]
            return Page(
                items=entries,
                next_cursor=entries[-1].id if len(rows) > limit else None,
            )

    @staticmethod
    def page_size(limit: int) -> None:
        if not 1 <= limit <= 50:
            raise OperationError("invalid_request", 422)

    async def list_work(
        self,
        actor: Principal,
        wid: UUID,
        request: UUID,
        limit: int = 25,
        cursor: UUID | None = None,
        state: str | None = None,
    ) -> Page[WorkItemView]:
        self.page_size(limit)
        async with self.access.authorized(actor, wid, Permission.READ, request) as s:
            query = select(WorkItem).where(WorkItem.workspace_id == wid)
            if cursor:
                query = query.where(WorkItem.id > cursor)
            if state:
                query = query.where(WorkItem.state == state)
            rows = (await s.scalars(query.order_by(WorkItem.id).limit(limit + 1))).all()
            return Page(
                items=[WorkItemView.model_validate(r) for r in rows[:limit]],
                next_cursor=rows[limit - 1].id if len(rows) > limit else None,
            )

    async def read_work(
        self, actor: Principal, wid: UUID, item_id: UUID, request: UUID
    ) -> WorkItemView:
        async with self.access.authorized(actor, wid, Permission.READ, request) as s:
            row = await s.scalar(
                select(WorkItem).where(WorkItem.workspace_id == wid, WorkItem.id == item_id)
            )
            if row is None:
                raise OperationError("not_found", 404)
            return WorkItemView.model_validate(row)

    async def create_work(
        self, actor: Principal, wid: UUID, command: CreateWorkItem, request: UUID
    ) -> WorkItemView:
        fingerprint = digest(command.model_dump_json(exclude={"idempotency_key"}))
        key = digest(command.idempotency_key)
        async with self.access.authorized(actor, wid, Permission.WORK_ITEM, request) as s:
            await s.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                {"key": f"work:{wid}:{actor.user_id}:{key}"},
            )
            row = await s.scalar(
                select(WorkItem).where(
                    WorkItem.workspace_id == wid,
                    WorkItem.actor_id == actor.user_id,
                    WorkItem.key_hash == key,
                )
            )
            if row is not None:
                if row.request_hash != fingerprint:
                    raise OperationError("idempotency_conflict")
                return WorkItemView.model_validate(row)
            row = WorkItem(
                workspace_id=wid,
                actor_id=actor.user_id,
                title=command.title,
                brief=command.brief,
                key_hash=key,
                request_hash=fingerprint,
            )
            s.add(row)
            await s.flush()
            audit(
                s,
                actor=actor.user_id,
                workspace=wid,
                request=request,
                action="work_item.created",
                outcome="succeeded",
                target=row.id,
            )
            return WorkItemView.model_validate(row)

    async def transition_work(
        self, actor: Principal, wid: UUID, item_id: UUID, command: TransitionWorkItem, request: UUID
    ) -> WorkItemView:
        async with self.access.authorized(actor, wid, Permission.WORK_ITEM, request) as s:
            row = await s.scalar(
                select(WorkItem)
                .where(WorkItem.workspace_id == wid, WorkItem.id == item_id)
                .with_for_update()
            )
            if row is None:
                raise OperationError("not_found", 404)
            if row.version != command.expected_version:
                raise OperationError("version_conflict")
            if command.state not in TRANSITIONS[WorkState(row.state)]:
                raise OperationError("invalid_transition")
            row.state = command.state
            row.version += 1
            audit(
                s,
                actor=actor.user_id,
                workspace=wid,
                request=request,
                action="work_item.transitioned",
                outcome="succeeded",
                target=row.id,
            )
            return WorkItemView.model_validate(row)

    async def audit_log(
        self,
        actor: Principal,
        wid: UUID,
        request: UUID,
        limit: int = 25,
        cursor: UUID | None = None,
        target: UUID | None = None,
    ) -> Page[AuditView]:
        self.page_size(limit)
        async with self.access.authorized(actor, wid, Permission.AUDIT, request) as s:
            query = select(AuditEvent).where(AuditEvent.workspace_id == wid)
            if cursor:
                query = query.where(AuditEvent.id > cursor)
            if target:
                query = query.where(AuditEvent.target_id == target)
            rows = (await s.scalars(query.order_by(AuditEvent.id).limit(limit + 1))).all()
            # Never expose arbitrary audit details or identity claims to either client.
            return Page(
                items=[AuditView.model_validate(r) for r in rows[:limit]],
                next_cursor=rows[limit - 1].id if len(rows) > limit else None,
            )
