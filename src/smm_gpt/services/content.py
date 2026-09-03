"""Transactional, transport-neutral manual content lifecycle. No social connector exists here."""

from collections.abc import Sequence
from datetime import timedelta
from typing import cast
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from smm_gpt.domain import content as d
from smm_gpt.domain.access import Permission, Principal
from smm_gpt.domain.operations import OperationError, Page
from smm_gpt.infrastructure.content_models import (
    ContentComment,
    ContentDecision,
    ContentReceipt,
    ContentRecord,
    PackageCancellation,
    Post,
    PostRevision,
    PublicationPackage,
    ReviewRun,
    WorkAssignment,
    WorkDependency,
    WorkingCopy,
)
from smm_gpt.infrastructure.models import Brand, Product, Source, WorkItem, Workspace, utcnow
from smm_gpt.services.access import AccessService, audit, digest
from smm_gpt.services.content_preflight import preflight
from smm_gpt.services.content_records import (
    confirm_record,
    create_record,
    member,
    record,
    workspace_lock,
)
from smm_gpt.services.content_revision import save_revision
from smm_gpt.services.operations import Operations


def permission(command: d.ContentCommand) -> Permission:
    if isinstance(command, (d.ConfirmRecord, d.DecidePost)):
        return Permission.APPROVE
    if isinstance(command, (d.PreparePackage, d.CancelPackage)):
        return Permission.PUBLISH
    if isinstance(command, (d.AssignTask, d.DependTask)):
        return Permission.WORK_ITEM
    if isinstance(command, d.AddComment):
        return Permission.COMMENT
    if isinstance(command, d.CreateRecord) and isinstance(
        command.body, (d.Campaign, d.ContentPlan, d.Brief, d.Idea)
    ):
        return Permission.PLAN
    return Permission.EDIT


async def post_row(s: AsyncSession, wid: UUID, pid: UUID) -> Post:
    post = await s.scalar(
        select(Post).where(Post.workspace_id == wid, Post.id == pid).with_for_update()
    )
    if post is None:
        raise OperationError("not_found", 404)
    return post


async def revision_row(s: AsyncSession, post: Post, rid: UUID | None = None) -> PostRevision:
    revision = await s.scalar(
        select(PostRevision).where(
            PostRevision.workspace_id == post.workspace_id,
            PostRevision.post_id == post.id,
            PostRevision.id == (rid or post.current_revision_id),
        )
    )
    if revision is None:
        raise OperationError("revision_required", 422)
    return revision


def exact_revision(post: Post, revision: PostRevision, rid: UUID, content_hash: str) -> None:
    if (
        revision.id != rid
        or post.current_revision_id != rid
        or revision.content_hash != content_hash
    ):
        raise OperationError("revision_conflict")


