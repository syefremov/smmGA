import json
from datetime import timedelta
from uuid import uuid4

import httpx
import pytest
from pydantic import ValidationError

from smm_gpt.core.config import Settings
from smm_gpt.domain.ai import PROFILES
from smm_gpt.domain.content import canonical_hash
from smm_gpt.domain.operations import OperationError
from smm_gpt.domain.planner import PlanDraft, RunPlanDraft
from smm_gpt.services.model_gateway import OpenAITextGateway, planning_payload
from smm_gpt.services.planner import validate_context, validate_draft

from .planner_fixtures import context_fixture, draft_fixture


@pytest.mark.parametrize(
    "patch",
    [
        {"approved": True},
        {"tools": ["publish"]},
        {"outcome": "approved"},
        {"slots": [{"topic": "unbound"}]},
        {"plan_id": "wrong"},
    ],
)
def test_plan_output_is_closed(patch: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        PlanDraft.model_validate(draft_fixture(context_fixture()).model_dump() | patch)


@pytest.mark.parametrize(
    "change",
    [
        "plan",
        "hash",
        "context",
        "fact",
        "quote",
        "source_quote",
        "index",
        "duplicate",
        "missing",
        "gaps",
        "no_draft",
        "empty_gap",
        "date",
        "destination",
        "owner",
        "no_facts",
        "integrity",
        "unconfirmed",
        "brand",
    ],
)
def test_plan_exact_bindings_and_gaps(change: str) -> None:
    context = context_fixture()
    draft = draft_fixture(context)
    if change == "plan":
        draft.plan_id = uuid4()
    elif change == "hash":
        draft.content_hash = "f" * 64
    elif change == "context":
        draft.context_hash = "f" * 64
    elif change == "fact":
        draft.slots[0].evidence[0].fact_id = context.plan.id
    elif change == "quote":
        draft.slots[0].evidence[0].quote = "Not in proposal"
    elif change == "source_quote":
        draft.slots[0].evidence[0].source_quote = "Invented fact"
    elif change == "index":
        draft.slots[0].slot_index = 2
    elif change == "duplicate":
        draft.slots.append(draft.slots[0])
    elif change == "missing":
        draft.slots = []
    elif change == "gaps":
        draft.knowledge_gaps = []
    elif change == "no_draft":
        draft.outcome = "insufficient_evidence"
    elif change == "empty_gap":
        context.knowledge_gaps = []
        draft.context_hash = canonical_hash(context)
        draft.outcome, draft.slots, draft.knowledge_gaps = "insufficient_evidence", [], []
    elif change == "date":
        draft.slots[0].planned_at += timedelta(minutes=1)
    elif change == "destination":
        draft.slots[0].destination = "vk:group:999"
    elif change == "owner":
        draft.slots[0].owner_id = uuid4()
    elif change == "no_facts":
        context.records = []
    elif change == "integrity":
        context.plan.content_hash = "f" * 64
    elif change == "unconfirmed":
        context.records[0].confirmed_by = None
    elif change == "brand":
        context.brand_id = uuid4()
    with pytest.raises(OperationError):
        validate_draft(draft, context)


def test_plan_valid_draft_abstention_limits_and_command() -> None:
    context = context_fixture()
    draft = draft_fixture(context)
    validate_draft(draft, context)
    draft.outcome, draft.slots = "insufficient_evidence", []
    validate_draft(draft, context)
    command = dict(
        idempotency_key=uuid4().hex,
        brand_id=context.brand_id,
        plan_id=context.plan.id,
        content_hash=context.plan.content_hash,
        fact_ids=context.fact_ids,
        direction=context.direction,
        knowledge_gaps=context.knowledge_gaps,
        profile_version_id=uuid4(),
        profile_selection_id=uuid4(),
        testing_only=True,
    )
    assert RunPlanDraft.model_validate(command).profile == "content_planner"
    for field in command:
        with pytest.raises(ValidationError):
            RunPlanDraft.model_validate({k: v for k, v in command.items() if k != field})
    patches: list[dict[str, object]] = [
        {"fact_ids": []},
        {"fact_ids": context.fact_ids * 2},
        {"direction": "x" * 501},
    ]
    for patch in patches:
        with pytest.raises(ValidationError):
            RunPlanDraft.model_validate(command | patch)
    context.records *= 1000
    with pytest.raises(OperationError, match="planner_context_too_large"):
        validate_context(context)


@pytest.mark.parametrize(
    "outcome",
    [
        "ok",
        "binding",
        "refusal",
        "tool",
        "timeout",
        "incomplete",
        "invalid",
        "rate_limit",
    ],
)
async def test_planner_http_contract(outcome: str) -> None:
    context = context_fixture()
    profile = next(p for p in PROFILES if p.name == "content_planner")
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        payload = json.loads(request.content)
        assert payload == planning_payload(
            profile, context.model_dump(mode="json"), "synthetic-model"
        )
        assert "tools" not in payload and "previous_response_id" not in payload
        assert not payload["store"] and not payload["background"]
        assert context.direction not in payload["instructions"]
        assert json.loads(payload["input"])["context_hash"] == canonical_hash(context)
        schema = payload["text"]["format"]["schema"]
        for definition in [schema, *schema["$defs"].values()]:
            assert definition["additionalProperties"] is False
            assert set(definition["required"]) == set(definition["properties"])
        if outcome == "timeout":
            raise httpx.ReadTimeout("never-echo-provider-detail")
        if outcome == "rate_limit":
            return httpx.Response(429, text="never-echo-provider-detail")
        answer = draft_fixture(context).model_dump(mode="json")
        if outcome == "binding":
            answer["plan_id"] = str(uuid4())
        if outcome == "invalid":
            answer["approved"] = True
        return httpx.Response(
            200,
            json={
                "id": "synthetic-response",
                "model": "synthetic-model",
                "status": "incomplete" if outcome == "incomplete" else "completed",
                "usage": {"input_tokens": 20, "output_tokens": 10},
                "output": [
                    {
                        "type": "function_call" if outcome == "tool" else "message",
                        "content": [
                            {
                                "type": "refusal" if outcome == "refusal" else "output_text",
                                "text": json.dumps(answer),
                            }
                        ],
                    }
                ],
            },
        )

    cfg = Settings(
        _env_file=None,
        ai_provider="openai",
        ai_model="synthetic-model",
        ai_api_key="test-only",
        ai_allowed_workspaces=(uuid4(),),
    )
    gateway = OpenAITextGateway(cfg, httpx.MockTransport(handler))
    if outcome == "ok":
        assert (await gateway.plan(profile, context)).draft == draft_fixture(context)
    else:
        with pytest.raises(OperationError) as error:
            await gateway.plan(profile, context)
        assert "never-echo" not in str(error.value)
    assert calls == 1
