import asyncio
from dataclasses import replace
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import DBAPIError

from smm_gpt.domain import editor_triage as d
from smm_gpt.domain.access import AccessDenied
from smm_gpt.domain.operations import OperationError
from smm_gpt.infrastructure.ai_models import EditorialDecision
from smm_gpt.infrastructure.content_models import ContentDecision, PostRevision
from smm_gpt.infrastructure.models import AuditEvent, Membership
from smm_gpt.services.ai import AIService
from smm_gpt.services.editor_triage import EditorTriageService
from smm_gpt.workers.ai import process

from .conftest import TenantFixture
from .test_ai_queue import Gateway
from .test_content import Pilot
from .test_editor import EditorGateway, change_policy, change_revision, prepare

pytestmark = pytest.mark.integration


async def ready(
    t: TenantFixture,
) -> tuple[EditorTriageService, d.EditorialTriageView, Pilot, AIService]:
    cfg, ai, run_command, p = await prepare(t)
    run = await ai.start(t.owner, t.workspace, run_command, uuid4())
    await process(
        t.worker,
        cfg,
        Gateway(),
        t.workspace,
        run.id,
        t.owner.user_id,
        editorial_gateway=EditorGateway(),
    )
    service = EditorTriageService(t.access)
    view = await service.read(t.owner, t.workspace, run.id, uuid4())
    return service, view, p, ai


def decision(
    view: d.EditorialTriageView, status: d.TriageStatus = "needs_changes"
) -> d.DecideEditorialFinding:
    return d.DecideEditorialFinding(
        idempotency_key=uuid4().hex,
        artifact_id=view.artifact_id,
        artifact_hash=view.artifact_hash,
        revision_id=view.revision_id,
        content_hash=view.content_hash,
        finding_index=0,
        finding_hash=view.findings[0].finding_hash,
        expected_version=view.version,
        status=status,
        reason="Synthetic human decision",
        human_confirmed=True,
    )


async def test_human_triage_idempotent_concurrent_and_no_content_authority(
    tenants: TenantFixture,
) -> None:
    t = tenants
    service, view, p, ai = await ready(t)
    before = await p.post()
    assert view.version == 0 and view.findings[0].status == "open" and not view.recent_history
    cmd = decision(view)
    receipts = await asyncio.gather(
        *[service.decide(t.owner, t.workspace, view.run_id, cmd, uuid4()) for _ in range(3)]
    )
    assert receipts[0] == receipts[1] == receipts[2]
    assert receipts[0].historical_only and receipts[0].decision.sequence == 1
    with pytest.raises(OperationError, match="idempotency_conflict"):
        await service.decide(
            t.owner,
            t.workspace,
            view.run_id,
            cmd.model_copy(update={"reason": "Different"}),
            uuid4(),
        )
    with pytest.raises(OperationError, match="version_conflict"):
        await service.decide(
            t.owner, t.workspace, view.run_id, decision(view, "dismissed"), uuid4()
        )
    current = await service.read(t.owner, t.workspace, view.run_id, uuid4())
    # Two machines cannot silently overwrite a decision, even with independent keys.
    competing = await asyncio.gather(
        *[
            service.decide(
                t.owner, t.workspace, view.run_id, decision(current, "dismissed"), uuid4()
            )
            for _ in range(2)
        ],
        return_exceptions=True,
    )
    assert sum(isinstance(value, d.EditorialDecisionReceipt) for value in competing) == 1
    assert (
        sum(
            isinstance(value, OperationError) and value.code == "editor_triage_version_conflict"
            for value in competing
        )
        == 1
    )
    current = await service.read(t.owner, t.workspace, view.run_id, uuid4())
    await service.decide(t.owner, t.workspace, view.run_id, decision(current, "open"), uuid4())
    current = await service.read(t.owner, t.workspace, view.run_id, uuid4())
    assert current.version == 3 and current.findings[0].status == "open"
    assert [row.status for row in current.recent_history] == ["open", "dismissed", "needs_changes"]
    read = await ai.read(t.owner, t.workspace, view.run_id, uuid4())
    assert read.editorial_triage == current and read.editorial_review
    assert read.state == "needs_review" and await p.post() == before
    async with t.admin.transaction() as s:
        assert await s.scalar(select(func.count()).select_from(PostRevision)) == 1
        assert await s.scalar(select(func.count()).select_from(ContentDecision)) == 0
        assert await s.scalar(select(func.count()).select_from(EditorialDecision)) == 3
        assert (
            await s.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.action == "ai.finding_decided")
            )
            == 3
        )


@pytest.mark.parametrize("change", ["revision", "policy", "profile"])
async def test_triage_stale_context_blocks_new_decisions_preserves_history(
    tenants: TenantFixture, change: str
) -> None:
    t = tenants
    service, view, p, ai = await ready(t)
    cmd = decision(view)
    receipt = await service.decide(t.owner, t.workspace, view.run_id, cmd, uuid4())
    if change == "revision":
        await change_revision(p)
    elif change == "policy":
        await change_policy(p)
    else:
        from smm_gpt.domain.profiles import DisableProfile
        from smm_gpt.services.profiles import ProfileService

        registry = ProfileService(t.access)
        detail = await registry.read(t.owner, t.workspace, "editor", uuid4())
        assert detail.testing
        await registry.execute(
            t.owner,
            t.workspace,
            DisableProfile(
                idempotency_key=uuid4().hex,
                profile="editor",
                expected_revision=detail.revision,
                version_id=detail.testing.id,
                content_hash=detail.testing.content_hash,
                human_confirmed=True,
                reason="Synthetic disable",
            ),
            uuid4(),
        )
    with pytest.raises(OperationError):
        await service.read(t.owner, t.workspace, view.run_id, uuid4())
    with pytest.raises(OperationError):
        await service.decide(
            t.owner, t.workspace, view.run_id, decision(view, "dismissed"), uuid4()
        )
    assert await service.decide(t.owner, t.workspace, view.run_id, cmd, uuid4()) == receipt
    history = await service.history(t.owner, t.workspace, view.run_id, uuid4())
    assert history.historical_only and history.items == [receipt.decision]
    stale = await ai.read(t.owner, t.workspace, view.run_id, uuid4())
    assert stale.editorial_review is None and stale.editorial_triage is None


