from uuid import uuid4

import pytest
from pydantic import ValidationError

from smm_gpt.domain.evaluation import EvalCase, EvalDefinition, EvalThresholds, acceptance_blockers


def test_benchmark_defaults_are_bounded_and_not_activation() -> None:
    definition = EvalDefinition(
        title="Synthetic",
        origin="synthetic",
        cases=[
            EvalCase(
                key="missing",
                category="no_answer",
                audience="workspace",
                query="Missing",
                expected_document_ids=[],
            )
        ],
    )
    assert set(acceptance_blockers(definition)) == {
        "synthetic_dataset",
        "at_least_eight_cases_required",
        "category_coverage_incomplete",
        "audience_coverage_incomplete",
        "forbidden_source_case_required",
    }
    assert definition.thresholds.recall == 1 and definition.limit == 5


@pytest.mark.parametrize(
    "change",
    [
        {"expected_document_ids": [uuid4()]},
        {"key": "../../unsafe"},
        {"query": " "},
        {"category": "conflict"},
        {"category": "exact"},
        {"audience": "arbitrary-user"},
        {"tools": ["publish"]},
        {"query": "x" * 501},
    ],
    ids=[
        "negative-expectation",
        "key",
        "empty-query",
        "conflict",
        "exact",
        "audience",
        "tools",
        "size",
    ],
)
def test_case_rejects_invalid_contract(change: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        EvalCase.model_validate(
            {
                "key": "test",
                "category": "no_answer",
                "audience": "workspace",
                "query": "Question",
                "expected_document_ids": [],
                **change,
            }
        )


def test_duplicate_and_contradictory_cases_are_not_coverage() -> None:
    cid = uuid4()
    case = EvalCase(
        key="exact",
        category="exact",
        audience="workspace",
        query="Product",
        expected_document_ids=[cid],
    )
    for replacement in ([cid, cid], [cid]):
        with pytest.raises(ValidationError):
            EvalCase.model_validate(
                {
                    **case.model_dump(),
                    "expected_document_ids": replacement,
                    "forbidden_document_ids": [cid],
                }
            )
    for second in (case, case.model_copy(update={"key": "second", "query": "  PRODUCT  "})):
        with pytest.raises(ValidationError):
            EvalDefinition(title="Duplicates", origin="synthetic", cases=[case, second])


@pytest.mark.parametrize(
    "threshold",
    [{"precision": 0.0}, {"recall": 0.79}, {"precision": float("nan")}, {"max_case_ms": 2001}],
)
def test_thresholds_cannot_disable_quality_gate(threshold: dict[str, float]) -> None:
    with pytest.raises(ValidationError):
        EvalThresholds.model_validate(threshold)
