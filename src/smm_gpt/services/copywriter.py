"""Read-only validation. Citation membership is not semantic entailment."""

from smm_gpt.domain.content import ProductFact, canonical_hash
from smm_gpt.domain.copywriter import CopyDraft, CopywritingContext
from smm_gpt.domain.operations import OperationError
from smm_gpt.services.knowledge_text import safe_text


def validate_context(context: CopywritingContext) -> None:
    source = context.source
    if source.revision.media_manifest or any(v.media for v in source.revision.body.variants):
        raise OperationError("copywriter_text_only_required")
    if not any(isinstance(r.body, ProductFact) for r in source.records):
        raise OperationError("copywriter_confirmed_facts_required")
    encoded = context.model_dump_json()
    if len(encoded.encode("utf-8")) > 100_000:
        raise OperationError("copywriter_context_too_large")
    safe_text(encoded)


def validate_draft(draft: CopyDraft, context: CopywritingContext) -> None:
    validate_context(context)
    safe_text(draft.model_dump_json())
    source = context.source
    if (
        draft.revision_id != source.revision.id
        or draft.content_hash != source.revision.content_hash
        or draft.context_hash != canonical_hash(context)
    ):
        raise OperationError("model_copy_binding_invalid")
    if not set(source.revision.body.knowledge_gaps) <= set(draft.knowledge_gaps):
        raise OperationError("model_copy_gaps_omitted")
    if draft.outcome == "insufficient_evidence":
        if draft.variants or not draft.knowledge_gaps:
            raise OperationError("model_copy_outcome_invalid")
        return
    indices = [v.variant_index for v in draft.variants]
    if sorted(indices) != list(range(len(source.revision.body.variants))):
        raise OperationError("model_copy_variants_invalid")
    facts = {r.id: r.body for r in source.records if isinstance(r.body, ProductFact)}
    for variant in draft.variants:
        for evidence in variant.evidence:
            fact = facts.get(evidence.fact_id)
            if fact is None or evidence.source_quote not in fact.statement:
                raise OperationError("model_copy_evidence_invalid")
            if evidence.quote not in variant.text:
                raise OperationError("model_copy_quote_invalid")
    # Unknown/uncited claims and policy compliance still require a human and normal preflight.
