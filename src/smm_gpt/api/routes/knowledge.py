"""Same-origin personal-session adapter; domain services own authorization."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request

from smm_gpt.api.routes.identity import service
from smm_gpt.api.routes.operations import Actor
from smm_gpt.core.request_context import request_id
from smm_gpt.domain import ai as a
from smm_gpt.domain import knowledge as d
from smm_gpt.domain.operations import ErrorResponse, Page, PageSize
from smm_gpt.services.ai import AIService
from smm_gpt.services.knowledge import KnowledgeService

router = APIRouter(
    prefix="/workspaces/{workspace_id}/knowledge",
    responses={s: {"model": ErrorResponse} for s in (401, 403, 404, 409, 422, 429, 503)},
)


def core(request: Request) -> KnowledgeService:
    return KnowledgeService(service(request).access)


def ai_core(request: Request) -> AIService:
    session = service(request)
    return AIService(session.access, session.settings)


Core = Annotated[KnowledgeService, Depends(core)]
AI = Annotated[AIService, Depends(ai_core)]


@router.post("/commands")
async def execute(
    workspace_id: UUID, command: d.KnowledgeCommand, actor: Actor, service: Core
) -> d.KnowledgeResult:
    return await service.execute(actor, workspace_id, command, request_id())


@router.get("/documents")
async def documents(
    workspace_id: UUID,
    actor: Actor,
    service: Core,
    limit: PageSize = 25,
    cursor: UUID | None = None,
) -> Page[d.DocumentView]:
    return await service.documents(actor, workspace_id, request_id(), limit, cursor)


@router.get("/documents/{document_id}")
async def document(
    workspace_id: UUID, document_id: UUID, actor: Actor, service: Core
) -> d.DocumentDetail:
    return await service.read_document(actor, workspace_id, document_id, request_id())


@router.post("/search")
async def search(
    workspace_id: UUID, query: d.SearchRequest, actor: Actor, service: Core
) -> d.SearchResult:
    return await service.search(actor, workspace_id, query, request_id())


@router.get("/documents/{document_id}/indexes/{index_id}/chunks")
async def preview(
    workspace_id: UUID,
    document_id: UUID,
    index_id: UUID,
    actor: Actor,
    service: Core,
    limit: PageSize = 25,
    cursor: UUID | None = None,
) -> Page[d.Citation]:
    return await service.preview(
        actor, workspace_id, document_id, index_id, request_id(), limit, cursor
    )


@router.get("/notes")
async def notes(
    workspace_id: UUID,
    actor: Actor,
    service: Core,
    limit: PageSize = 25,
    cursor: UUID | None = None,
) -> Page[d.NoteView]:
    return await service.notes(actor, workspace_id, request_id(), limit, cursor)


@router.get("/profiles")
async def profiles(workspace_id: UUID, actor: Actor, service: AI) -> list[a.Profile]:
    return await service.profiles(actor, workspace_id, request_id())


@router.post("/runs")
async def run(
    workspace_id: UUID, command: a.RunAssessment, actor: Actor, service: AI
) -> a.AIRunView:
    return await service.start(actor, workspace_id, command, request_id())


@router.get("/runs")
async def runs(
    workspace_id: UUID, actor: Actor, service: AI, limit: PageSize = 25, cursor: UUID | None = None
) -> Page[a.AIRunView]:
    return await service.runs(actor, workspace_id, request_id(), limit, cursor)


@router.get("/runs/{run_id}")
async def read_run(workspace_id: UUID, run_id: UUID, actor: Actor, service: AI) -> a.AIRunView:
    return await service.read(actor, workspace_id, run_id, request_id())
