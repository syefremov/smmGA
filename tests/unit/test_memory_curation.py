from datetime import timedelta
from uuid import uuid4

import pytest
from pydantic import TypeAdapter, ValidationError

from smm_gpt.domain import knowledge as d
from smm_gpt.infrastructure.knowledge_models import KnowledgeNote, KnowledgeNoteReview
from smm_gpt.infrastructure.models import utcnow
from smm_gpt.services.access import digest
from smm_gpt.services.knowledge import memory_context_hash


def command() -> d.CurateMemory:
    now = utcnow()
    return d.CurateMemory(
        idempotency_key=uuid4().hex,
        note_id=uuid4(),
        review_id=uuid4(),
        brand_id=uuid4(),
        context_hash="a" * 64,
        title="Synthetic reviewed lesson",
        text="Reference only",
        text_hash=digest("Reference only"),
        human_confirmed=True,
        source_date=now,
        effective_from=now,
        effective_to=now + timedelta(days=1),
    )


def test_new_reference_only_and_explicit_confirmation() -> None:
    value = command()
    parsed: d.KnowledgeCommand = TypeAdapter(d.KnowledgeCommand).validate_json(
        value.model_dump_json()
    )
    assert isinstance(parsed, d.CurateMemory)
    assert parsed.visibility == "owner" and parsed.document_type == "reference"
    assert parsed.document_id is None and parsed.expected_version == 0


@pytest.mark.parametrize(
    "update",
    [
        {"human_confirmed": False},
        {"human_confirmed": None},
        {"document_id": str(uuid4())},
        {"expected_version": 1},
        {"document_type": "product"},
        {"document_type": "brand_policy"},
        {"source_uri": "https://example.invalid/source"},
        {"context_hash": "not-hash"},
        {"text_hash": "A" * 64},
        {"tools": ["publish"]},
        {"ai_approved": True},
        {"format": "html"},
        {"visibility": "public"},
    ],
)
def test_closed_curation_contract(update: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(d.KnowledgeCommand).validate_python(command().model_dump() | update)


def test_context_hash_binds_proposal_scope_author_and_review() -> None:
    note = KnowledgeNote(
        id=uuid4(),
        workspace_id=uuid4(),
        actor_id=uuid4(),
        created_at=utcnow(),
        brand_id=uuid4(),
        kind="memory",
        text="Untrusted proposal",
        purpose="Test",
        safe_alternative="Ask owner",
        evidence_ids=[str(uuid4())],
        effective_to=utcnow() + timedelta(days=1),
    )
    review = KnowledgeNoteReview(
        id=uuid4(),
        workspace_id=note.workspace_id,
        note_id=note.id,
        actor_id=uuid4(),
        created_at=utcnow(),
        decision="accept_for_curation",
        reason="Test review",
        evidence_ids=note.evidence_ids,
    )
    original = memory_context_hash(note, review)
    assert original == memory_context_hash(note, review)
    assert original != memory_context_hash(note, None)
    for target, field, new in [
        (note, "text", "Changed"),
        (note, "brand_id", uuid4()),
        (note, "workspace_id", uuid4()),
        (note, "actor_id", uuid4()),
        (note, "evidence_ids", [str(uuid4())]),
        (note, "effective_to", utcnow() + timedelta(days=2)),
        (review, "id", uuid4()),
        (review, "actor_id", uuid4()),
        (review, "reason", "Changed"),
        (review, "decision", "reject"),
    ]:
        old = getattr(target, field)
        setattr(target, field, new)
        assert original != memory_context_hash(note, review), field
        setattr(target, field, old)
