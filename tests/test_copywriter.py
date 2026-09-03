import json
from uuid import uuid4

import httpx
import pytest
from pydantic import ValidationError

from smm_gpt.core.config import Settings
from smm_gpt.domain.ai import PROFILES
from smm_gpt.domain.content import canonical_hash
from smm_gpt.domain.copywriter import CopyDraft, RunCopyDraft
from smm_gpt.domain.operations import OperationError
from smm_gpt.services.copywriter import validate_context, validate_draft
from smm_gpt.services.model_gateway import OpenAITextGateway, copywriting_payload

from .copywriter_fixtures import context_fixture, draft_fixture


@pytest.mark.parametrize(
    "patch",
    [
        {"approved": True},
        {"tools": ["publish"]},
        {"outcome": "approved"},
        {"revision_id": "wrong"},
        {"variants": [{"text": "no identity"}]},
    ],
)
def test_copy_output_is_closed(patch: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        CopyDraft.model_validate(draft_fixture(context_fixture()).model_dump() | patch)


@pytest.mark.parametrize(
    "change",
    [
        "revision",
        "hash",
        "context",
        "fact",
        "source_quote",
        "quote",
        "index",
        "duplicate",
        "missing",
        "gaps",
        "no_draft",
        "empty_gap",
        "media",
        "no_facts",
    ],
)
def test_copy_exact_binding_and_knowledge_gaps(change: str) -> None:
    context = context_fixture()
    context.source.revision.body.knowledge_gaps = ["Missing expiry evidence"]
    draft = draft_fixture(context)
    if change == "revision":
        draft.revision_id = uuid4()
    elif change == "hash":
        draft.content_hash = "f" * 64
    elif change == "context":
        draft.context_hash = "f" * 64
    elif change == "fact":
        draft.variants[0].evidence[0].fact_id = context.source.brief.id
    elif change == "source_quote":
        draft.variants[0].evidence[0].source_quote = "Invented fact"
    elif change == "quote":
        draft.variants[0].evidence[0].quote = "Absent from proposed text"
    elif change == "index":
        draft.variants[0].variant_index = 2
    elif change == "duplicate":
        draft.variants.append(draft.variants[0])
    elif change == "missing":
        draft.variants = []
    elif change == "gaps":
        draft.knowledge_gaps = []
    elif change == "no_draft":
        draft.outcome = "insufficient_evidence"
    elif change == "empty_gap":
        context.source.revision.body.knowledge_gaps = []
        draft.context_hash = canonical_hash(context)
        draft.outcome, draft.variants, draft.knowledge_gaps = "insufficient_evidence", [], []
    elif change == "media":
        context.source.revision.media_manifest = [{"file_id": str(uuid4())}]
        draft.context_hash = canonical_hash(context)
    elif change == "no_facts":
        context.source.records = []
        draft.context_hash = canonical_hash(context)
    with pytest.raises(OperationError):
        validate_draft(draft, context)


def test_copy_valid_draft_abstention_limits_and_exact_command() -> None:
    context = context_fixture()
    draft = draft_fixture(context)
    validate_draft(draft, context)
    draft.outcome, draft.variants = "insufficient_evidence", []
    draft.knowledge_gaps = ["No evidence for requested claim"]
    validate_draft(draft, context)
    command = dict(
        idempotency_key=uuid4().hex,
        brand_id=context.source.brand_id,
        post_id=context.source.post_id,
        revision_id=draft.revision_id,
        content_hash=draft.content_hash,
        direction="Shorter",
        profile_version_id=uuid4(),
        profile_selection_id=uuid4(),
        testing_only=True,
    )
    assert RunCopyDraft.model_validate(command).profile == "copywriter"
    for field in [
        "revision_id",
        "content_hash",
        "direction",
        "profile_version_id",
        "profile_selection_id",
    ]:
        with pytest.raises(ValidationError):
            RunCopyDraft.model_validate({k: v for k, v in command.items() if k != field})
    with pytest.raises(ValidationError):
        RunCopyDraft.model_validate(command | {"direction": "x" * 501})
    context.source.records *= 1000
    with pytest.raises(OperationError, match="copywriter_context_too_large"):
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
async def test_copywriter_http_contract(outcome: str) -> None:
    context = context_fixture()
    profile = next(p for p in PROFILES if p.name == "copywriter")
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        payload = json.loads(request.content)
        assert payload == copywriting_payload(
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
            answer["revision_id"] = str(uuid4())
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
        assert (await gateway.draft(profile, context)).draft == draft_fixture(context)
    else:
        with pytest.raises(OperationError) as error:
            await gateway.draft(profile, context)
        assert "never-echo" not in str(error.value)
    assert calls == 1
