"""Disposable PostgreSQL tests of the manual content vertical and its invariants."""

import asyncio
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.exc import DBAPIError

from smm_gpt.domain import content as d
from smm_gpt.domain.access import AccessDenied, Principal
from smm_gpt.domain.operations import CreateWorkItem, OperationError, TransitionWorkItem, WorkState
from smm_gpt.infrastructure.content_models import ContentRecord, PostRevision, WorkingCopy
from smm_gpt.infrastructure.models import FileMetadata, Membership, utcnow
from smm_gpt.services.content import ContentService
from smm_gpt.services.operations import Operations

from .conftest import TenantFixture

pytestmark = pytest.mark.integration


@dataclass
class Pilot:
    t: TenantFixture
    core: ContentService
    brand: UUID
    source: d.RecordView
    policy: d.RecordView
    fact: d.RecordView
    brief: d.RecordView
    post_id: UUID

    async def run(
        self, command: d.ContentCommand, actor: Principal | None = None
    ) -> d.CommandResult:
        return await self.core.execute(actor or self.t.owner, self.t.workspace, command, uuid4())

    async def post(self) -> d.PostView:
        return await self.core.read_post(self.t.owner, self.t.workspace, self.post_id, uuid4())

    def body(self, value: str = "Synthetic content") -> d.RevisionBody:
        return d.RevisionBody(
            variants=[d.Variant(destination="vk:group:123", text=value)], fact_ids=[self.fact.id]
        )

    async def approve(self) -> d.PostView:
        post = await self.post()
        await self.run(
            d.RequestReview(
                post_id=post.id, expected_version=post.version, idempotency_key=uuid4().hex
            )
        )
        post = await self.post()
        await self.run(
            d.DecidePost(
                post_id=post.id,
                expected_version=post.version,
                revision_id=post.revisions[0].id,
                content_hash=post.revisions[0].content_hash,
                decision="approve",
                reason="Human synthetic check",
                human_confirmed=True,
                claims_reviewed=True,
                idempotency_key=uuid4().hex,
            )
        )
        return await self.post()

    async def package(self, post: d.PostView) -> d.CommandResult:
        return await self.run(
            d.PreparePackage(
                post_id=post.id,
                expected_version=post.version,
                revision_id=post.revisions[0].id,
                content_hash=post.revisions[0].content_hash,
                scheduled_at=utcnow() + timedelta(days=1),
                human_confirmed=True,
                idempotency_key=uuid4().hex,
            )
        )


