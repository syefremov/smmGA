from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

from smm_gpt.domain.ai import PROFILES, ReferenceAssessment, RunAssessment
from smm_gpt.domain.knowledge import SubmitDocument
from smm_gpt.domain.operations import OperationError
from smm_gpt.services.knowledge_text import chunks, normalize, safe_text


@pytest.mark.parametrize(
    "value",
    [
        "password: synthetic",
        "Bearer synthetic",
        "\x00binary",
        "x" * 200001,
        "postgresql://synthetic",
    ],
    ids=["credential", "bearer", "binary", "oversized", "connection"],
)
def test_unsafe_input(value: str) -> None:
    with pytest.raises(OperationError):
        safe_text(value)


def test_text_parsers_never_evaluate_or_fetch() -> None:
    assert normalize("<h1>Title</h1><p>Body &amp; more</p>", "html") == "Title\n\nBody & more"
    assert (
        normalize('Name,Value\nitem,"=WEBSERVICE(abc)"', "csv")
        == "Name | Value\n\nitem | =WEBSERVICE(abc)"
    )
    with pytest.raises(OperationError, match="active_html"):
        normalize("<script>instructions</script>", "html")
    with pytest.raises(OperationError, match="binary_parser"):
        normalize("%PDF-synthetic", "pdf")
    value = normalize("# Context\n\n" + "word " * 1000, "markdown")
    result = chunks(value)
    assert result == chunks(value)
    assert all(len(body) <= 1800 for _, body in result)
    assert all(section == "Context" for section, _ in result)


def test_contracts_and_capabilities_are_closed() -> None:
    for profile in PROFILES:
        assert "tools.call" in profile.denied_capabilities
        assert "content.approve" in profile.denied_capabilities
        expected = (
            {"content.snapshot.read", "editorial_review.propose"}
            if profile.name == "editor"
            else {"knowledge.search", "assessment.propose"}
        )
        assert set(profile.allowed_capabilities) == expected
    with pytest.raises(ValidationError):
        ReferenceAssessment.model_validate(
            {"statements": [], "hypotheses": [], "knowledge_gaps": [], "approve": True}
        )
    with pytest.raises(ValidationError):
        RunAssessment.model_validate(
            {
                "idempotency_key": "synthetic-key",
                "profile": "orchestrator",
                "brand_id": str(uuid4()),
                "question": "bypass",
                "testing_only": True,
            }
        )
    now = datetime.now(UTC)
    with pytest.raises(ValidationError):
        SubmitDocument(
            idempotency_key="synthetic-key",
            brand_id=uuid4(),
            title="Test",
            text="Text",
            source_uri="https://example.test/?token=synthetic",
            source_date=now,
            effective_from=now,
            effective_to=now + timedelta(days=1),
        )
