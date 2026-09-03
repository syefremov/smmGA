"""Same-origin personal-session adapter; domain services own authorization."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request

from smm_gpt.api.routes.identity import service
from smm_gpt.api.routes.operations import Actor
from smm_gpt.core.request_context import request_id
from smm_gpt.domain import ai as a
from smm_gpt.domain import ai_costs as costs
from smm_gpt.domain import copy_adoption as adoption
from smm_gpt.domain import editor_triage as t
from smm_gpt.domain import evaluation as e
from smm_gpt.domain import ingestion as j
from smm_gpt.domain import knowledge as d
from smm_gpt.domain import plan_adoption as plan
from smm_gpt.domain import profiles as p
from smm_gpt.domain.copywriter import RunCopyDraft
from smm_gpt.domain.editor import RunEditorialReview
from smm_gpt.domain.operations import ErrorResponse, Page, PageSize
from smm_gpt.domain.planner import RunPlanDraft
from smm_gpt.infrastructure.file_storage import VolumeFileStore
from smm_gpt.services.ai import AIService
from smm_gpt.services.ai_costs import CostService
from smm_gpt.services.copy_adoption import CopyAdoptionService
from smm_gpt.services.editor_triage import EditorTriageService
from smm_gpt.services.evaluation import EvaluationService
from smm_gpt.services.ingestion import IngestionService
from smm_gpt.services.knowledge import KnowledgeService
from smm_gpt.services.plan_adoption import PlanAdoptionService
from smm_gpt.services.profiles import ProfileService

router = APIRouter(
    prefix="/workspaces/{workspace_id}/knowledge",
    responses={s: {"model": ErrorResponse} for s in (401, 403, 404, 409, 422, 429, 503)},
)


def core(request: Request) -> KnowledgeService:
    session = service(request)
    return KnowledgeService(session.access, VolumeFileStore(session.settings.media_root))


def ai_core(request: Request) -> AIService:
    session = service(request)
    return AIService(session.access, session.settings)


Core = Annotated[KnowledgeService, Depends(core)]
AI = Annotated[AIService, Depends(ai_core)]


@router.get("/costs")
async def cost_summary(workspace_id: UUID, actor: Actor, service: AI) -> costs.CostSummary:
    return await CostService(service.access, service.settings).summary(
        actor, workspace_id, request_id()
    )


@router.get("/runs/{run_id}/cost")
async def cost_receipt(
    workspace_id: UUID, run_id: UUID, actor: Actor, service: AI
) -> costs.CostReceipt:
    return await CostService(service.access, service.settings).receipt(
        actor, workspace_id, run_id, request_id()
    )


def eval_core(request: Request) -> EvaluationService:
    return EvaluationService(service(request).access)


Evals = Annotated[EvaluationService, Depends(eval_core)]


def ingestion_core(request: Request) -> IngestionService:
    return IngestionService(service(request).access)


Jobs = Annotated[IngestionService, Depends(ingestion_core)]


def profile_core(request: Request) -> ProfileService:
    return ProfileService(service(request).access)


Profiles = Annotated[ProfileService, Depends(profile_core)]


def triage_core(request: Request) -> EditorTriageService:
    return EditorTriageService(service(request).access)


Triage = Annotated[EditorTriageService, Depends(triage_core)]


def adoption_core(request: Request) -> CopyAdoptionService:
    return CopyAdoptionService(service(request).access)


Adoption = Annotated[CopyAdoptionService, Depends(adoption_core)]


def plan_adoption_core(request: Request) -> PlanAdoptionService:
    return PlanAdoptionService(service(request).access)


PlanningAdoption = Annotated[PlanAdoptionService, Depends(plan_adoption_core)]


@router.get("/runs/{run_id}/plan-adoption/preview")
async def plan_adoption_preview(
    workspace_id: UUID, run_id: UUID, actor: Actor, service: PlanningAdoption
) -> plan.PlanAdoptionPreview:
    return await service.preview(actor, workspace_id, run_id, request_id())


@router.get("/runs/{run_id}/plan-adoption")
async def plan_adoption_read(
    workspace_id: UUID, run_id: UUID, actor: Actor, service: PlanningAdoption
) -> plan.PlanAdoptionView | None:
    return await service.read(actor, workspace_id, run_id, request_id())


@router.post("/runs/{run_id}/plan-adoption")
async def plan_adopt(
    workspace_id: UUID,
    run_id: UUID,
    command: plan.AdoptPlanDraft,
    actor: Actor,
    service: PlanningAdoption,
) -> plan.PlanAdoptionView:
    return await service.adopt(actor, workspace_id, run_id, command, request_id())


@router.get("/runs/{run_id}/copy-adoption/preview")
async def copy_adoption_preview(
    workspace_id: UUID, run_id: UUID, actor: Actor, service: Adoption
) -> adoption.CopyAdoptionPreview:
    return await service.preview(actor, workspace_id, run_id, request_id())


@router.get("/runs/{run_id}/copy-adoption")
async def copy_adoption_read(
    workspace_id: UUID, run_id: UUID, actor: Actor, service: Adoption
) -> adoption.CopyAdoptionView | None:
    return await service.read(actor, workspace_id, run_id, request_id())


@router.post("/runs/{run_id}/copy-adoption")
async def copy_adopt(
    workspace_id: UUID,
    run_id: UUID,
    command: adoption.AdoptCopyDraft,
    actor: Actor,
    service: Adoption,
) -> adoption.CopyAdoptionView:
    return await service.adopt(actor, workspace_id, run_id, command, request_id())


@router.get("/runs/{run_id}/editor-triage")
async def editorial_triage(
    workspace_id: UUID, run_id: UUID, actor: Actor, service: Triage
) -> t.EditorialTriageView:
    return await service.read(actor, workspace_id, run_id, request_id())


@router.post("/runs/{run_id}/editor-triage")
async def editorial_decide(
    workspace_id: UUID,
    run_id: UUID,
    command: t.DecideEditorialFinding,
    actor: Actor,
    service: Triage,
) -> t.EditorialDecisionReceipt:
    return await service.decide(actor, workspace_id, run_id, command, request_id())


@router.get("/runs/{run_id}/editor-triage/history")
async def editorial_history(
    workspace_id: UUID,
    run_id: UUID,
    actor: Actor,
    service: Triage,
    before: t.HistoryCursor | None = None,
) -> t.EditorialHistory:
    return await service.history(actor, workspace_id, run_id, request_id(), before)


@router.post("/profile-registry/commands")
async def profile_execute(
    workspace_id: UUID, command: p.ProfileCommand, actor: Actor, service: Profiles
) -> p.ProfileReceipt:
    return await service.execute(actor, workspace_id, command, request_id())


@router.get("/profile-registry")
async def profile_registry(
    workspace_id: UUID, actor: Actor, service: Profiles
) -> list[p.RegisteredProfile]:
    return await service.registry(actor, workspace_id, request_id())


@router.get("/profile-registry/versions/{version_id}")
async def profile_version(
    workspace_id: UUID, version_id: UUID, actor: Actor, service: Profiles
) -> p.ProfileVersionView:
    return await service.read_version(actor, workspace_id, version_id, request_id())


@router.get("/profile-registry/{profile}")
async def profile_detail(
    workspace_id: UUID, profile: a.ProfileName, actor: Actor, service: Profiles
) -> p.ProfileDetail:
    return await service.read(actor, workspace_id, profile, request_id())


@router.get("/jobs")
async def ingestion_jobs(
    workspace_id: UUID,
    kind: j.JobKind,
    actor: Actor,
    service: Jobs,
    limit: PageSize = 25,
    cursor: UUID | None = None,
) -> Page[j.IngestionJob]:
    return await service.jobs(actor, workspace_id, kind, request_id(), limit, cursor)


@router.post("/jobs/cancel")
async def ingestion_cancel(
    workspace_id: UUID,
    command: j.CancelIngestion,
    actor: Actor,
    service: Jobs,
) -> j.IngestionReceipt:
    return await service.cancel(actor, workspace_id, command, request_id())


@router.get("/jobs/{kind}/{job_id}/history")
async def ingestion_history(
    workspace_id: UUID,
    kind: j.JobKind,
    job_id: UUID,
    actor: Actor,
    service: Jobs,
) -> j.IngestionHistory:
    return await service.history(actor, workspace_id, kind, job_id, request_id())


@router.post("/evaluations/commands")
async def evaluate(
    workspace_id: UUID, command: e.EvalCommand, actor: Actor, service: Evals
) -> e.EvalResult:
    return await service.execute(actor, workspace_id, command, request_id())


@router.get("/evaluations/datasets")
async def eval_datasets(
    workspace_id: UUID,
    actor: Actor,
    service: Evals,
    limit: PageSize = 25,
    cursor: UUID | None = None,
) -> Page[e.DatasetView]:
    return await service.datasets(actor, workspace_id, request_id(), limit, cursor)


@router.get("/evaluations/datasets/{dataset_id}")
async def eval_dataset(
    workspace_id: UUID, dataset_id: UUID, actor: Actor, service: Evals
) -> e.DatasetView:
    return await service.read_dataset(actor, workspace_id, dataset_id, request_id())


@router.get("/evaluations/runs")
async def eval_runs(
    workspace_id: UUID,
    actor: Actor,
    service: Evals,
    limit: PageSize = 25,
    cursor: UUID | None = None,
    dataset_id: UUID | None = None,
) -> Page[e.EvalRunView]:
    return await service.runs(actor, workspace_id, request_id(), limit, cursor, dataset_id)


@router.get("/evaluations/runs/{run_id}")
async def eval_run(
    workspace_id: UUID, run_id: UUID, actor: Actor, service: Evals
) -> e.EvalRunDetail:
    return await service.read(actor, workspace_id, run_id, request_id())


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


@router.get("/notes/{note_id}")
async def note_detail(
    workspace_id: UUID, note_id: UUID, actor: Actor, service: Core
) -> d.NoteDetail:
    return await service.read_note(actor, workspace_id, note_id, request_id())


@router.get("/documents/{document_id}/memory-origin")
async def memory_origin(
    workspace_id: UUID, document_id: UUID, actor: Actor, service: Core
) -> d.MemoryDocumentView:
    return await service.memory_origin(actor, workspace_id, document_id, request_id())


@router.post("/runs")
async def run(
    workspace_id: UUID, command: a.RunAssessment, actor: Actor, service: AI
) -> a.AIRunView:
    return await service.start(actor, workspace_id, command, request_id())


@router.post("/editor-runs")
async def editorial_run(
    workspace_id: UUID,
    command: RunEditorialReview,
    actor: Actor,
    service: AI,
) -> a.AIRunView:
    return await service.start(actor, workspace_id, command, request_id())


@router.post("/copywriter-runs")
async def copywriter_run(
    workspace_id: UUID, command: RunCopyDraft, actor: Actor, service: AI
) -> a.AIRunView:
    return await service.start(actor, workspace_id, command, request_id())


@router.post("/planner-runs")
async def planner_run(
    workspace_id: UUID, command: RunPlanDraft, actor: Actor, service: AI
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


@router.get("/runs/{run_id}/inputs")
async def read_inputs(workspace_id: UUID, run_id: UUID, actor: Actor, service: AI) -> a.AIInputView:
    return await service.inputs(actor, workspace_id, run_id, request_id())


@router.post("/runs/{run_id}/cancel")
async def cancel_run(
    workspace_id: UUID, run_id: UUID, command: a.CancelAssessment, actor: Actor, service: AI
) -> a.AICancelReceipt:
    return await service.cancel(actor, workspace_id, run_id, command, request_id())