async def pilot(t: TenantFixture) -> Pilot:
    core = ContentService(t.access)

    async def run(command: d.ContentCommand) -> d.CommandResult:
        return await core.execute(t.owner, t.workspace, command, uuid4())

    async def catalog(kind: str) -> UUID:
        return (
            await run(
                d.CreateCatalog.model_validate(
                    {"kind": kind, "name": "Synthetic", "idempotency_key": uuid4().hex}
                )
            )
        ).entity_id

    brand, product, source_id = (
        await catalog("brands"),
        await catalog("products"),
        await catalog("sources"),
    )

    async def save(body: d.Artifact, confirmed: bool = True) -> d.RecordView:
        result = await run(
            d.CreateRecord(
                body=body, expires_at=utcnow() + timedelta(days=10), idempotency_key=uuid4().hex
            )
        )
        row = await core.read_record(t.owner, t.workspace, result.entity_id, uuid4())
        if confirmed:
            result = await run(
                d.ConfirmRecord(
                    record_id=row.id,
                    content_hash=row.content_hash,
                    confirmed=True,
                    idempotency_key=uuid4().hex,
                )
            )
            row = await core.read_record(t.owner, t.workspace, result.entity_id, uuid4())
        return row

    source = await save(
        d.SourceItem(
            name="Owner input",
            brand_id=brand,
            source_id=source_id,
            locator="owner-input:synthetic",
            excerpt="Synthetic evidence, not production claims",
            observed_at=utcnow(),
            evidence_kind="owner_input",
        )
    )
    await save(
        d.BrandProfile(
            name="Profile",
            brand_id=brand,
            audience="Adults",
            tone="Factual",
            source_item_id=source.id,
        )
    )
    version = await save(
        d.ProductVersion(
            name="Product v1",
            brand_id=brand,
            product_id=product,
            description="Synthetic description",
            source_item_id=source.id,
        )
    )
    fact = await save(
        d.ProductFact(
            name="Fact",
            brand_id=brand,
            product_version_id=version.id,
            statement="Synthetic fact",
            source_item_id=source.id,
        )
    )
    policy = await save(
        d.ClaimPolicy(
            name="Policy",
            brand_id=brand,
            source_item_id=source.id,
            jurisdiction="Internal pilot, not legal advice",
            rules=[d.ClaimRule(phrase="forbidden", severity="blocker")],
        )
    )
    research = await save(
        d.Research(
            name="Research",
            brand_id=brand,
            source_item_ids=[source.id],
            observations="Observed data",
            hypotheses="Untested idea",
        ),
        False,
    )
    campaign = await save(
        d.Campaign(
            name="Campaign",
            brand_id=brand,
            goal="Learn",
            kpi="Manual review",
            owner_id=t.owner.user_id,
            starts_at=utcnow(),
            ends_at=utcnow() + timedelta(days=5),
        ),
        False,
    )
    await save(
        d.ContentPlan(
            name="Plan",
            brand_id=brand,
            campaign_id=campaign.id,
            slots=[
                d.Slot(
                    planned_at=utcnow() + timedelta(days=1),
                    topic="Topic",
                    destination="vk:group:123",
                )
            ],
        ),
        False,
    )
    brief = await save(
        d.Brief(
            name="Brief",
            brand_id=brand,
            product_id=product,
            goal="Explain",
            audience="Adults",
            campaign_id=campaign.id,
            research_id=research.id,
        ),
        False,
    )
    idea = await save(
        d.Idea(name="Idea", brand_id=brand, brief_id=brief.id, rationale="Test"), False
    )
    result = await run(
        d.CreatePost(
            brief_id=brief.id, idea_id=idea.id, title="Synthetic post", idempotency_key=uuid4().hex
        )
    )
    p = Pilot(t, core, brand, source, policy, fact, brief, result.entity_id)
    await p.run(
        d.SaveRevision(
            post_id=p.post_id, expected_version=1, body=p.body(), idempotency_key=uuid4().hex
        )
    )
    return p


async def test_manual_vertical_exact_manifest_and_invalidation(tenants: TenantFixture) -> None:
    p = await pilot(tenants)
    post = await p.post()
    report = await p.core.check(p.t.owner, p.t.workspace, post.id, uuid4())
    assert report.passed and report.ai_review == "not_run"
    assert post.active_approval_id is None
    with pytest.raises(OperationError, match="approval_required"):
        await p.package(post)
    post = await p.approve()
    result = await p.package(post)
    package = await p.core.read_package(p.t.owner, p.t.workspace, result.entity_id, uuid4())
    assert package.status == "active"
    assert package.manifest["external_dispatch"] is False
    assert package.manifest["revision"] == post.revisions[0].model_dump(mode="json")
    assert package.content_hash == post.revisions[0].content_hash
    await p.run(
        d.AddComment(
            post_id=post.id,
            revision_id=post.revisions[0].id,
            text="<script>not executable</script>",
            idempotency_key=uuid4().hex,
        )
    )
    post = await p.post()
    assert post.state == "package_ready" and post.active_approval_id
    await p.run(
        d.SaveRevision(
            post_id=post.id,
            expected_version=post.version,
            body=p.body("Changed"),
            idempotency_key=uuid4().hex,
        )
    )
    changed = await p.post()
    assert changed.state == "draft" and changed.active_approval_id is None
    assert len(changed.revisions) == 2 and len(changed.decisions) == 1
    assert (
        await p.core.read_package(p.t.owner, p.t.workspace, package.id, uuid4())
    ).status == "stale"
    history = await p.core.history(p.t.owner, p.t.workspace, post.id, uuid4(), "revisions", 1)
    assert history.next_cursor
    assert (
        len(
            (
                await p.core.history(
                    p.t.owner, p.t.workspace, post.id, uuid4(), "revisions", 1, history.next_cursor
                )
            ).items
        )
        == 1
    )


