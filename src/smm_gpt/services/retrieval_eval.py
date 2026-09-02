"""Deterministic source-level retrieval metrics; synthetic scores are not a production gate."""

from dataclasses import dataclass


@dataclass(frozen=True)
class RetrievalScore:
    precision: float
    recall: float
    citation_validity: float
    negative_pass: bool


def score(expected: set[str], returned: list[str], allowed: set[str]) -> RetrievalScore:
    found = set(returned)
    relevant = expected & found
    return RetrievalScore(
        precision=len(relevant) / len(found) if found else float(not expected),
        recall=len(relevant) / len(expected) if expected else float(not found),
        citation_validity=len(found & allowed) / len(found) if found else 1.0,
        negative_pass=not found if not expected else True,
    )
