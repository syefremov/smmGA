"""Personal-session file transport. Original bytes are attachments, never inline markup."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response

from smm_gpt.api.routes.identity import service
from smm_gpt.api.routes.operations import Actor
from smm_gpt.core.request_context import request_id
from smm_gpt.domain import knowledge_files as d
from smm_gpt.domain.operations import ErrorResponse, Page, PageSize
from smm_gpt.services.knowledge_files import KnowledgeFileService

router = APIRouter(
    prefix="/workspaces/{workspace_id}/knowledge/files",
    responses={s: {"model": ErrorResponse} for s in (401, 403, 404, 409, 413, 422, 429, 503)},
)


def core(request: Request) -> KnowledgeFileService:
    session = service(request)
    return KnowledgeFileService(session.access, session.settings)


Core = Annotated[KnowledgeFileService, Depends(core)]


@router.post("")
async def submit(
    workspace_id: UUID, command: d.SubmitFile, actor: Actor, service: Core
) -> d.FileReceipt:
    return await service.submit(actor, workspace_id, command, request_id())


@router.get("")
async def files(
    workspace_id: UUID,
    actor: Actor,
    service: Core,
    limit: PageSize = 25,
    cursor: UUID | None = None,
) -> Page[d.FileView]:
    return await service.files(actor, workspace_id, request_id(), limit, cursor)


@router.post("/retry")
async def retry(
    workspace_id: UUID, command: d.RetryFile, actor: Actor, service: Core
) -> d.FileReceipt:
    return await service.retry(actor, workspace_id, command, request_id())


@router.post("/rescan")
async def rescan(
    workspace_id: UUID, command: d.RescanFile, actor: Actor, service: Core
) -> d.FileReceipt:
    return await service.rescan(actor, workspace_id, command, request_id())


@router.get("/{file_id}")
async def read(workspace_id: UUID, file_id: UUID, actor: Actor, service: Core) -> d.FileDetail:
    return await service.read(actor, workspace_id, file_id, request_id())


@router.get(
    "/{file_id}/original",
    response_class=Response,
    responses={
        200: {
            "content": {
                "application/octet-stream": {"schema": {"type": "string", "format": "binary"}}
            }
        }
    },
)
async def original(workspace_id: UUID, file_id: UUID, actor: Actor, service: Core) -> Response:
    data, format = await service.download(actor, workspace_id, file_id, request_id())
    return Response(
        data,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="{file_id}.{format}"',
            "Content-Security-Policy": "sandbox; default-src 'none'",
        },
    )