async def test_role_rls_immutable_history_and_retries(tenants: TenantFixture) -> None:
    p = await pilot(tenants)
    cmd = d.SaveRevision(
        post_id=p.post_id, expected_version=2, body=p.body(), idempotency_key="same-content-key"
    )
    results = await asyncio.gather(*(p.run(cmd) for _ in range(4)))
    assert len({r.entity_id for r in results}) == 1
    with pytest.raises(OperationError, match="idempotency_conflict"):
        await p.run(cmd.model_copy(update={"body": p.body("Different")}))
    with pytest.raises(AccessDenied):
        await p.run(cmd, p.t.viewer)
    with pytest.raises(AccessDenied):
        await p.core.read_post(p.t.other, p.t.workspace, p.post_id, uuid4())
    with pytest.raises(OperationError, match="not_found"):
        await p.core.read_post(p.t.other, p.t.other_workspace, p.post_id, uuid4())
    async with p.t.runtime.transaction(p.t.other.user_id, p.t.other_workspace) as s:
        assert await s.get(PostRevision, results[0].entity_id) is None
        assert await s.get(ContentRecord, p.fact.id) is None
    for statement in (
        "UPDATE post_revisions SET number=99",
        "DELETE FROM content_records",
        "TRUNCATE content_decisions",
    ):
        with pytest.raises(DBAPIError):
            async with p.t.admin.transaction() as s:
                await s.execute(text(statement))
    # A valid tenant cannot attach another tenant's foreign key even with raw SQL.
    with pytest.raises(DBAPIError):
        async with p.t.runtime.transaction(p.t.other.user_id, p.t.other_workspace) as s:
            s.add(
                ContentRecord(
                    workspace_id=p.t.other_workspace,
                    brand_id=p.brand,
                    family_id=uuid4(),
                    number=1,
                    kind="brief",
                    body={},
                    content_hash="a" * 64,
                    actor_id=p.t.other.user_id,
                    expires_at=utcnow() + timedelta(days=1),
                )
            )


async def test_concurrent_edit_and_review_permissions(tenants: TenantFixture) -> None:
    p = await pilot(tenants)
    commands = [
        d.SaveRevision(
            post_id=p.post_id, expected_version=2, body=p.body(str(i)), idempotency_key=uuid4().hex
        )
        for i in range(2)
    ]
    outcomes = await asyncio.gather(*(p.run(c) for c in commands), return_exceptions=True)
    assert sum(isinstance(r, OperationError) for r in outcomes) == 1
    await p.run(d.RequestReview(post_id=p.post_id, expected_version=3, idempotency_key=uuid4().hex))
    post = await p.post()
    command = d.DecidePost(
        post_id=post.id,
        expected_version=post.version,
        revision_id=post.revisions[0].id,
        content_hash=post.revisions[0].content_hash,
        decision="approve",
        reason="Checked",
        human_confirmed=True,
        claims_reviewed=True,
        idempotency_key=uuid4().hex,
    )
    for role in ("editor", "publisher", "administrator", "strategist", "analyst", "viewer"):
        async with p.t.admin.transaction() as s:
            await s.execute(
                update(Membership).where(Membership.user_id == p.t.viewer.user_id).values(role=role)
            )
        with pytest.raises(AccessDenied):
            await p.run(command, p.t.viewer)
    with pytest.raises(AccessDenied):
        await p.run(command, Principal(p.t.owner.user_id, p.t.owner.identity_id, False))
    with pytest.raises(OperationError, match="revision_conflict"):
        await p.run(command.model_copy(update={"content_hash": "0" * 64}))
    await p.run(command)


