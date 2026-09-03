from uuid import uuid4

import pytest
from pydantic import ValidationError

from smm_gpt.domain.content import canonical_hash
from smm_gpt.domain.copy_adoption import AdoptCopyDraft
from smm_gpt.domain.operations import OperationError
from smm_gpt.services.copy_adoption import candidate_body

from .copywriter_fixtures import context_fixture, draft_fixture


def test_adoption_preserves_destinations_facts_gaps_without_mutating_inputs() -> None:
    context = context_fixture()
    context.source.revision.body.knowledge_gaps = ["Original gap"]
    context.source.revision.body.variants.append(
        context.source.revision.body.variants[0].model_copy(
            update={"destination": "vk:group:456"},
        )
    )
    draft = draft_fixture(context)
    draft.knowledge_gaps = ["Original gap", "AI gap"]
    draft.variants[0].text = "New " + draft.variants[0].text
    # Order of model output does not swap destinations.
    draft.variants.reverse()
    saved_context, saved_draft = context.model_dump(), draft.model_dump()
    result = candidate_body(draft, context)
    assert result.variants[0].text.startswith("New ")
    assert result.variants[0].destination == "vk:group:123"
    assert result.variants[1].destination == "vk:group:456"
    assert result.fact_ids == context.source.revision.body.fact_ids
    assert result.knowledge_gaps == draft.knowledge_gaps
    assert context.model_dump() == saved_context and draft.model_dump() == saved_draft


@pytest.mark.parametrize("change", ["abstain", "too_many_gaps", "unmapped_fact", "binding"])
def test_adoption_rejects_unrepresentable_or_unbound_candidate(change: str) -> None:
    context = context_fixture()
    draft = draft_fixture(context)
    if change == "abstain":
        draft.outcome, draft.variants, draft.knowledge_gaps = (
            "insufficient_evidence",
            [],
            ["Missing fact"],
        )
    elif change == "too_many_gaps":
        draft.knowledge_gaps = [f"Gap {i}" for i in range(21)]
    elif change == "unmapped_fact":
        context.source.revision.body.fact_ids = []
        draft.context_hash = canonical_hash(context)
    else:
        draft.revision_id = uuid4()
    with pytest.raises(OperationError):
        candidate_body(draft, context)


@pytest.mark.parametrize(
    "change",
    [
        {"human_confirmed": False},
        {"share_with_workspace_confirmed": False},
        {"body": {}},
        {"approved": True},
        {"expected_post_version": True},
        {"preview_hash": "bad"},
    ],
)
def test_adoption_command_is_closed_and_requires_two_confirmations(
    change: dict[str, object],
) -> None:
    values = dict(
        idempotency_key=uuid4().hex,
        artifact_id=uuid4(),
        artifact_hash="a" * 64,
        preview_hash="b" * 64,
        proposed_content_hash="c" * 64,
        expected_post_version=2,
        reason="Explicit save and workspace sharing",
        human_confirmed=True,
        share_with_workspace_confirmed=True,
    )
    assert AdoptCopyDraft.model_validate(values)
    with pytest.raises(ValidationError):
        AdoptCopyDraft.model_validate(values | change)
    for name in [
        "preview_hash",
        "proposed_content_hash",
        "human_confirmed",
        "share_with_workspace_confirmed",
    ]:
        with pytest.raises(ValidationError):
            AdoptCopyDraft.model_validate({k: v for k, v in values.items() if k != name})
