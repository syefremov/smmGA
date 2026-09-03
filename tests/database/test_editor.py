import asyncio
from dataclasses import replace
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from smm_gpt.core.config import Settings
from smm_gpt.domain import content as c
from smm_gpt.domain.access import AccessDenied
from smm_gpt.domain.ai import CancelAssessment, Profile, RunAssessment
from smm_gpt.domain.editor import EditorContext, RunEditorialReview
from smm_gpt.domain.operations import OperationError
from smm_gpt.infrastructure.ai_models import AIArtifact, AIInput
from smm_gpt.infrastructure.content_models import ContentDecision, PostRevision
from smm_gpt.services.ai import AIService
from smm_gpt.services.model_gateway import EditorialGatewayResult
from smm_gpt.workers.ai import process

from ..editor_fixtures import review_fixture
from .conftest import TenantFixture
from .profile_fixtures import select_profile
from .test_ai_queue import Gateway, config
from .test_content import Pilot, pilot

pytestmark = pytest.mark.integration


class EditorGateway:
    def __init__(self, pause: bool = False, invalid: bool = False):
        self.calls = 0
        self.invalid = invalid
        self.entered, self.release = asyncio.Event(), asyncio.Event()
        if not pause:
            self.release.set()

    async def review(self, profile: Profile, context: EditorContext) -> EditorialGatewayResult:
        self.calls += 1
        assert profile.name == "editor" and profile.output_schema == "EditorialReview"
        self.entered.set()
        await asyncio.wait_for(self.release.wait(), 10)
        result = review_fixture(context)
        if self.invalid:
            result.context_hash = "f" * 64
        return EditorialGatewayResult(
            review=result,
            model="synthetic-model",
            response_id="synthetic-response",
            input_tokens=20,
            output_tokens=10,
        )


async def prepare(t: TenantFixture) -> tuple[Settings, AIService, RunEditorialReview, Pilot]:
    p = await pilot(t)
    selected = await select_profile(t, "editor", "Synthetic text-only review")
    revision = (await p.post()).revisions[0]
    settings = config(t.workspace)
    command = RunEditorialReview(
        idempotency_key=uuid4().hex,
        brand_id=p.brand,
        post_id=p.post_id,
        revision_id=revision.id,
        content_hash=revision.content_hash,
        profile_version_id=selected.version_id,
        profile_selection_id=selected.decision_id,
        testing_only=True,
    )
    return settings, AIService(t.access, settings), command, p


async def change_revision(p: Pilot) -> None:
    post = await p.post()
    await p.run(
        c.SaveRevision(
            post_id=p.post_id,
            expected_version=post.version,
            body=p.body("Changed text"),
            idempotency_key=uuid4().hex,
        )
    )


async def change_policy(p: Pilot) -> None:
    created = await p.run(
        c.CreateRecord(
            body=p.policy.body,
            replaces_id=p.policy.id,
            expires_at=p.policy.expires_at,
            idempotency_key=uuid4().hex,
        )
    )
    version = await p.core.read_record(p.t.owner, p.t.workspace, created.entity_id, uuid4())
    await p.run(
        c.ConfirmRecord(
            record_id=version.id,
            content_hash=version.content_hash,
            confirmed=True,
            idempotency_key=uuid4().hex,
        )
    )


async def test_editor_queue_exact_snapshot_once_and_no_content_mutation(
    tenants: TenantFixture,
) -> None:
    t = tenants
    cfg, service, cmd, p = await prepare(t)
    before = await p.post()
    runs = await asyncio.gather(
        *[service.start(t.owner, t.workspace, cmd, uuid4()) for _ in range(3)]
    )
    assert len({r.id for r in runs}) == 1 and runs[0].state == "queued"
    run = runs[0]
    inputs = await service.inputs(t.owner, t.workspace, run.id, uuid4())
    assert inputs.editor_context and not inputs.citations
    assert inputs.editor_context.revision.id == cmd.revision_id
    assert {r.body.kind for r in inputs.editor_context.records} >= {
        "product_fact",
        "claim_policy",
        "brand_profile",
        "source_item",
    }
    text_gateway, editor = Gateway(), EditorGateway()
    results = await asyncio.gather(
        *[
            process(
                t.worker,
                cfg,
                text_gateway,
                t.workspace,
                run.id,
                t.owner.user_id,
                editorial_gateway=editor,
            )
            for _ in range(2)
        ]
    )
    assert sorted(results) == [False, True] and editor.calls == 1 and text_gateway.calls == 0
    result = await service.read(t.owner, t.workspace, run.id, uuid4())
    assert result.editorial_review == review_fixture(inputs.editor_context)
    assert result.assessment is None and result.state == "needs_review"
    assert result.usage["input_tokens"] == 20
    after = await p.post()
    assert before.version == after.version and before.state == after.state == "draft"
    assert before.revisions == after.revisions
    async with t.admin.transaction() as s:
        assert await s.scalar(select(func.count()).select_from(ContentDecision)) == 0
        assert await s.scalar(select(func.count()).select_from(PostRevision)) == 1
    assert (await service.start(t.owner, t.workspace, cmd, uuid4())).id == run.id
    await change_revision(p)
    assert (
        await service.read(t.owner, t.workspace, run.id, uuid4())
    ).error_code == "artifact_editor_stale_or_unavailable"
    # Historical input remains available after a text edit, while its evidence is current.
    assert await service.inputs(t.owner, t.workspace, run.id, uuid4()) == inputs


