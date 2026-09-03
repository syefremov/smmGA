import asyncio
from dataclasses import replace
from uuid import uuid4

import pytest
from alembic import command as alembic_command
from alembic.config import Config
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import DBAPIError

from smm_gpt.domain import content as c
from smm_gpt.domain import copy_adoption as d
from smm_gpt.domain.access import AccessDenied
from smm_gpt.domain.ai import Profile
from smm_gpt.domain.copywriter import CopywritingContext
from smm_gpt.domain.operations import OperationError
from smm_gpt.domain.profiles import DisableProfile
from smm_gpt.infrastructure.ai_models import CopyAdoption
from smm_gpt.infrastructure.content_models import ContentDecision, PostRevision, WorkingCopy
from smm_gpt.infrastructure.models import AuditEvent, Membership
from smm_gpt.services.ai import AIService
from smm_gpt.services.copy_adoption import CopyAdoptionService
from smm_gpt.services.model_gateway import CopywritingGatewayResult
from smm_gpt.services.profiles import ProfileService
from smm_gpt.workers.ai import process

from .conftest import TenantFixture
from .test_ai_queue import Gateway
from .test_content import Pilot
from .test_copywriter import CopyGateway, prepare
from .test_editor import change_policy, change_revision

pytestmark = pytest.mark.integration


class BlockedCopyGateway(CopyGateway):
    async def draft(
        self, profile: Profile, context: CopywritingContext
    ) -> CopywritingGatewayResult:
        result = await super().draft(profile, context)
        result.draft.variants[0].text += " forbidden"
        result.draft.knowledge_gaps = ["Synthetic unresolved gap"]
        return result


async def ready(
    t: TenantFixture, *, blocked: bool = False
) -> tuple[CopyAdoptionService, d.CopyAdoptionPreview, Pilot, AIService]:
    cfg, ai, cmd, p = await prepare(t)
    run = await ai.start(t.owner, t.workspace, cmd, uuid4())
    gateway = BlockedCopyGateway() if blocked else CopyGateway()
    assert await process(
        t.worker,
        cfg,
        Gateway(),
        t.workspace,
        run.id,
        t.owner.user_id,
        copywriting_gateway=gateway,
    )
    assert gateway.calls == 1
    service = CopyAdoptionService(t.access)
    return service, await service.preview(t.owner, t.workspace, run.id, uuid4()), p, ai


def command(view: d.CopyAdoptionPreview) -> d.AdoptCopyDraft:
    return d.AdoptCopyDraft(
        idempotency_key=uuid4().hex,
        artifact_id=view.artifact_id,
        artifact_hash=view.artifact_hash,
        preview_hash=view.preview_hash,
        proposed_content_hash=view.proposed_content_hash,
        expected_post_version=view.post_version,
        reason="Synthetic human text and sharing review",
        human_confirmed=True,
        share_with_workspace_confirmed=True,
    )


async def disable(t: TenantFixture) -> None:
    service = ProfileService(t.access)
    head = await service.read(t.owner, t.workspace, "copywriter", uuid4())
    assert head.testing
    await service.execute(
        t.owner,
        t.workspace,
        DisableProfile(
            idempotency_key=uuid4().hex,
            profile="copywriter",
            expected_revision=head.revision,
            version_id=head.testing.id,
            content_hash=head.testing.content_hash,
            human_confirmed=True,
            reason="Synthetic disable",
        ),
        uuid4(),
    )


