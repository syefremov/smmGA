"""Versioned REST adapter; business decisions belong to Operations."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response

from smm_gpt.api.routes.identity import principal, service
from smm_gpt.core.request_context import request_id
from smm_gpt.domain.access import Principal
from smm_gpt.domain.operations import (
    AuditView,
    CatalogKind,
    CatalogView,
    CreateWorkItem,
    ErrorResponse,
    Page,
    PageSize,
    SessionView,
    TransitionWorkItem,
    WorkItemView,
    WorkState,
)
from smm_gpt.services.operations import Operations

router = APIRouter(responses={s: {"model": ErrorResponse} for s in (401, 403, 404, 409, 422, 503)})
Actor = Annotated[Principal, Depends(principal)]


def operations(request: Request) -> Operations:
    return Operations(service(request).access)


Core = Annotated[Operations, Depends(operations)]


@router.get("/session")
async def session(actor: Actor, core: Core) -> SessionView:
    return await core.session(actor)


@router.get("/workspaces/{workspace_id}/catalog/{kind}")
async def catalog(
    workspace_id: UUID,
    kind: CatalogKind,
    actor: Actor,
    core: Core,
    limit: PageSize = 25,
    cursor: UUID | None = None,
) -> Page[CatalogView]:
    return await core.catalog(actor, workspace_id, kind, request_id(), limit, cursor)


@router.get("/workspaces/{workspace_id}/work-items")
async def work_list(
    workspace_id: UUID,
    actor: Actor,
    core: Core,
    limit: PageSize = 25,
    cursor: UUID | None = None,
    state: WorkState | None = None,
) -> Page[WorkItemView]:
    return await core.list_work(actor, workspace_id, request_id(), limit, cursor, state)


@router.get("/workspaces/{workspace_id}/work-items/{item_id}")
async def work_read(
    workspace_id: UUID, item_id: UUID, actor: Actor, core: Core, response: Response
) -> WorkItemView:
    result = await core.read_work(actor, workspace_id, item_id, request_id())
    response.headers["ETag"] = f'"{result.version}"'
    return result


@router.post("/workspaces/{workspace_id}/work-items", status_code=201)
async def work_create(
    workspace_id: UUID, command: CreateWorkItem, actor: Actor, core: Core
) -> WorkItemView:
    return await core.create_work(actor, workspace_id, command, request_id())


@router.post("/workspaces/{workspace_id}/work-items/{item_id}/transition")
async def work_transition(
    workspace_id: UUID,
    item_id: UUID,
    command: TransitionWorkItem,
    actor: Actor,
    core: Core,
    response: Response,
) -> WorkItemView:
    result = await core.transition_work(actor, workspace_id, item_id, command, request_id())
    response.headers["ETag"] = f'"{result.version}"'
    return result


@router.get("/workspaces/{workspace_id}/audit")
async def audit_read(
    workspace_id: UUID,
    actor: Actor,
    core: Core,
    limit: PageSize = 25,
    cursor: UUID | None = None,
    target: UUID | None = None,
) -> Page[AuditView]:
    return await core.audit_log(actor, workspace_id, request_id(), limit, cursor, target)