class ContentService:
    def __init__(self, access: AccessService):
        self.access = access

    async def execute(
        self, actor: Principal, wid: UUID, command: d.ContentCommand, request: UUID
    ) -> d.CommandResult:
        async with self.access.authorized(actor, wid, permission(command), request) as s:
            await workspace_lock(s, wid)
            fingerprint = d.canonical_hash(
                command.model_dump(mode="json", exclude={"idempotency_key"})
            )
            key = digest(command.idempotency_key)
            previous = await s.scalar(
                select(ContentReceipt).where(
                    ContentReceipt.workspace_id == wid,
                    ContentReceipt.actor_id == actor.user_id,
                    ContentReceipt.key_hash == key,
                )
            )
            if previous:
                if previous.request_hash != fingerprint:
                    raise OperationError("idempotency_conflict")
                return d.CommandResult.model_validate(previous.result)
            entity_id, version = await self._execute(s, actor, wid, command)
            result = d.CommandResult(entity_id=entity_id, version=version, action=command.action)
            s.add(
                ContentReceipt(
                    workspace_id=wid,
                    actor_id=actor.user_id,
                    key_hash=key,
                    request_hash=fingerprint,
                    result=result.model_dump(mode="json"),
                )
            )
            audit(
                s, actor.user_id, wid, request, "content." + command.action, "succeeded", entity_id
            )
            return result

    async def _execute(
        self, s: AsyncSession, actor: Principal, wid: UUID, command: d.ContentCommand
    ) -> tuple[UUID, int]:
        if isinstance(command, d.CreateCatalog):
            models: dict[str, type[Brand] | type[Product] | type[Source]] = {
                "brands": Brand,
                "products": Product,
                "sources": Source,
            }
            entry = models[command.kind](workspace_id=wid, name=command.name)
            s.add(entry)
            await s.flush()
            return entry.id, 1
        if isinstance(command, d.CreateRecord):
            created = await create_record(s, wid, actor.user_id, command)
            return created.id, created.number
        if isinstance(command, d.ConfirmRecord):
            confirmed = await confirm_record(s, wid, actor.user_id, command)
            return confirmed.id, confirmed.number
        if isinstance(command, d.CreatePost):
            brief = await record(s, wid, command.brief_id, "brief")
            if command.idea_id:
                idea = await record(s, wid, command.idea_id, "idea", brief.brand_id)
                if d.Idea.model_validate(idea.body).brief_id != brief.id:
                    raise OperationError("idea_brief_mismatch", 422)
            post = Post(
                workspace_id=wid,
                brand_id=brief.brand_id,
                brief_id=brief.id,
                idea_id=command.idea_id,
                title=command.title,
            )
            s.add(post)
            await s.flush()
            return post.id, post.version
        if isinstance(command, (d.AssignTask, d.DependTask)):
            return await self._task(s, wid, command)
        if isinstance(command, d.CancelPackage):
            package = await s.scalar(
                select(PublicationPackage).where(
                    PublicationPackage.workspace_id == wid,
                    PublicationPackage.id == command.package_id,
                )
            )
            if package is None:
                raise OperationError("not_found", 404)
            post = await post_row(s, wid, package.post_id)
            d.check_version(post.version, command.expected_version)
            existing = await s.scalar(
                select(PackageCancellation.id).where(
                    PackageCancellation.workspace_id == wid,
                    PackageCancellation.package_id == package.id,
                )
            )
            if existing:
                raise OperationError("package_already_cancelled")
            s.add(
                PackageCancellation(workspace_id=wid, package_id=package.id, actor_id=actor.user_id)
            )
            post.version += 1
            if (
                post.current_revision_id == package.revision_id
                and post.active_approval_id == package.approval_id
            ):
                post.state = "approved"
            return package.id, post.version
        post = await post_row(s, wid, command.post_id)
        if isinstance(command, d.SaveWorkingCopy):
            # Preserve stale work without silently committing it as a new revision.
            row = await s.scalar(
                select(WorkingCopy).where(
                    WorkingCopy.workspace_id == wid,
                    WorkingCopy.post_id == post.id,
                    WorkingCopy.actor_id == actor.user_id,
                )
            )
            if row and row.expires_at <= utcnow():
                await s.delete(row)
                await s.flush()
                row = None
            actual = row.version if row else 0
            d.check_version(actual, command.expected_copy_version)
            if command.base_version > post.version:
                raise OperationError("version_conflict")
            if row is None:
                row = WorkingCopy(workspace_id=wid, post_id=post.id, actor_id=actor.user_id)
                s.add(row)
            row.version = actual + 1
            row.base_version = command.base_version
            row.body = command.body.model_dump(mode="json")
            row.expires_at = utcnow() + timedelta(days=7)
            return post.id, row.version
        if isinstance(command, d.AddComment):
            revision = await revision_row(s, post, command.revision_id)
            comment = ContentComment(
                workspace_id=wid,
                post_id=post.id,
                revision_id=revision.id,
                actor_id=actor.user_id,
                text=command.text,
            )
            s.add(comment)
            await s.flush()
            return comment.id, post.version
        d.check_version(post.version, command.expected_version)
        if isinstance(command, d.SaveRevision):
            revision = await save_revision(
                s,
                post,
                actor.user_id,
                command.body,
                clear_working_copy=True,
            )
            return revision.id, post.version
        revision = await revision_row(s, post)
        if isinstance(command, d.RequestReview):
            if post.state not in {"draft", "rejected"}:
                raise OperationError("invalid_transition")
            report = await preflight(s, post, revision)
            s.add(
                ReviewRun(
                    workspace_id=wid,
                    post_id=post.id,
                    revision_id=revision.id,
                    actor_id=actor.user_id,
                    result=report.model_dump(mode="json"),
                )
            )
            post.state = "in_review"
            post.version += 1
            return post.id, post.version
        if isinstance(command, d.DecidePost):
            exact_revision(post, revision, command.revision_id, command.content_hash)
            if post.state != "in_review":
                raise OperationError("invalid_transition")
            report = await preflight(s, post, revision)
            if command.decision == "approve" and not report.passed:
                raise OperationError("preflight_blocked", 422)
            decision = ContentDecision(
                id=uuid4(),
                workspace_id=wid,
                post_id=post.id,
                revision_id=revision.id,
                actor_id=actor.user_id,
                decision=command.decision,
                reason=command.reason,
                content_hash=revision.content_hash,
                preflight=report.model_dump(mode="json"),
            )
            s.add(decision)
            await s.flush()
            post.active_approval_id = decision.id if command.decision == "approve" else None
            post.state = "approved" if command.decision == "approve" else "rejected"
            post.version += 1
            return decision.id, post.version
        if isinstance(command, d.PreparePackage):
            exact_revision(post, revision, command.revision_id, command.content_hash)
            if post.state != "approved" or not post.active_approval_id:
                raise OperationError("approval_required", 422)
            if command.scheduled_at <= utcnow():
                raise OperationError("schedule_in_past", 422)
            approval = await s.scalar(
                select(ContentDecision).where(
                    ContentDecision.workspace_id == wid,
                    ContentDecision.id == post.active_approval_id,
                    ContentDecision.post_id == post.id,
                    ContentDecision.revision_id == revision.id,
                    ContentDecision.content_hash == revision.content_hash,
                    ContentDecision.decision == "approve",
                )
            )
            if approval is None:
                raise OperationError("approval_required", 422)
            report = await preflight(s, post, revision, command.scheduled_at)
            if not report.passed:
                raise OperationError("preflight_blocked", 422)
            if (
                approval.preflight.get("checked_record_ids")
                != report.model_dump(mode="json")["checked_record_ids"]
            ):
                raise OperationError("approval_context_changed", 422)
            workspace = await s.get(Workspace, wid)
            assert workspace is not None
            package = PublicationPackage(
                workspace_id=wid,
                post_id=post.id,
                revision_id=revision.id,
                approval_id=approval.id,
                actor_id=actor.user_id,
                content_hash=revision.content_hash,
                scheduled_at=command.scheduled_at,
                timezone=workspace.timezone,
                manifest={
                    "mode": "manual",
                    "external_dispatch": False,
                    "media_bytes_verified": False,
                    "revision": d.RevisionView.model_validate(revision).model_dump(mode="json"),
                    "approval": d.DecisionView.model_validate(approval).model_dump(mode="json"),
                    "preflight": report.model_dump(mode="json"),
                    "scheduled_at": command.scheduled_at.isoformat(),
                    "timezone": workspace.timezone,
                },
            )
            s.add(package)
            await s.flush()
            post.state = "package_ready"
            post.version += 1
            return package.id, post.version
        raise OperationError("invalid_request", 422)

    async def _task(
        self, s: AsyncSession, wid: UUID, command: d.AssignTask | d.DependTask
    ) -> tuple[UUID, int]:
        item = await s.scalar(
            select(WorkItem)
            .where(WorkItem.workspace_id == wid, WorkItem.id == command.item_id)
            .with_for_update()
        )
        if item is None:
            raise OperationError("not_found", 404)
        d.check_version(item.version, command.expected_version)
        if item.state in {"done", "cancelled"}:
            raise OperationError("invalid_transition")
        if isinstance(command, d.AssignTask):
            await member(s, wid, command.assignee_id)
            if command.campaign_id:
                await record(s, wid, command.campaign_id, "campaign")
            assignment = await s.scalar(
                select(WorkAssignment).where(
                    WorkAssignment.workspace_id == wid, WorkAssignment.item_id == item.id
                )
            )
            if assignment is None:
                assignment = WorkAssignment(workspace_id=wid, item_id=item.id)
                s.add(assignment)
            assignment.assignee_id, assignment.due_at = command.assignee_id, command.due_at
            assignment.campaign_id = command.campaign_id
        else:
            dependency = await s.scalar(
                select(WorkItem).where(
                    WorkItem.workspace_id == wid, WorkItem.id == command.depends_on
                )
            )
            if dependency is None or item.id == command.depends_on:
                raise OperationError("dependency_unavailable", 422)
            rows = (
                await s.scalars(
                    select(WorkDependency)
                    .where(WorkDependency.workspace_id == wid, WorkDependency.active.is_(True))
                    .limit(501)
                )
            ).all()
            if len(rows) >= 500 and not command.remove:
                raise OperationError("dependency_limit_exceeded", 422)
            graph: dict[UUID, list[UUID]] = {}
            for row in rows:
                graph.setdefault(row.item_id, []).append(row.depends_on)
            stack, visited = [command.depends_on], set()
            while stack and not command.remove:
                node = stack.pop()
                if node == item.id:
                    raise OperationError("dependency_cycle", 422)
                if node not in visited:
                    visited.add(node)
                    stack.extend(graph.get(node, []))
            edge = await s.scalar(
                select(WorkDependency).where(
                    WorkDependency.workspace_id == wid,
                    WorkDependency.item_id == item.id,
                    WorkDependency.depends_on == command.depends_on,
                )
            )
            if edge is None:
                edge = WorkDependency(
                    workspace_id=wid, item_id=item.id, depends_on=command.depends_on
                )
                s.add(edge)
            edge.active = not command.remove
        item.version += 1
        return item.id, item.version

    async def records(
        self,
        actor: Principal,
        wid: UUID,
        request: UUID,
        kind: d.RecordKind | None = None,
        limit: int = 25,
        cursor: UUID | None = None,
    ) -> Page[d.RecordView]:
        Operations.page_size(limit)
        async with self.access.authorized(actor, wid, Permission.READ, request) as s:
            query = select(ContentRecord).where(ContentRecord.workspace_id == wid)
            if kind:
                query = query.where(ContentRecord.kind == kind)
            if cursor:
                query = query.where(ContentRecord.id > cursor)
            rows = (await s.scalars(query.order_by(ContentRecord.id).limit(limit + 1))).all()
            return Page(
                items=[d.RecordView.model_validate(row) for row in rows[:limit]],
                next_cursor=rows[limit - 1].id if len(rows) > limit else None,
            )

    async def read_record(
        self, actor: Principal, wid: UUID, rid: UUID, request: UUID
    ) -> d.RecordView:
        async with self.access.authorized(actor, wid, Permission.READ, request) as s:
            return d.RecordView.model_validate(await record(s, wid, rid))

    async def posts(
        self,
        actor: Principal,
        wid: UUID,
        request: UUID,
        state: d.PostState | None = None,
        limit: int = 25,
        cursor: UUID | None = None,
    ) -> Page[d.PostSummary]:
        Operations.page_size(limit)
        async with self.access.authorized(actor, wid, Permission.READ, request) as s:
            query = select(Post).where(Post.workspace_id == wid)
            if state:
                query = query.where(Post.state == state)
            if cursor:
                query = query.where(Post.id > cursor)
            rows = (await s.scalars(query.order_by(Post.id).limit(limit + 1))).all()
            return Page(
                items=[d.PostSummary.model_validate(row) for row in rows[:limit]],
                next_cursor=rows[limit - 1].id if len(rows) > limit else None,
            )

    async def read_post(self, actor: Principal, wid: UUID, pid: UUID, request: UUID) -> d.PostView:
        async with self.access.authorized(actor, wid, Permission.READ, request) as s:
            post = await post_row(s, wid, pid)
            revisions = (
                await s.scalars(
                    select(PostRevision)
                    .where(PostRevision.workspace_id == wid, PostRevision.post_id == pid)
                    .order_by(PostRevision.number.desc())
                    .limit(11)
                )
            ).all()
            decisions = (
                await s.scalars(
                    select(ContentDecision)
                    .where(ContentDecision.workspace_id == wid, ContentDecision.post_id == pid)
                    .order_by(ContentDecision.created_at.desc(), ContentDecision.id)
                    .limit(21)
                )
            ).all()
            comments = (
                await s.scalars(
                    select(ContentComment)
                    .where(ContentComment.workspace_id == wid, ContentComment.post_id == pid)
                    .order_by(ContentComment.created_at.desc(), ContentComment.id)
                    .limit(21)
                )
            ).all()
            return d.PostView(
                **d.PostSummary.model_validate(post).model_dump(),
                brief_id=post.brief_id,
                idea_id=post.idea_id,
                active_approval_id=post.active_approval_id,
                revisions=[d.RevisionView.model_validate(r) for r in revisions[:10]],
                decisions=[d.DecisionView.model_validate(r) for r in decisions[:20]],
                comments=[d.CommentView.model_validate(r) for r in comments[:20]],
                history_truncated=len(revisions) > 10 or len(decisions) > 20 or len(comments) > 20,
            )

    async def check(self, actor: Principal, wid: UUID, pid: UUID, request: UUID) -> d.Preflight:
        async with self.access.authorized(actor, wid, Permission.READ, request) as s:
            post = await post_row(s, wid, pid)
            return await preflight(s, post, await revision_row(s, post))

    async def working_copy(
        self, actor: Principal, wid: UUID, pid: UUID, request: UUID
    ) -> d.WorkingCopyView | None:
        async with self.access.authorized(actor, wid, Permission.EDIT, request) as s:
            await post_row(s, wid, pid)
            row = await s.scalar(
                select(WorkingCopy).where(
                    WorkingCopy.workspace_id == wid,
                    WorkingCopy.post_id == pid,
                    WorkingCopy.actor_id == actor.user_id,
                    WorkingCopy.expires_at > utcnow(),
                )
            )
            return d.WorkingCopyView.model_validate(row) if row else None

    async def _package_view(self, s: AsyncSession, row: PublicationPackage) -> d.PackageView:
        post = await post_row(s, row.workspace_id, row.post_id)
        status: str = "active"
        cancelled = await s.scalar(
            select(PackageCancellation.id).where(
                PackageCancellation.workspace_id == row.workspace_id,
                PackageCancellation.package_id == row.id,
            )
        )
        if cancelled:
            status = "cancelled"
        elif (
            post.current_revision_id != row.revision_id
            or post.active_approval_id != row.approval_id
        ):
            status = "stale"
        elif row.scheduled_at <= utcnow():
            status = "expired"
        else:
            report = await preflight(s, post, await revision_row(s, post), row.scheduled_at)
            original_report = d.Preflight.model_validate(row.manifest["preflight"])
            if not report.passed or report.checked_record_ids != original_report.checked_record_ids:
                status = "stale"
        return d.PackageView.model_validate(
            {
                "id": row.id,
                "post_id": row.post_id,
                "revision_id": row.revision_id,
                "created_at": row.created_at,
                "content_hash": row.content_hash,
                "scheduled_at": row.scheduled_at,
                "timezone": row.timezone,
                "status": status,
                "manifest": row.manifest,
            }
        )

    async def packages(
        self,
        actor: Principal,
        wid: UUID,
        request: UUID,
        limit: int = 25,
        cursor: UUID | None = None,
    ) -> Page[d.PackageSummary]:
        Operations.page_size(limit)
        async with self.access.authorized(actor, wid, Permission.READ, request) as s:
            query = select(PublicationPackage).where(PublicationPackage.workspace_id == wid)
            if cursor:
                query = query.where(PublicationPackage.id > cursor)
            rows = (await s.scalars(query.order_by(PublicationPackage.id).limit(limit + 1))).all()
            return Page(
                items=[
                    d.PackageSummary.model_validate(
                        (await self._package_view(s, r)).model_dump(exclude={"manifest"})
                    )
                    for r in rows[:limit]
                ],
                next_cursor=rows[limit - 1].id if len(rows) > limit else None,
            )

    async def read_package(
        self, actor: Principal, wid: UUID, pid: UUID, request: UUID
    ) -> d.PackageView:
        async with self.access.authorized(actor, wid, Permission.READ, request) as s:
            row = await s.scalar(
                select(PublicationPackage).where(
                    PublicationPackage.workspace_id == wid, PublicationPackage.id == pid
                )
            )
            if row is None:
                raise OperationError("not_found", 404)
            return await self._package_view(s, row)

    async def task_context(
        self, actor: Principal, wid: UUID, iid: UUID, request: UUID
    ) -> d.TaskContext:
        async with self.access.authorized(actor, wid, Permission.READ, request) as s:
            item = await s.scalar(
                select(WorkItem).where(WorkItem.workspace_id == wid, WorkItem.id == iid)
            )
            if item is None:
                raise OperationError("not_found", 404)
            assignment = await s.scalar(
                select(WorkAssignment).where(
                    WorkAssignment.workspace_id == wid, WorkAssignment.item_id == iid
                )
            )
            dependencies = (
                await s.scalars(
                    select(WorkDependency.depends_on)
                    .where(
                        WorkDependency.workspace_id == wid,
                        WorkDependency.item_id == iid,
                        WorkDependency.active.is_(True),
                    )
                    .order_by(WorkDependency.depends_on)
                    .limit(501)
                )
            ).all()
            return d.TaskContext(
                item_id=iid,
                version=item.version,
                assignee_id=assignment.assignee_id if assignment else None,
                due_at=assignment.due_at if assignment else None,
                campaign_id=assignment.campaign_id if assignment else None,
                dependencies=list(dependencies),
            )

    async def history(
        self,
        actor: Principal,
        wid: UUID,
        pid: UUID,
        request: UUID,
        kind: d.HistoryKind,
        limit: int = 10,
        cursor: UUID | None = None,
    ) -> Page[d.HistoryEntry]:
        Operations.page_size(limit)
        async with self.access.authorized(actor, wid, Permission.READ, request) as s:
            await post_row(s, wid, pid)
            models: dict[
                str,
                type[PostRevision] | type[ContentDecision] | type[ContentComment] | type[ReviewRun],
            ] = {
                "revisions": PostRevision,
                "decisions": ContentDecision,
                "comments": ContentComment,
                "reviews": ReviewRun,
            }
            model = models[kind]
            query = select(model).where(model.workspace_id == wid, model.post_id == pid)
            if cursor:
                query = query.where(model.id > cursor)
            rows = cast(
                Sequence[PostRevision | ContentDecision | ContentComment | ReviewRun],
                (await s.scalars(query.order_by(model.id).limit(limit + 1))).all(),
            )
            entries = []
            for row in rows[:limit]:
                data: dict[str, object]
                if isinstance(row, PostRevision):
                    data = d.RevisionView.model_validate(row).model_dump(mode="json")
                elif isinstance(row, ContentDecision):
                    data = d.DecisionView.model_validate(row).model_dump(mode="json")
                elif isinstance(row, ContentComment):
                    data = d.CommentView.model_validate(row).model_dump(mode="json")
                else:
                    data = row.result
                entries.append(
                    d.HistoryEntry(id=row.id, created_at=row.created_at, kind=kind, data=data)
                )
            return Page(
                items=entries, next_cursor=rows[limit - 1].id if len(rows) > limit else None
            )
