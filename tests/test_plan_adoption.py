from uuid import uuid4

import pytest
from pydantic import ValidationError

from smm_gpt.domain import content as c
from smm_gpt.domain.operations import OperationError
from smm_gpt.domain.plan_adoption import AdoptPlanDraft
from smm_gpt.services.plan_adoption import candidate

from .planner_fixtures import context_fixture, draft_fixture


def test_candidate_preserves_intent_and_all_disclosed_notes() -> None:
    context = context_fixture()
    assert isinstance(context.plan.body, c.ContentPlan)
    original = context.model_dump_json()
    draft = draft_fixture(context)
    draft.knowledge_gaps = [*context.knowledge_gaps, "Additional unresolved gap"]
    body, notes = candidate(draft, context)
    assert body.model_dump(exclude={"slots"}) == context.plan.body.model_dump(exclude={"slots"})
    for proposed, existing in zip(body.slots, context.plan.body.slots, strict=True):
        assert proposed.model_dump(exclude={"topic"}) == existing.model_dump(exclude={"topic"})
    assert notes.fact_ids == context.fact_ids
    assert notes.evidence_record_ids == sorted(r.id for r in context.records)
    assert notes.slots == draft.slots
    assert notes.warnings == draft.warnings and notes.knowledge_gaps == draft.knowledge_gaps
    assert context.direction not in notes.model_dump_json()
    assert context.model_dump_json() == original


def test_adoption_rejects_abstention_and_changed_bindings() -> None:
    context = context_fixture()
    draft = draft_fixture(context)
    draft.outcome, draft.slots = "insufficient_evidence", []
    with pytest.raises(OperationError, match="plan_adoption_draft_required"):
        candidate(draft, context)
    draft = draft_fixture(context)
    draft.slots[0].owner_id = uuid4()
    with pytest.raises(OperationError):
        candidate(draft, context)


def test_notes_preserve_all_twenty_gaps_without_truncation() -> None:
    context = context_fixture()
    draft = draft_fixture(context)
    draft.knowledge_gaps = [*context.knowledge_gaps, *[f"Gap {i}" for i in range(19)]]
    _, notes = candidate(draft, context)
    assert len(notes.knowledge_gaps) == 20 and notes.knowledge_gaps == draft.knowledge_gaps
    draft = draft_fixture(context)
    draft.knowledge_gaps = []
    with pytest.raises(OperationError):
        candidate(draft, context)


@pytest.mark.parametrize("field", ["human_confirmed", "share_with_workspace_confirmed"])
def test_command_requires_both_confirmations(field: str) -> None:
    payload: dict[str, object] = {
        "idempotency_key": uuid4().hex,
        "artifact_id": uuid4(),
        "artifact_hash": "a" * 64,
        "preview_hash": "b" * 64,
        "proposed_content_hash": "c" * 64,
        "notes_hash": "d" * 64,
        "expected_plan_number": 1,
        "reason": "Human review",
        "human_confirmed": True,
        "share_with_workspace_confirmed": True,
    }
    assert AdoptPlanDraft.model_validate(payload)
    with pytest.raises(ValidationError):
        AdoptPlanDraft.model_validate({**payload, field: False})
    with pytest.raises(ValidationError):
        AdoptPlanDraft.model_validate({**payload, "topic": "Silent edit"})