@pytest.mark.parametrize("change", ["revision", "policy", "brief_expiry", "source_expiry"])
async def test_editor_stale_queue_never_calls_provider(
    tenants: TenantFixture, change: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    t = tenants
    cfg, service, cmd, p = await prepare(t)
    run = await service.start(t.owner, t.workspace, cmd, uuid4())
    if change == "brief_expiry":
        monkeypatch.setattr("smm_gpt.services.editor.utcnow", lambda: p.brief.expires_at)
    elif change == "source_expiry":
        monkeypatch.setattr("smm_gpt.services.content_records.utcnow", lambda: p.source.expires_at)
    else:
        await (change_revision(p) if change == "revision" else change_policy(p))
    editor = EditorGateway()
    assert not await process(
        t.worker, cfg, Gateway(), t.workspace, run.id, t.owner.user_id, editorial_gateway=editor
    )
    ended = await service.read(t.owner, t.workspace, run.id, uuid4())
    assert ended.state == "blocked" and ended.error_code and ended.error_code.startswith("editor_")
    assert editor.calls == 0


@pytest.mark.parametrize("change", ["revision", "policy", "cancel", "profile"])
async def test_editor_discards_inflight_result_and_retains_known_usage(
    tenants: TenantFixture, change: str
) -> None:
    t = tenants
    cfg, service, cmd, p = await prepare(t)
    run = await service.start(t.owner, t.workspace, cmd, uuid4())
    editor = EditorGateway(pause=True)
    task = asyncio.create_task(
        process(
            t.worker, cfg, Gateway(), t.workspace, run.id, t.owner.user_id, editorial_gateway=editor
        )
    )
    await asyncio.wait_for(editor.entered.wait(), 5)
    try:
        if change == "profile":
            from smm_gpt.domain.profiles import DisableProfile
            from smm_gpt.services.profiles import ProfileService

            profiles = ProfileService(t.access)
            head = await profiles.read(t.owner, t.workspace, "editor", uuid4())
            await profiles.execute(
                t.owner,
                t.workspace,
                DisableProfile(
                    idempotency_key=uuid4().hex,
                    profile="editor",
                    expected_revision=head.revision,
                    version_id=head.latest.id,
                    content_hash=head.latest.content_hash,
                    reason="Synthetic explicit disable",
                    human_confirmed=True,
                ),
                uuid4(),
            )
        elif change == "cancel":
            current = await service.read(t.owner, t.workspace, run.id, uuid4())
            await service.cancel(
                t.owner,
                t.workspace,
                run.id,
                CancelAssessment(idempotency_key=uuid4().hex, expected_version=current.version),
                uuid4(),
            )
        else:
            await (change_revision(p) if change == "revision" else change_policy(p))
    finally:
        editor.release.set()
    assert not await task
    result = await service.read(t.owner, t.workspace, run.id, uuid4())
    assert result.state == ("cancelled" if change == "cancel" else "failed")
    assert result.usage["input_tokens"] == 20 and result.editorial_review is None
    async with t.admin.transaction() as s:
        assert await s.scalar(select(func.count()).select_from(AIArtifact)) == 0


async def test_editor_roles_exact_ids_closed_endpoint_and_restricted_worker(
    tenants: TenantFixture,
) -> None:
    t = tenants
    cfg, service, cmd, _ = await prepare(t)
    for actor in (t.viewer, t.other, replace(t.owner, mfa=False)):
        with pytest.raises(AccessDenied):
            await service.start(actor, t.workspace, cmd, uuid4())
    bad = cmd.model_copy(update={"revision_id": uuid4()})
    assert (
        await service.start(t.owner, t.workspace, bad, uuid4())
    ).error_code == "editor_revision_unavailable"
    old = RunAssessment(
        idempotency_key=uuid4().hex,
        profile="editor",
        brand_id=cmd.brand_id,
        question="Must not run as reference assessment",
        testing_only=True,
    )
    assert (
        await service.start(t.owner, t.workspace, old, uuid4())
    ).error_code == "editor_revision_request_required"
    queued = await service.start(
        t.owner, t.workspace, cmd.model_copy(update={"idempotency_key": uuid4().hex}), uuid4()
    )
    for database, sql in [
        (t.worker, "UPDATE posts SET title='Changed'"),
        (t.worker, "INSERT INTO content_decisions SELECT * FROM content_decisions"),
        (t.admin, "UPDATE ai_inputs SET editor_context=NULL"),
    ]:
        with pytest.raises(DBAPIError):
            async with database.transaction(t.owner.user_id, t.workspace) as s:
                await s.execute(text(sql))
    async with t.runtime.transaction(t.viewer.user_id, t.workspace) as s:
        assert await s.scalar(select(func.count()).select_from(AIInput)) == 0
    with pytest.raises(OperationError, match="not_found"):
        await service.read(t.other, t.other_workspace, queued.id, uuid4())
    editor = EditorGateway(invalid=True)
    assert not await process(
        t.worker, cfg, Gateway(), t.workspace, queued.id, t.owner.user_id, editorial_gateway=editor
    )
    result = await service.read(t.owner, t.workspace, queued.id, uuid4())
    assert result.error_code == "model_review_binding_invalid" and result.editorial_review is None