async def test_adoption_preserves_copies_invalidates_approval_and_package(
    tenants: TenantFixture,
) -> None:
    t = tenants
    service, initial, p, ai = await ready(t)
    before = await p.post()
    assert await service.preview(t.owner, t.workspace, initial.run_id, uuid4()) == initial
    assert await service.read(t.owner, t.workspace, initial.run_id, uuid4()) is None
    assert await p.post() == before
    package = await p.package(await p.approve())
    before = await p.post()
    async with t.admin.transaction() as s:
        s.add(Membership(user_id=t.other.user_id, workspace_id=t.workspace, role="owner"))
    for actor in [t.owner, t.other]:
        await p.run(
            c.SaveWorkingCopy(
                post_id=p.post_id,
                expected_copy_version=0,
                base_version=before.version,
                body=p.body("Unsaved independent work"),
                idempotency_key=uuid4().hex,
            ),
            actor,
        )
    # State-only changes also bind the preview; a fresh preview does not rerun the model.
    with pytest.raises(OperationError, match="preview_changed"):
        await service.adopt(t.owner, t.workspace, initial.run_id, command(initial), uuid4())
    preview = await service.preview(t.owner, t.workspace, initial.run_id, uuid4())
    receipt = await service.adopt(t.owner, t.workspace, preview.run_id, command(preview), uuid4())
    after = await p.post()
    assert after.state == "draft" and after.active_approval_id is None
    assert after.version == before.version + 1 and len(after.revisions) == 2
    assert after.revisions[0].id == receipt.revision_id
    assert after.revisions[0].body == preview.body
    assert after.revisions[0].content_hash == receipt.content_hash == preview.proposed_content_hash
    assert receipt.source_revision_id == before.revisions[0].id and receipt.historical_only
    assert (
        await p.core.read_package(t.owner, t.workspace, package.entity_id, uuid4())
    ).status == "stale"
    assert receipt.preflight.revision_id == receipt.revision_id
    result = await ai.read(t.owner, t.workspace, preview.run_id, uuid4())
    assert result.copy_draft is None and result.copy_adoption == receipt
    assert result.error_code == "artifact_copywriter_stale_or_unavailable"
    assert await service.read(t.owner, t.workspace, preview.run_id, uuid4()) == receipt
    async with t.admin.transaction() as s:
        copies = (await s.scalars(select(WorkingCopy))).all()
        assert len(copies) == 2
        assert all(row.base_version == before.version and row.version == 1 for row in copies)
        assert all(
            c.RevisionBody.model_validate(row.body).variants[0].text == "Unsaved independent work"
            for row in copies
        )
        assert await s.scalar(select(func.count()).select_from(ContentDecision)) == 1
        assert await s.scalar(select(func.count()).select_from(CopyAdoption)) == 1
        assert (
            await s.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.action == "content.copy_adopted")
            )
            == 1
        )


