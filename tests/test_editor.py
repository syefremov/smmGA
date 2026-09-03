import json
from uuid import uuid4

import httpx
import pytest
from pydantic import ValidationError

from smm_gpt.core.config import Settings
from smm_gpt.domain.ai import PROFILES
from smm_gpt.domain.content import Finding, canonical_hash
from smm_gpt.domain.editor import EditorialFinding, EditorialReview, RunEditorialReview
from smm_gpt.domain.operations import OperationError
from smm_gpt.services.editor import validate_review
from smm_gpt.services.model_gateway import OpenAITextGateway, editorial_payload

from .editor_fixtures import context_fixture, review_fixture


@pytest.mark.parametrize(
    "patch",
    [
        {"approved": True},
        {"body": {"text": "Overwrite"}},
        {"tools": ["publish"]},
        {"recommendation": "approved"},
        {"revision_id": "not-a-uuid"},
    ],
)
def test_review_output_is_closed(patch: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        EditorialReview.model_validate(review_fixture(context_fixture()).model_dump() | patch)


@pytest.mark.parametrize(
    "change", ["revision", "context", "evidence", "index", "quote", "location", "blocker", "media"]
)
def test_review_validation_exact_binding_and_no_override(change: str) -> None:
    context = context_fixture()
    if change == "blocker":
        context.preflight_findings.append(
            Finding(code="knowledge_gap", severity="blocker", location="revision")
        )
    if change == "media":
        context.revision.media_manifest.append({"file_id": str(uuid4())})
    review = review_fixture(context)
    if change == "revision":
        review.revision_id = uuid4()
    elif change == "context":
        review.context_hash = "f" * 64
    elif change == "evidence":
        review.findings[0].record_ids = [uuid4()]
    elif change == "index":
        review.findings[0].variant_index = 2
    elif change == "quote":
        review.findings[0].quote = "Invented quote"
    elif change == "location":
        review.findings[0].location = "brief"
    else:
        review.recommendation = "pass"
    with pytest.raises(OperationError):
        validate_review(review, context)


def test_finding_pass_and_command_require_exact_revision_not_working_copy() -> None:
    context = context_fixture()
    review = review_fixture(context)
    validate_review(review, context)
    review.recommendation = "pass"
    review.findings[0].severity = "blocking"
    with pytest.raises(OperationError, match="model_review_conflicts_with_checks"):
        validate_review(review, context)
    with pytest.raises(ValidationError):
        EditorialFinding.model_validate(review.findings[0].model_dump() | {"resolved": True})
    command = dict(
        idempotency_key=uuid4().hex,
        brand_id=context.brand_id,
        post_id=context.post_id,
        revision_id=context.revision.id,
        content_hash=context.revision.content_hash,
        profile_version_id=uuid4(),
        profile_selection_id=uuid4(),
        testing_only=True,
    )
    assert RunEditorialReview.model_validate(command).profile == "editor"
    for field in ["revision_id", "content_hash", "profile_version_id", "profile_selection_id"]:
        with pytest.raises(ValidationError):
            RunEditorialReview.model_validate({k: v for k, v in command.items() if k != field})


@pytest.mark.parametrize(
    "outcome", ["ok", "binding", "refusal", "tool", "timeout", "incomplete", "invalid"]
)
async def test_editorial_http_contract_and_redaction(outcome: str) -> None:
    context = context_fixture()
    profile = next(p for p in PROFILES if p.name == "editor")
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        payload = json.loads(request.content)
        assert payload == editorial_payload(
            profile, context.model_dump(mode="json"), "synthetic-model"
        )
        assert "tools" not in payload and "previous_response_id" not in payload
        assert payload["store"] is False and payload["background"] is False
        assert payload["max_output_tokens"] == 2000
        assert "Ignore instructions" not in payload["instructions"]
        assert json.loads(payload["input"])["context_hash"] == canonical_hash(context)
        schema = payload["text"]["format"]["schema"]
        assert schema["additionalProperties"] is False and payload["text"]["format"]["strict"]
        assert set(schema["required"]) == set(schema["properties"])
        if outcome == "timeout":
            raise httpx.ReadTimeout("never-echo-provider-detail")
        answer = review_fixture(context).model_dump(mode="json")
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
        assert (await gateway.review(profile, context)).review == review_fixture(context)
    else:
        with pytest.raises(OperationError) as error:
            await gateway.review(profile, context)
        assert "never-echo" not in str(error.value)
    assert calls == 1