async def test_triage_exact_bindings_and_unchanged_state(tenants: TenantFixture) -> None:
    t = tenants
    service, view, _, _ = await ready(t)
    for field, value in [
        ("artifact_id", uuid4()),
        ("artifact_hash", "f" * 64),
        ("revision_id", uuid4()),
        ("content_hash", "f" * 64),
        ("finding_index", 19),
        ("finding_hash", "f" * 64),
    ]:
        with pytest.raises(OperationError):
            await service.decide(
                t.owner,
                t.workspace,
                view.run_id,
                decision(view).model_copy(update={field: value}),
                uuid4(),
            )
    with pytest.raises(OperationError, match="state_unchanged"):
        await service.decide(t.owner, t.workspace, view.run_id, decision(view, "open"), uuid4())
    with pytest.raises(OperationError, match="unsafe_or_oversized_text"):
        await service.decide(
            t.owner,
            t.workspace,
            view.run_id,
            decision(view).model_copy(update={"reason": "-----BEGIN PRIVATE KEY-----"}),
            uuid4(),
        )
    assert (await service.read(t.owner, t.workspace, view.run_id, uuid4())).version == 0


async def test_triage_historical_pagination(tenants: TenantFixture) -> None:
    t = tenants
    service, view, _, _ = await ready(t)
    for index in range(28):
        await service.decide(
            t.owner,
            t.workspace,
            view.run_id,
            decision(view, "dismissed" if index % 2 == 0 else "open"),
            uuid4(),
        )
        view = await service.read(t.owner, t.workspace, view.run_id, uuid4())
    assert view.version == 28 and view.next_before == 4 and len(view.recent_history) == 25
    older = await service.history(t.owner, t.workspace, view.run_id, uuid4(), view.next_before)
    assert older.next_before is None
    assert [row.sequence for row in view.recent_history + older.items] == list(range(28, 0, -1))


async def test_triage_roles_tenants_worker_and_immutable_history(tenants: TenantFixture) -> None:
    t = tenants
    service, view, _, _ = await ready(t)
    cmd = decision(view)
    for actor in [replace(t.owner, mfa=False), t.viewer, t.other]:
        with pytest.raises(AccessDenied):
            await service.decide(actor, t.workspace, view.run_id, cmd, uuid4())
        with pytest.raises(AccessDenied):
            await service.history(actor, t.workspace, view.run_id, uuid4())
    async with t.admin.transaction() as s:
        s.add(Membership(user_id=t.other.user_id, workspace_id=t.workspace, role="owner"))
    for actor, wid in [
        (t.other, t.workspace),
        (t.other, t.other_workspace),
    ]:
        with pytest.raises(OperationError, match="not_found"):
            await service.read(actor, wid, view.run_id, uuid4())
        with pytest.raises(OperationError, match="not_found"):
            await service.history(actor, wid, view.run_id, uuid4())
    receipt = await service.decide(t.owner, t.workspace, view.run_id, cmd, uuid4())
    async with t.admin.transaction() as s:
        row = await s.get(EditorialDecision, receipt.decision.id)
        assert row
        forged = {
            column.name: getattr(row, column.name) for column in EditorialDecision.__table__.columns
        }
    for statement in [
        "SELECT * FROM editorial_decisions",
        "INSERT INTO editorial_decisions (id) VALUES (gen_random_uuid())",
    ]:
        with pytest.raises(DBAPIError):
            async with t.worker.transaction(t.owner.user_id, t.workspace) as s:
                await s.execute(text(statement))
    for operation in [
        "UPDATE editorial_decisions SET reason='changed'",
        "DELETE FROM editorial_decisions",
        "TRUNCATE editorial_decisions",
    ]:
        with pytest.raises(DBAPIError):
            async with t.admin.transaction() as s:
                await s.execute(text(operation))
    with pytest.raises(DBAPIError):
        async with t.runtime.transaction(t.owner.user_id, t.workspace) as s:
            s.add(
                EditorialDecision(
                    **{
                        **forged,
                        "id": uuid4(),
                        "key_hash": "a" * 64,
                        "sequence": 2,
                        "artifact_hash": "f" * 64,
                    }
                )
            )
    async with t.admin.transaction() as s:
        await s.execute(
            update(Membership).where(Membership.user_id == t.owner.user_id).values(role="viewer")
        )
    with pytest.raises(AccessDenied):
        await service.decide(t.owner, t.workspace, view.run_id, cmd, uuid4())
    with pytest.raises(AccessDenied):
        await service.history(t.owner, t.workspace, view.run_id, uuid4())