async def test_preflight_freshness_policy_change_and_gaps(tenants: TenantFixture) -> None:
    p = await pilot(tenants)
    await p.run(
        d.SaveRevision(
            post_id=p.post_id,
            expected_version=2,
            body=p.body("FORBIDDEN"),
            idempotency_key=uuid4().hex,
        )
    )
    with pytest.raises(OperationError, match="preflight_blocked"):
        await p.approve()
    post = await p.post()
    await p.run(
        d.SaveRevision(
            post_id=post.id,
            expected_version=post.version,
            body=p.body(),
            idempotency_key=uuid4().hex,
        )
    )
    post = await p.approve()
    package = await p.package(post)
    # A confirmed policy change invalidates the approval context even if text still passes.
    changed = await p.run(
        d.CreateRecord(
            body=p.policy.body,
            replaces_id=p.policy.id,
            expires_at=p.policy.expires_at,
            idempotency_key=uuid4().hex,
        )
    )
    await p.run(
        d.ConfirmRecord(
            record_id=changed.entity_id,
            content_hash=p.policy.content_hash,
            confirmed=True,
            idempotency_key=uuid4().hex,
        )
    )
    assert (
        await p.core.read_package(p.t.owner, p.t.workspace, package.entity_id, uuid4())
    ).status == "stale"
    await p.run(
        d.CancelPackage(
            package_id=package.entity_id,
            expected_version=package.version,
            idempotency_key=uuid4().hex,
        )
    )
    with pytest.raises(OperationError, match="approval_context_changed"):
        await p.package(await p.post())
    # Immutable facts cannot be scheduled beyond the evidence horizon.
    post = await p.post()
    with pytest.raises(OperationError, match="preflight_blocked"):
        await p.run(
            d.PreparePackage(
                post_id=post.id,
                expected_version=post.version,
                revision_id=post.revisions[0].id,
                content_hash=post.revisions[0].content_hash,
                scheduled_at=utcnow() + timedelta(days=20),
                human_confirmed=True,
                idempotency_key=uuid4().hex,
            )
        )
    gaps = p.body().model_copy(update={"knowledge_gaps": ["Missing consent"]})
    await p.run(
        d.SaveRevision(
            post_id=post.id, expected_version=post.version, body=gaps, idempotency_key=uuid4().hex
        )
    )
    assert "knowledge_gap" in {
        f.code for f in (await p.core.check(p.t.owner, p.t.workspace, post.id, uuid4())).findings
    }


async def test_private_working_copies_expiry_and_dependencies(tenants: TenantFixture) -> None:
    p = await pilot(tenants)
    async with p.t.admin.transaction() as s:
        await s.execute(
            update(Membership).where(Membership.user_id == p.t.viewer.user_id).values(role="editor")
        )
    cmd = d.SaveWorkingCopy(
        post_id=p.post_id,
        expected_copy_version=0,
        base_version=2,
        body=p.body("Private draft"),
        idempotency_key=uuid4().hex,
    )
    await p.run(cmd)
    assert await p.core.working_copy(p.t.viewer, p.t.workspace, p.post_id, uuid4()) is None
    async with p.t.runtime.transaction(p.t.viewer.user_id, p.t.workspace) as s:
        assert (await s.scalars(select(WorkingCopy))).all() == []
    async with p.t.admin.transaction() as s:
        await s.execute(update(WorkingCopy).values(expires_at=utcnow() - timedelta(days=1)))
    assert await p.core.working_copy(p.t.owner, p.t.workspace, p.post_id, uuid4()) is None
    await p.run(cmd.model_copy(update={"idempotency_key": uuid4().hex}))
    ops = Operations(p.t.access)
    a, b = [
        await ops.create_work(
            p.t.owner,
            p.t.workspace,
            CreateWorkItem(title="Task", idempotency_key=uuid4().hex),
            uuid4(),
        )
        for _ in range(2)
    ]
    await p.run(
        d.AssignTask(
            item_id=a.id,
            expected_version=1,
            assignee_id=p.t.viewer.user_id,
            due_at=utcnow() + timedelta(days=1),
            idempotency_key=uuid4().hex,
        )
    )
    await p.run(
        d.DependTask(item_id=a.id, depends_on=b.id, expected_version=2, idempotency_key=uuid4().hex)
    )
    with pytest.raises(OperationError, match="dependency_cycle"):
        await p.run(
            d.DependTask(
                item_id=b.id, depends_on=a.id, expected_version=1, idempotency_key=uuid4().hex
            )
        )
    with pytest.raises(OperationError, match="dependencies_not_done"):
        await ops.transition_work(
            p.t.owner,
            p.t.workspace,
            a.id,
            TransitionWorkItem(expected_version=3, state=WorkState.IN_PROGRESS),
            uuid4(),
        )
    context = await p.core.task_context(p.t.owner, p.t.workspace, a.id, uuid4())
    assert context.assignee_id == p.t.viewer.user_id and context.dependencies == [b.id]
    await ops.transition_work(
        p.t.owner,
        p.t.workspace,
        b.id,
        TransitionWorkItem(expected_version=1, state=WorkState.IN_PROGRESS),
        uuid4(),
    )
    await ops.transition_work(
        p.t.owner,
        p.t.workspace,
        b.id,
        TransitionWorkItem(expected_version=2, state=WorkState.DONE),
        uuid4(),
    )
    await ops.transition_work(
        p.t.owner,
        p.t.workspace,
        a.id,
        TransitionWorkItem(expected_version=3, state=WorkState.IN_PROGRESS),
        uuid4(),
    )


