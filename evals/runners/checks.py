"""Individual check functions, one per metric type declared in the golden
set's `checks:` field. Each returns a dict with at minimum `passed: bool`
and enough raw detail to debug a failure without re-running anything --
half of today's real findings came from reading raw output, not summaries.
"""

from __future__ import annotations

from datasets import Dataset
from ragas import evaluate
from ragas.metrics import faithfulness as ragas_faithfulness

from app.rag.eval_metrics import check_date_bindings
from app.rag.generate import GeneratedAnswer, _render_context
from app.rag.retrieval import RetrievalResult


async def check_faithfulness(question: str, answer: GeneratedAnswer, result: RetrievalResult) -> dict:
    context_text = _render_context(result)
    contexts = [c.strip() for c in context_text.split("<<<DOC") if c.strip()]
    if not contexts:
        return {"passed": None, "score": None, "note": "no context to evaluate against"}

    dataset = Dataset.from_dict({
        "question": [question], "answer": [answer.text], "contexts": [contexts],
    })
    scores = evaluate(dataset, metrics=[ragas_faithfulness])
    score = scores["faithfulness"][0]
    return {
        "passed": score >= 0.7,  # threshold, not a claim of ground truth --
        "score": score,          # see note below
        "note": (
            "CAUTION: faithfulness measures 'is this traceable to context', "
            "NOT correctness. Known to score 1.0 on our captured "
            "date-misattribution case. A pass here does not mean the "
            "answer is factually correct -- pair with date_binding for "
            "diachronic questions."
        ),
    }


async def check_date_binding(question: str, answer: GeneratedAnswer, result: RetrievalResult) -> dict:
    if not result.lineages:
        return {"passed": None, "note": "no lineage data -- not a diachronic case"}

    all_versions = [v for versions in result.lineages.values() for v in versions]
    bindings = check_date_bindings(answer.text, all_versions)

    # A claim with a real date but unverified content is a candidate
    # misattribution. Known limitation (logged in golden_set.yaml):
    # negation claims ("X was absent") also read as unverified, so this
    # flags candidates for review, it doesn't prove a hallucination alone.
    suspect = [b for b in bindings if b.date_exists_in_lineage and b.content_verified is False]
    return {
        "passed": len(suspect) == 0,
        "total_date_claims": len(bindings),
        "suspect_claims": [
            {"date": b.claimed_date, "sentence": b.sentence} for b in suspect
        ],
        "note": "Flags candidates; verify manually, especially for negation claims ('X was absent').",
    }


async def check_citation_presence(question: str, answer: GeneratedAnswer, result: RetrievalResult) -> dict:
    # BUG FOUND: length alone can't distinguish "a real claim requiring
    # citation" from "a correct, well-worded refusal that happens to be
    # long." diachronic-section-reorg-001's correct abstention ("The
    # documents provided do not contain any information...") was 172 chars
    # -- over the old 100-char threshold -- and got wrongly marked as
    # missing citations it never should have had in the first place.
    ABSTENTION_MARKERS = [
        "do not contain", "cannot confirm", "no reference",
        "unable to determine", "insufficient information",
        "not found in", "no information regarding",
    ]
    is_abstention = any(m in answer.text.lower() for m in ABSTENTION_MARKERS)
    has_substantive_content = len(answer.text) > 100 and not is_abstention
    has_citations = len(answer.cited_sections) > 0
    return {
        "passed": has_citations if has_substantive_content else True,
        "is_abstention": is_abstention,
        "cited_sections": answer.cited_sections,
        "unverifiable_citations": answer.unverifiable_citations,
        "note": "Known unreliable even with this fix -- confirmed a real run with correct, substantive content and zero citation tags.",
    }

async def check_citation_coverage(question: str, answer: GeneratedAnswer, result: RetrievalResult) -> dict:
    """For questions containing an explicit CFR citation, does retrieval
    output actually include that section? This is the check that would
    have caught the resolve()-scored-314.5-over-314.2 bug automatically."""
    from app.rag.retrieval import extract_citations

    cited_in_question = extract_citations(question)
    if not cited_in_question:
        return {"passed": None, "note": "question contains no explicit citation"}

    found_sections = {c.section_path for c in result.resolved}
    found_sections |= {sec for (_, sec) in result.lineages.keys()}

    missing = [c for c in cited_in_question if c not in found_sections]
    return {
        "passed": len(missing) == 0,
        "cited_in_question": cited_in_question,
        "found_in_retrieval": list(found_sections),
        "missing": missing,
    }


async def check_abstention(question: str, answer: GeneratedAnswer, result: RetrievalResult) -> dict:
    """Coarse heuristic: does the answer contain hedging/abstention language
    when we expect it to (unanswerable/ambiguous cases), or overclaim
    confidently when it shouldn't?  Deliberately crude -- a real version
    belongs in Phase 6 alongside proper guardrails; this just flags the
    signal for now."""
    lower = answer.text.lower()
    hedge_markers = [
        "do not contain", "cannot confirm", "no change", "not found",
        "no reference", "unable to", "insufficient", "may reflect",
        "not independently verified", "ambiguous",
    ]
    has_hedge = any(m in lower for m in hedge_markers)
    return {
        "passed": None,  # informational -- correctness depends on which
                          # direction is EXPECTED per-item, not universal
        "hedged": has_hedge,
        "note": "Informational only. Check against the item's expected_outcome manually.",
    }


CHECK_DISPATCH = {
    "faithfulness": check_faithfulness,
    "date_binding": check_date_binding,
    "citation_presence": check_citation_presence,
    "citation_coverage": check_citation_coverage,
    "abstention": check_abstention,
}
