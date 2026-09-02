"""Strict content contracts never turn malformed or AI review input into approval."""

from uuid import uuid4

import pytest
from pydantic import TypeAdapter, ValidationError

from smm_gpt.domain import content as d


def test_approval_requires_exact_confirmation_and_hash() -> None:
    command = dict(
        action="post_decide",
        post_id=uuid4(),
        expected_version=1,
        revision_id=uuid4(),
        content_hash="a" * 64,
        decision="approve",
        reason="Human review",
        human_confirmed=True,
        claims_reviewed=True,
        idempotency_key="synthetic-test",
    )
    adapter: TypeAdapter[d.ContentCommand] = TypeAdapter(d.ContentCommand)
    assert isinstance(adapter.validate_python(command), d.DecidePost)
    for patch in (
        {"human_confirmed": False},
        {"claims_reviewed": False},
        {"content_hash": "bad"},
        {"ai_approved": True},
    ):
        with pytest.raises(ValidationError):
            adapter.validate_python(command | patch)


def test_revision_hash_binds_destination_facts_and_media_order() -> None:
    a = d.RevisionBody(variants=[d.Variant(destination="vk:group:1", text="Synthetic")])
    b = d.RevisionBody(variants=[d.Variant(destination="vk:group:2", text="Synthetic")])
    assert d.canonical_hash(a) != d.canonical_hash(b)
    assert d.canonical_hash(
        {"body": a.model_dump(mode="json"), "media_manifest": []}
    ) != d.canonical_hash(a)
    with pytest.raises(ValidationError):
        d.RevisionBody(variants=[a.variants[0], a.variants[0]])
    with pytest.raises(ValidationError):
        d.Variant(destination="https://arbitrary.example/", text="x")