async def test_media_snapshot_publisher_and_source_provenance(tenants: TenantFixture) -> None:
    p = await pilot(tenants)
    media_id = uuid4()
    async with p.t.admin.transaction() as s:
        s.add(
            FileMetadata(
                id=media_id,
                workspace_id=p.t.workspace,
                storage_key="synthetic/internal",
                content_type="image/png",
                sha256="a" * 64,
                size_bytes=256,
            )
        )
        await s.execute(
            update(Membership)
            .where(Membership.user_id == p.t.viewer.user_id)
            .values(role="publisher")
        )
    body = p.body()
    body.variants[0].media = [
        d.Attachment(file_id=media_id, alt="Synthetic photo", rights_confirmed=True)
    ]
    await p.run(
        d.SaveRevision(
            post_id=p.post_id, expected_version=2, body=body, idempotency_key=uuid4().hex
        )
    )
    post = await p.approve()
    command = d.PreparePackage(
        post_id=post.id,
        expected_version=post.version,
        revision_id=post.revisions[0].id,
        content_hash=post.revisions[0].content_hash,
        scheduled_at=utcnow() + timedelta(days=1),
        human_confirmed=True,
        idempotency_key=uuid4().hex,
    )
    package = await p.run(command, p.t.viewer)
    result = await p.core.read_package(p.t.owner, p.t.workspace, package.entity_id, uuid4())
    assert result.status == "active" and result.manifest["media_bytes_verified"] is False
    assert post.revisions[0].media_manifest[0]["sha256"] == "a" * 64
    assert "storage_key" not in str(result.manifest)
    async with p.t.admin.transaction() as s:
        await s.execute(
            update(FileMetadata).where(FileMetadata.id == media_id).values(sha256="b" * 64)
        )
    assert (
        await p.core.read_package(p.t.owner, p.t.workspace, package.entity_id, uuid4())
    ).status == "stale"
    # A confirmed hypothesis is still not valid evidence for an authoritative fact.
    source = d.SourceItem.model_validate(p.source.body).model_copy(
        update={"evidence_kind": "hypothesis"}
    )
    created = await p.run(
        d.CreateRecord(body=source, expires_at=p.source.expires_at, idempotency_key=uuid4().hex)
    )
    confirmed = await p.run(
        d.ConfirmRecord(
            record_id=created.entity_id,
            content_hash=d.canonical_hash(source),
            confirmed=True,
            idempotency_key=uuid4().hex,
        )
    )
    fact = d.ProductFact.model_validate(p.fact.body).model_copy(
        update={"source_item_id": confirmed.entity_id}
    )
    created = await p.run(
        d.CreateRecord(body=fact, expires_at=p.fact.expires_at, idempotency_key=uuid4().hex)
    )
    with pytest.raises(OperationError, match="hypothesis_is_not_evidence"):
        await p.run(
            d.ConfirmRecord(
                record_id=created.entity_id,
                content_hash=d.canonical_hash(fact),
                confirmed=True,
                idempotency_key=uuid4().hex,
            )
        )
