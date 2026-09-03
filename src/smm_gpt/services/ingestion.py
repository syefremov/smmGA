"""Personal/Owner queue controls. No originals, parser invocation or activation rights."""

from typing import cast
from uuid import UUID

from sqlalchemy import func, or_, select

from smm_gpt.domain import ingestion as d
from smm_gpt.domain.access import Permission, Principal
from smm_gpt.domain.content import canonical_hash
from smm_gpt.domain.operations import OperationError, Page
from smm_gpt.infrastructure.file_models import KnowledgeFile
from smm_gpt.infrastructure.knowledge_models import (
    KnowledgeIndex,
    KnowledgeJobEvent,
    KnowledgeJobReceipt,
)
from smm_gpt.services.access import AccessService, audit, digest
from smm_gpt.services.ingestion_state import finish
from smm_gpt.services.knowledge import lock


class IngestionService:
    def __init__(self, access: AccessService):
        self.access = access

    async def history(
        self,
        actor: Principal,
        wid: UUID,
        kind: d.JobKind,
        jid: UUID,
        request: UUID,
    ) -> d.IngestionHistory:
        async with self.access.authorized(actor, wid, Permission.KNOWLEDGE, request) as s:
            model = KnowledgeIndex if kind == "index" else KnowledgeFile
            visible = await s.scalar(
                select(model.id).where(
                    model.workspace_id == wid,
                    model.id == jid,
                    or_(model.actor_id == actor.user_id, func.smm_knowledge_owner(wid)),
                )
            )
            if visible is None:
                raise OperationError("not_found", 404)
            field = KnowledgeJobEvent.index_id if kind == "index" else KnowledgeJobEvent.file_id
            rows = list(
                (
                    await s.scalars(
                        select(KnowledgeJobEvent)
                        .where(
                            KnowledgeJobEvent.workspace_id == wid,
                            field == jid,
                        )
                        .order_by(KnowledgeJobEvent.version.desc())
                        .limit(51)
                    )
                ).all()
            )
            return d.IngestionHistory(
                kind=kind,
                job_id=jid,
                events=[d.IngestionEvent.model_validate(row) for row in reversed(rows[:50])],
                truncated=len(rows) > 50,
            )

    async def jobs(
        self,
        actor: Principal,
        wid: UUID,
        kind: d.JobKind,
        request: UUID,
        limit: int = 25,
        cursor: UUID | None = None,
    ) -> Page[d.IngestionJob]:
        async with self.access.authorized(actor, wid, Permission.KNOWLEDGE, request) as s:
            model = KnowledgeIndex if kind == "index" else KnowledgeFile
            query = select(model).where(
                model.workspace_id == wid,
                or_(model.actor_id == actor.user_id, func.smm_knowledge_owner(wid)),
            )
            if cursor:
                query = query.where(model.id > cursor)
            rows = cast(
                list[KnowledgeIndex | KnowledgeFile],
                list((await s.scalars(query.order_by(model.id).limit(limit + 1))).all()),
            )
            return Page(
                items=[
                    d.IngestionJob(
                        id=r.id,
                        kind=kind,
                        actor_id=r.actor_id,
                        state=r.state,
                        version=r.version,
                        attempts=r.attempts,
                        error_code=r.error_code,
                        document_id=r.document_id if isinstance(r, KnowledgeIndex) else None,
                        created_at=r.created_at,
                        started_at=r.started_at,
                        finished_at=r.finished_at,
                    )
                    for r in rows[:limit]
                ],
                next_cursor=rows[limit - 1].id if len(rows) > limit else None,
            )

    async def cancel(
        self,
        actor: Principal,
        wid: UUID,
        c: d.CancelIngestion,
        request: UUID,
    ) -> d.IngestionReceipt:
        # Cancellation remains available with feature flags disabled.
        async with self.access.authorized(actor, wid, Permission.KNOWLEDGE, request) as s:
            await lock(s, wid)
            fingerprint = canonical_hash(c.model_dump(mode="json", exclude={"idempotency_key"}))
            prior = await s.scalar(
                select(KnowledgeJobReceipt).where(
                    KnowledgeJobReceipt.workspace_id == wid,
                    KnowledgeJobReceipt.actor_id == actor.user_id,
                    KnowledgeJobReceipt.key_hash == digest(c.idempotency_key),
                )
            )
            if prior:
                if prior.request_hash != fingerprint:
                    raise OperationError("idempotency_conflict")
                return d.IngestionReceipt.model_validate(prior.result)
            model = KnowledgeIndex if c.kind == "index" else KnowledgeFile
            row = cast(
                KnowledgeIndex | KnowledgeFile | None,
                await s.scalar(
                    select(model)
                    .where(
                        model.workspace_id == wid,
                        model.id == c.job_id,
                        or_(model.actor_id == actor.user_id, func.smm_knowledge_owner(wid)),
                    )
                    .with_for_update()
                ),
            )
            if row is None:
                raise OperationError("not_found", 404)
            if row.version != c.expected_version:
                raise OperationError("ingestion_conflict")
            if row.state not in {"queued", "processing"}:
                raise OperationError("ingestion_cancel_not_allowed")
            finish(row, "cancelled", "cancelled_by_user")
            receipt = d.IngestionReceipt(kind=c.kind, job_id=row.id, version=row.version)
            s.add(
                KnowledgeJobReceipt(
                    workspace_id=wid,
                    actor_id=actor.user_id,
                    index_id=row.id if c.kind == "index" else None,
                    file_id=row.id if c.kind == "file" else None,
                    key_hash=digest(c.idempotency_key),
                    request_hash=fingerprint,
                    result=receipt.model_dump(mode="json"),
                )
            )
            audit(s, actor.user_id, wid, request, "knowledge.job_cancelled", c.kind, row.id)
            return receipt
