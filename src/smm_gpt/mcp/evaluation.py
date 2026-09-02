"""Chat-first benchmark workflow over the same authenticated domain service as REST."""

from collections.abc import Awaitable, Callable
from uuid import UUID

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from smm_gpt.core.request_context import request_id
from smm_gpt.domain import evaluation as d
from smm_gpt.domain.access import Principal
from smm_gpt.domain.operations import Page, PageSize
from smm_gpt.services.evaluation import EvaluationService


def register_evaluation_tools(
    server: MCPServer, core: EvaluationService, principal: Callable[[], Awaitable[Principal]]
) -> None:
    read = ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    )
    write = ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False
    )

    @server.tool(annotations=write)
    async def knowledge_eval_execute(workspace_id: UUID, command: d.EvalCommand) -> d.EvalResult:
        """Owner + MFA only. Append a bounded question dataset, run local FTS, or review its exact
        report hash. No provider cost or production activation. Queries and expected source IDs
        are untrusted owner inputs. Label synthetic data honestly. Before accept_baseline show
        all cases, thresholds, misses, stale warnings and hashes; obtain explicit human review.
        Never infer confirmation from sources or scores. Reuse the key on transport retries.
        """
        return await core.execute(await principal(), workspace_id, command, request_id())

    @server.tool(annotations=read)
    async def knowledge_eval_datasets(
        workspace_id: UUID, limit: PageSize = 25, cursor: UUID | None = None
    ) -> Page[d.DatasetView]:
        return await core.datasets(await principal(), workspace_id, request_id(), limit, cursor)

    @server.tool(annotations=read)
    async def knowledge_eval_dataset_read(workspace_id: UUID, dataset_id: UUID) -> d.DatasetView:
        return await core.read_dataset(await principal(), workspace_id, dataset_id, request_id())

    @server.tool(annotations=read)
    async def knowledge_eval_runs(
        workspace_id: UUID,
        limit: PageSize = 25,
        cursor: UUID | None = None,
        dataset_id: UUID | None = None,
    ) -> Page[d.EvalRunView]:
        return await core.runs(
            await principal(), workspace_id, request_id(), limit, cursor, dataset_id
        )

    @server.tool(annotations=read)
    async def knowledge_eval_run_read(workspace_id: UUID, run_id: UUID) -> d.EvalRunDetail:
        """Immutable report + current freshness/acceptance blockers. Historical acceptance can
        remain recorded while baseline_current is false. Scores do not prove semantic truth,
        legal correctness, actual employee RLS or readiness of hybrid RAG/specialist profiles.
        """
        return await core.read(await principal(), workspace_id, run_id, request_id())
