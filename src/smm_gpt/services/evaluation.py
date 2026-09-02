"""Bounded local FTS evaluations. No model, worker, network or activation capability."""

from datetime import datetime
from time import perf_counter
from uuid import UUID, uuid4

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from smm_gpt.domain import evaluation as d
from smm_gpt.domain.access import Permission, Principal
from smm_gpt.domain.content import canonical_hash
from smm_gpt.domain.knowledge import SearchRequest
from smm_gpt.domain.operations import OperationError, Page
from smm_gpt.infrastructure.evaluation_models import EvalDataset, EvalReceipt, EvalReview, EvalRun
from smm_gpt.infrastructure.knowledge_models import (
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeIndex,
    KnowledgeVersion,
)
from smm_gpt.infrastructure.models import utcnow
from smm_gpt.services.access import AccessService, audit, digest
from smm_gpt.services.knowledge import brand_exists, document, lock, retrieve
from smm_gpt.services.knowledge_text import safe_text
from smm_gpt.services.retrieval_eval import score


async def corpus(s: AsyncSession, wid: UUID, bid: UUID, at: datetime) -> list[d.CorpusSource]:
    rows = (
        await s.execute(
            select(KnowledgeDocument, KnowledgeIndex, KnowledgeVersion)
            .join(KnowledgeIndex, KnowledgeIndex.id == KnowledgeDocument.active_index_id)
            .join(KnowledgeVersion, KnowledgeVersion.id == KnowledgeIndex.document_version_id)
            .where(
                KnowledgeDocument.workspace_id == wid,
                KnowledgeDocument.brand_id == bid,
                KnowledgeDocument.archived.is_(False),
                KnowledgeIndex.state == "ready",
                KnowledgeVersion.effective_from <= at,
                KnowledgeVersion.effective_to > at,
            )
            .order_by(KnowledgeDocument.id)
            .limit(501)
        )
    ).all()
    if len(rows) > 500:
        raise OperationError("evaluation_corpus_limit", 422)
    return [
        d.CorpusSource(
            document_id=doc.id,
            document_version_id=v.id,
            index_id=i.id,
            content_hash=v.content_hash,
            parser_version=i.parser_version,
            chunking_version=i.chunking_version,
            visibility=doc.visibility,
            effective_from=v.effective_from,
            effective_to=v.effective_to,
        )
        for doc, i, v in rows
    ]


def corpus_hash(sources: list[d.CorpusSource]) -> str:
    return canonical_hash(
        {"algorithm": "ru-simple-v1", "sources": [c.model_dump(mode="json") for c in sources]}
    )


async def dataset(s: AsyncSession, wid: UUID, did: UUID) -> EvalDataset:
    row = await s.scalar(
        select(EvalDataset).where(EvalDataset.workspace_id == wid, EvalDataset.id == did)
    )
    if row is None:
        raise OperationError("not_found", 404)
    return row


async def latest(s: AsyncSession, row: EvalDataset) -> bool:
    newer = await s.scalar(
        select(EvalDataset.id)
        .where(
            EvalDataset.workspace_id == row.workspace_id,
            EvalDataset.family_id == row.family_id,
            EvalDataset.number > row.number,
        )
        .limit(1)
    )
    return newer is None