async def test_adoption_concurrency_replay_history_and_atomicity(
    tenants: TenantFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    t = tenants
    service, preview, p, ai = await ready(t)
    await p.approve()
    preview = await service.preview(t.owner, t.workspace, preview.run_id, uuid4())
    cmd = command(preview)
    before = await p.post()
    assert before.active_approval_id is not None

    def fail(*args: object) -> None:
        raise OperationError("synthetic_audit_failure")

    with monkeypatch.context() as patch:
        patch.setattr("smm_gpt.services.copy_adoption.audit", fail)
        with pytest.raises(OperationError, match="synthetic_audit_failure"):
            await service.adopt(t.owner, t.workspace, preview.run_id, cmd, uuid4())
    assert await p.post() == before
    assert await service.read(t.owner, t.workspace, preview.run_id, uuid4()) is None
    receipts = await asyncio.gather(
        *[service.adopt(t.owner, t.workspace, preview.run_id, cmd, uuid4()) for _ in range(3)]
    )
    assert receipts[0] == receipts[1] == receipts[2]
    with pytest.raises(OperationError, match="idempotency_conflict"):
        await service.adopt(
            t.owner,
            t.workspace,
            preview.run_id,
            cmd.model_copy(update={"reason": "Different"}),
            uuid4(),
        )
    with pytest.raises(OperationError, match="already_adopted"):
        await service.adopt(t.owner, t.workspace, preview.run_id, command(preview), uuid4())
    await change_revision(p)
    await disable(t)
    monkeypatch.setattr("smm_gpt.services.content_records.utcnow", lambda: p.source.expires_at)
    assert await service.adopt(t.owner, t.workspace, preview.run_id, cmd, uuid4()) == receipts[0]
    assert await service.read(t.owner, t.workspace, preview.run_id, uuid4()) == receipts[0]
    assert (await ai.read(t.owner, t.workspace, preview.run_id, uuid4())).copy_adoption == receipts[
        0
    ]
    async with t.admin.transaction() as s:
        assert await s.scalar(select(func.count()).select_from(CopyAdoption)) == 1
        assert await s.scalar(select(func.count()).select_from(PostRevision)) == 3


@pytest.mark.parametrize("change", ["revision", "policy", "expiry", "profile"])
async def test_adoption_revalidates_current_context(
    tenants: TenantFixture,
    change: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    t = tenants
    service, preview, p, _ = await ready(t)
    if change == "revision":
        await change_revision(p)
    elif change == "policy":
        await change_policy(p)
    elif change == "profile":
        await disable(t)
    else:
        monkeypatch.setattr("smm_gpt.services.content_records.utcnow", lambda: p.source.expires_at)
    before = await p.post()
    with pytest.raises(OperationError):
        await service.adopt(t.owner, t.workspace, preview.run_id, command(preview), uuid4())
    with pytest.raises(OperationError):
        await service.preview(t.owner, t.workspace, preview.run_id, uuid4())
    assert await p.post() == before
    assert await service.read(t.owner, t.workspace, preview.run_id, uuid4()) is None


async def test_adoption_exact_binding_and_preflight_is_not_approval(tenants: TenantFixture) -> None:
    t = tenants
    service, preview, p, _ = await ready(t, blocked=True)
    for field, value in [
        ("artifact_id", uuid4()),
        ("artifact_hash", "f" * 64),
        ("preview_hash", "f" * 64),
        ("proposed_content_hash", "f" * 64),
        ("expected_post_version", preview.post_version + 1),
        ("human_confirmed", False),
        ("share_with_workspace_confirmed", False),
    ]:
        with pytest.raises(OperationError):
            await service.adopt(
                t.owner,
                t.workspace,
                preview.run_id,
                command(preview).model_copy(update={field: value}),
                uuid4(),
            )
    receipt = await service.adopt(t.owner, t.workspace, preview.run_id, command(preview), uuid4())
    assert not receipt.preflight.passed
    assert {f.code for f in receipt.preflight.findings} >= {"claim_rule_match", "knowledge_gap"}
    post = await p.post()
    assert post.state == "draft" and post.active_approval_id is None
    assert post.revisions[0].body.knowledge_gaps == preview.draft.knowledge_gaps
    assert post.revisions[0].body.fact_ids == preview.body.fact_ids


async def test_adoption_permissions_immutable_private_receipt_and_downgrade(
    tenants: TenantFixture,
) -> None:
    t = tenants
    service, preview, p, _ = await ready(t)
    cmd = command(preview)
    for actor in [t.viewer, t.other, replace(t.owner, mfa=False)]:
        with pytest.raises(AccessDenied):
            await service.preview(actor, t.workspace, preview.run_id, uuid4())
        with pytest.raises(AccessDenied):
            await service.adopt(actor, t.workspace, preview.run_id, cmd, uuid4())
        with pytest.raises(AccessDenied):
            await service.read(actor, t.workspace, preview.run_id, uuid4())
    receipt = await service.adopt(t.owner, t.workspace, preview.run_id, cmd, uuid4())
    # Sharing gives content readers the new text, not the private run/receipt.
    shared = await p.core.read_post(t.viewer, t.workspace, p.post_id, uuid4())
    assert shared.revisions[0].body == preview.body
    async with t.admin.transaction() as s:
        s.add(Membership(user_id=t.other.user_id, workspace_id=t.workspace, role="owner"))
        row = await s.get(CopyAdoption, receipt.id)
        assert row
        original = {col.name: getattr(row, col.name) for col in CopyAdoption.__table__.columns}
    for actor, wid in [(t.other, t.workspace), (t.other, t.other_workspace)]:
        with pytest.raises(OperationError, match="not_found"):
            await service.read(actor, wid, preview.run_id, uuid4())
        with pytest.raises(OperationError, match="not_found"):
            await service.adopt(actor, wid, preview.run_id, cmd, uuid4())
        async with t.runtime.transaction(actor.user_id, wid) as s:
            assert await s.scalar(select(func.count()).select_from(CopyAdoption)) == 0
    for sql in [
        "SELECT * FROM copy_adoptions",
        "INSERT INTO copy_adoptions (id) VALUES (gen_random_uuid())",
    ]:
        with pytest.raises(DBAPIError):
            async with t.worker.transaction(t.owner.user_id, t.workspace) as s:
                await s.execute(text(sql))
    for sql in [
        "UPDATE copy_adoptions SET reason='changed'",
        "DELETE FROM copy_adoptions",
        "TRUNCATE copy_adoptions",
    ]:
        with pytest.raises(DBAPIError):
            async with t.admin.transaction() as s:
                await s.execute(text(sql))
    for field, value in [
        ("artifact_hash", "f" * 64),
        ("input_hash", "f" * 64),
        ("source_content_hash", "f" * 64),
        ("content_hash", "f" * 64),
        ("post_version", receipt.post_version + 1),
    ]:
        with pytest.raises(DBAPIError, match="copy_adoption_binding_invalid"):
            async with t.runtime.transaction(t.owner.user_id, t.workspace) as s:
                s.add(
                    CopyAdoption(**{**original, "id": uuid4(), "key_hash": "b" * 64, field: value})
                )
    with pytest.raises(DBAPIError, match="copy_adoption_history_requires_restore_plan"):
        await asyncio.to_thread(alembic_command.downgrade, Config("alembic.ini"), "0014_copywriter")
    async with t.admin.transaction() as s:
        assert (
            await s.scalar(text("SELECT version_num FROM alembic_version")) == "0015_copy_adoption"
        )
        await s.execute(
            update(Membership).where(Membership.user_id == t.owner.user_id).values(role="viewer")
        )
    with pytest.raises(AccessDenied):
        await service.adopt(t.owner, t.workspace, preview.run_id, cmd, uuid4())
