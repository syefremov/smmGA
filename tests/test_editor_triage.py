from uuid import uuid4

import pytest
from pydantic import ValidationError

from smm_gpt.domain.editor_triage import DecideEditorialFinding


def command() -> DecideEditorialFinding:
    return DecideEditorialFinding(
        idempotency_key=uuid4().hex,
        artifact_id=uuid4(),
        artifact_hash="a" * 64,
        revision_id=uuid4(),
        content_hash="b" * 64,
        finding_index=0,
        finding_hash="c" * 64,
        expected_version=0,
        status="needs_changes",
        reason="Synthetic explicit human reason",
        human_confirmed=True,
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("human_confirmed", False),
        ("status", "approved"),
        ("status", "fixed"),
        ("finding_index", -1),
        ("finding_index", 20),
        ("finding_index", True),
        ("expected_version", -1),
        ("expected_version", True),
        ("reason", "  "),
        ("reason", "x" * 2001),
        ("artifact_hash", "wrong"),
        ("finding_hash", ""),
        ("content", "replacement"),
        ("tools", []),
    ],
)
def test_closed_human_triage_contract(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        DecideEditorialFinding.model_validate({**command().model_dump(), field: value})


def test_triage_requires_all_exact_bindings() -> None:
    original = command()
    for field in original.model_fields_set:
        with pytest.raises(ValidationError):
            DecideEditorialFinding.model_validate(original.model_dump(exclude={field}))
