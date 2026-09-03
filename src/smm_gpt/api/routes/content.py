"""Authenticated REST adapter for the shared manual content lifecycle."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request

from smm_gpt.api.routes.identity import service
from smm_gpt.api.routes.operations import Actor
from smm_gpt.core.request_context import request_id
from smm_gpt.domain import content as d
from smm_gpt.domain.operations import ErrorResponse, Page, PageSize
from smm_gpt.domain.plan_adoption import PlanNotesView
from smm_gpt.services.content import ContentService
from smm_gpt.services.plan_notes import PlanNotesService

router = APIRouter(
    prefix="/workspaces/{workspace_id}/content",
    responses={s: {"model": ErrorResponse} for s in (401, 403, 404, 409, 422, 503)},
)


def content_service(request: Request) -> ContentService:
    return ContentService(service(request).access)


Core = Annotated[ContentService, Depends(content_service)]


@router.post("/commands")
async def execute(
    workspace_id: UUID, command: d.ContentCommand, actor: Actor, core: Core
) -> d.CommandResult:
    return await core.execute(actor, workspace_id, command, request_id())


@router.get("/records")
async def records(
    workspace_id: UUID,
    actor: Actor,
    core: Core,
    kind: d.RecordKind | None = None,
    limit: PageSize = 25,
    cursor: UUID | None = None,
) -> Page[d.RecordView]:
    return await core.records(actor, workspace_id, request_id(), kind, limit, cursor)


@router.get("/records/{record_id}")
async def record(workspace_id: UUID, record_id: UUID, actor: Actor, core: Core) -> d.RecordView:
    return await core.read_record(actor, workspace_id, record_id, request_id())


@router.get("/records/{record_id}/plan-notes")
async def plan_notes(
    workspace_id: UUID, record_id: UUID, actor: Actor, core: Core
) -> PlanNotesView | None:
    return await PlanNotesService(core.access).read(actor, workspace_id, record_id, request_id())


@router.get("/posts")
async def posts(
    workspace_id: UUID,
    actor: Actor,
    core: Core,
    state: d.PostState | None = None,
    limit: PageSize = 25,
    cursor: UUID | None = None,
) -> Page[d.PostSummary]:
    return await core.posts(actor, workspace_id, request_id(), state, limit, cursor)


@router.get("/posts/{post_id}")
async def post(workspace_id: UUID, post_id: UUID, actor: Actor, core: Core) -> d.PostView:
    return await core.read_post(actor, workspace_id, post_id, request_id())


@router.get("/posts/{post_id}/preflight")
async def preflight(workspace_id: UUID, post_id: UUID, actor: Actor, core: Core) -> d.Preflight:
    return await core.check(actor, workspace_id, post_id, request_id())


@router.get("/posts/{post_id}/working-copy")
async def working_copy(
    workspace_id: UUID, post_id: UUID, actor: Actor, core: Core
) -> d.WorkingCopyView | None:
    return await core.working_copy(actor, workspace_id, post_id, request_id())


@router.get("/packages")
async def packages(
    workspace_id: UUID, actor: Actor, core: Core, limit: PageSize = 25, cursor: UUID | None = None
) -> Page[d.PackageSummary]:
    return await core.packages(actor, workspace_id, request_id(), limit, cursor)


@router.get("/packages/{package_id}")
async def package(workspace_id: UUID, package_id: UUID, actor: Actor, core: Core) -> d.PackageView:
    return await core.read_package(actor, workspace_id, package_id, request_id())


@router.get("/tasks/{item_id}")
async def task(workspace_id: UUID, item_id: UUID, actor: Actor, core: Core) -> d.TaskContext:
    return await core.task_context(actor, workspace_id, item_id, request_id())


@router.get("/posts/{post_id}/history/{kind}")
async def history(
    workspace_id: UUID,
    post_id: UUID,
    kind: d.HistoryKind,
    actor: Actor,
    core: Core,
    limit: PageSize = 10,
    cursor: UUID | None = None,
) -> Page[d.HistoryEntry]:
    return await core.history(actor, workspace_id, post_id, request_id(), kind, limit, cursor)