class EvaluationService:
    def __init__(self, access: AccessService):
        self.access = access

    async def execute(
        self, actor: Principal, wid: UUID, command: d.EvalCommand, request: UUID
    ) -> d.EvalResult:
        async with self.access.authorized(actor, wid, Permission.APPROVE, request) as s:
            # Same serialization boundary as index activation/archive. All case queries use
            # one effective time; the corpus is checked again before committing the report.
            await s.execute(text("SET LOCAL statement_timeout = '5s'"))
            await lock(s, wid)
            fingerprint = canonical_hash(
                command.model_dump(mode="json", exclude={"idempotency_key"})
            )
            previous = await s.scalar(
                select(EvalReceipt).where(
                    EvalReceipt.workspace_id == wid,
                    EvalReceipt.actor_id == actor.user_id,
                    EvalReceipt.key_hash == digest(command.idempotency_key),
                )
            )
            if previous:
                if previous.request_hash != fingerprint:
                    raise OperationError("idempotency_conflict")
                return d.EvalResult.model_validate(previous.result)
            if isinstance(command, d.SubmitEval):
                result = await self._submit(s, actor, wid, command)
            elif isinstance(command, d.RunEval):
                result = await self._run(s, actor, wid, command)
            else:
                result = await self._review(s, actor, wid, command)
            s.add(
                EvalReceipt(
                    workspace_id=wid,
                    actor_id=actor.user_id,
                    key_hash=digest(command.idempotency_key),
                    request_hash=fingerprint,
                    result=result.model_dump(mode="json"),
                )
            )
            audit(
                s,
                actor.user_id,
                wid,
                request,
                "knowledge." + command.action,
                "succeeded",
                result.entity_id,
            )
            return result

    async def _submit(
        self, s: AsyncSession, actor: Principal, wid: UUID, c: d.SubmitEval
    ) -> d.EvalResult:
        await brand_exists(s, wid, c.brand_id)
        safe_text(c.definition.model_dump_json())
        # No arbitrary cross-workspace UUIDs or inaccessible expected-source metadata.
        for cid in {
            x
            for case in c.definition.cases
            for x in case.expected_document_ids + case.forbidden_document_ids
        }:
            source = await document(s, wid, cid)
            if source.brand_id != c.brand_id:
                raise OperationError("not_found", 404)
        family, number = uuid4(), 1
        if c.previous_dataset_id:
            prior = await dataset(s, wid, c.previous_dataset_id)
            if prior.brand_id != c.brand_id or not await latest(s, prior):
                raise OperationError("dataset_conflict")
            family, number = prior.family_id, prior.number + 1
        body = c.definition.model_dump(mode="json")
        row = EvalDataset(
            id=uuid4(),
            workspace_id=wid,
            actor_id=actor.user_id,
            brand_id=c.brand_id,
            previous_dataset_id=c.previous_dataset_id,
            family_id=family,
            number=number,
            definition=body,
            content_hash=canonical_hash(body),
        )
        s.add(row)
        return d.EvalResult(entity_id=row.id, content_hash=row.content_hash)

    async def _run(
        self, s: AsyncSession, actor: Principal, wid: UUID, c: d.RunEval
    ) -> d.EvalResult:
        row = await dataset(s, wid, c.dataset_id)
        if row.content_hash != c.dataset_hash or not await latest(s, row):
            raise OperationError("dataset_conflict")
        definition = d.EvalDefinition.model_validate(row.definition)
        at, started = utcnow(), perf_counter()
        sources = await corpus(s, wid, row.brand_id, at)
        snapshot_hash = corpus_hash(sources)
        cases = []
        for case in definition.cases:
            case_start = perf_counter()
            found = await retrieve(
                s,
                wid,
                SearchRequest(query=case.query, brand_id=row.brand_id, limit=definition.limit),
                at=at,
                workspace_only=case.audience == "workspace",
            )
            elapsed = round((perf_counter() - case_start) * 1000, 3)
            allowed = {
                str(x.document_id)
                for x in sources
                if case.audience == "owner" or x.visibility == "workspace"
            }
            expected = {str(x) for x in case.expected_document_ids}
            returned = {str(x.document_id) for x in found}
            scores = score(expected, [str(x.document_id) for x in found], allowed)
            # Confirm every returned pointer belongs to this exact immutable index/version.
            pointers = {
                (x.document_id, x.document_version_id, x.index_id)
                for x in sources
                if case.audience == "owner" or x.visibility == "workspace"
            }
            chunks = (
                {
                    x.id: x
                    for x in (
                        await s.scalars(
                            select(KnowledgeChunk).where(
                                KnowledgeChunk.workspace_id == wid,
                                KnowledgeChunk.id.in_([c.chunk_id for c in found]),
                            )
                        )
                    ).all()
                }
                if found
                else {}
            )
            valid = sum(
                (x.document_id, x.document_version_id, x.index_id) in pointers
                and x.chunk_id in chunks
                and (
                    chunks[x.chunk_id].document_id,
                    chunks[x.chunk_id].index_id,
                    chunks[x.chunk_id].content_hash,
                )
                == (x.document_id, x.index_id, x.content_hash)
                for x in found
            )
            citation_validity = valid / len(found) if found else 1.0
            forbidden_pass = not ({str(x) for x in case.forbidden_document_ids} & returned)
            passed = (
                scores.precision >= definition.thresholds.precision
                and scores.recall >= definition.thresholds.recall
                and citation_validity == 1
                and scores.negative_pass
                and forbidden_pass
                and elapsed <= definition.thresholds.max_case_ms
            )
            cases.append(
                d.CaseScore(
                    key=case.key,
                    precision=scores.precision,
                    recall=scores.recall,
                    citation_validity=citation_validity,
                    negative_pass=scores.negative_pass,
                    forbidden_pass=forbidden_pass,
                    latency_ms=elapsed,
                    passed=passed,
                    missing_document_ids=sorted(UUID(x) for x in expected - returned),
                    unexpected_document_ids=sorted(UUID(x) for x in returned - expected),
                    hits=[d.EvalHit.model_validate(x.model_dump(), extra="ignore") for x in found],
                )
            )
            if perf_counter() - started > 10:
                raise OperationError("evaluation_time_budget_exceeded", 429)
        if corpus_hash(await corpus(s, wid, row.brand_id, utcnow())) != snapshot_hash:
            raise OperationError("evaluation_corpus_changed")
        report = d.EvalReport(
            passed=all(x.passed for x in cases),
            precision=sum(x.precision for x in cases) / len(cases),
            recall=sum(x.recall for x in cases) / len(cases),
            citation_validity=sum(x.citation_validity for x in cases) / len(cases),
            negative_pass=all(x.negative_pass for x in cases),
            forbidden_pass=all(x.forbidden_pass for x in cases),
            duration_ms=round((perf_counter() - started) * 1000, 3),
            cases=cases,
        )
        report_hash = canonical_hash(
            {
                "dataset_hash": row.content_hash,
                "corpus_hash": snapshot_hash,
                "report": report.model_dump(mode="json"),
            }
        )
        run = EvalRun(
            id=uuid4(),
            workspace_id=wid,
            actor_id=actor.user_id,
            brand_id=row.brand_id,
            dataset_id=row.id,
            dataset_hash=row.content_hash,
            corpus_hash=snapshot_hash,
            corpus=[x.model_dump(mode="json") for x in sources],
            report_hash=report_hash,
            report=report.model_dump(mode="json"),
            created_at=at,
        )
        s.add(run)
        return d.EvalResult(entity_id=run.id, content_hash=report_hash)

    async def _review(
        self, s: AsyncSession, actor: Principal, wid: UUID, c: d.ReviewEval
    ) -> d.EvalResult:
        view = await self._read(s, wid, c.run_id)
        if view.report_hash != c.report_hash or view.decision:
            raise OperationError("evaluation_review_conflict")
        if c.decision == "accept_baseline" and view.acceptance_blockers:
            raise OperationError("evaluation_acceptance_blocked")
        safe_text(c.reason)
        s.add(
            EvalReview(
                workspace_id=wid,
                actor_id=actor.user_id,
                run_id=c.run_id,
                report_hash=c.report_hash,
                decision=c.decision,
                reason=c.reason,
            )
        )
        return d.EvalResult(entity_id=c.run_id, content_hash=c.report_hash)

    async def datasets(
        self,
        actor: Principal,
        wid: UUID,
        request: UUID,
        limit: int = 25,
        cursor: UUID | None = None,
    ) -> Page[d.DatasetView]:
        async with self.access.authorized(actor, wid, Permission.APPROVE, request) as s:
            query = select(EvalDataset).where(EvalDataset.workspace_id == wid)
            if cursor:
                query = query.where(EvalDataset.id > cursor)
            rows = list((await s.scalars(query.order_by(EvalDataset.id).limit(limit + 1))).all())
            return Page(
                items=[d.DatasetView.model_validate(x) for x in rows[:limit]],
                next_cursor=rows[limit - 1].id if len(rows) > limit else None,
            )

    async def read_dataset(
        self, actor: Principal, wid: UUID, did: UUID, request: UUID
    ) -> d.DatasetView:
        async with self.access.authorized(actor, wid, Permission.APPROVE, request) as s:
            return d.DatasetView.model_validate(await dataset(s, wid, did))

    async def read(self, actor: Principal, wid: UUID, rid: UUID, request: UUID) -> d.EvalRunDetail:
        async with self.access.authorized(actor, wid, Permission.APPROVE, request) as s:
            return await self._read(s, wid, rid)

    async def _read(
        self, s: AsyncSession, wid: UUID, rid: UUID, *, snapshots: dict[UUID, str] | None = None
    ) -> d.EvalRunDetail:
        run = await s.scalar(select(EvalRun).where(EvalRun.workspace_id == wid, EvalRun.id == rid))
        if run is None:
            raise OperationError("not_found", 404)
        definition = await dataset(s, wid, run.dataset_id)
        parsed = d.EvalDefinition.model_validate(definition.definition)
        report = d.EvalReport.model_validate(run.report)
        review = await s.scalar(
            select(EvalReview).where(EvalReview.workspace_id == wid, EvalReview.run_id == rid)
        )
        cache = snapshots if snapshots is not None else {}
        if run.brand_id not in cache:
            cache[run.brand_id] = corpus_hash(await corpus(s, wid, run.brand_id, utcnow()))
        stale_reasons = []
        if cache[run.brand_id] != run.corpus_hash:
            stale_reasons.append("corpus_changed")
        if not await latest(s, definition):
            stale_reasons.append("dataset_superseded")
        blockers = d.acceptance_blockers(parsed) + stale_reasons
        if not report.passed:
            blockers.append("quality_thresholds_failed")
        if not run.corpus:
            blockers.append("empty_corpus")
        return d.EvalRunDetail(
            id=run.id,
            actor_id=run.actor_id,
            brand_id=run.brand_id,
            dataset_id=run.dataset_id,
            dataset_hash=run.dataset_hash,
            corpus_hash=run.corpus_hash,
            report_hash=run.report_hash,
            created_at=run.created_at,
            report=report,
            decision=review.decision if review else None,
            stale=bool(stale_reasons),
            stale_reasons=stale_reasons,
            acceptance_blockers=blockers,
            baseline_current=bool(review and review.decision == "accept_baseline" and not blockers),
            definition=parsed,
            corpus=[d.CorpusSource.model_validate(x) for x in run.corpus],
            review_reason=review.reason if review else None,
            reviewed_by=review.actor_id if review else None,
            reviewed_at=review.created_at if review else None,
        )

    async def runs(
        self,
        actor: Principal,
        wid: UUID,
        request: UUID,
        limit: int = 25,
        cursor: UUID | None = None,
        dataset_id: UUID | None = None,
    ) -> Page[d.EvalRunView]:
        async with self.access.authorized(actor, wid, Permission.APPROVE, request) as s:
            query = select(EvalRun.id).where(EvalRun.workspace_id == wid)
            if dataset_id:
                await dataset(s, wid, dataset_id)
                query = query.where(EvalRun.dataset_id == dataset_id)
            if cursor:
                query = query.where(EvalRun.id > cursor)
            ids = list((await s.scalars(query.order_by(EvalRun.id).limit(limit + 1))).all())
            snapshots: dict[UUID, str] = {}
            items = [
                d.EvalRunView.model_validate(
                    (await self._read(s, wid, rid, snapshots=snapshots)).model_dump(),
                    extra="ignore",
                )
                for rid in ids[:limit]
            ]
            return Page(items=items, next_cursor=ids[limit - 1] if len(ids) > limit else None)
