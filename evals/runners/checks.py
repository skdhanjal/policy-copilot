"""Individual check functions, one per metric type declared in the golden
set's `checks:` field. Each returns a dict with at minimum `passed: bool`
and enough raw detail to debug a failure without re-running anything --
half of today's real findings came from reading raw output, not summaries.
"""

from __future__ import annotations

from evals.runners import _ragas_compat  # noqa: F401 -- must run before `import ragas`, see that module's docstring

from datasets import Dataset
from ragas import evaluate
from ragas.metrics import (
    faithfulness as ragas_faithfulness,
    answer_relevancy as ragas_answer_relevancy,
    context_precision as ragas_context_precision,
    context_recall as ragas_context_recall,
)
from ragas.embeddings.base import LangchainEmbeddingsWrapper
from langchain_openai import OpenAIEmbeddings

from app.rag.eval_metrics import check_date_bindings
from app.rag.generate import GeneratedAnswer, _render_context
from app.rag.retrieval import RetrievalResult

_ragas_embeddings: LangchainEmbeddingsWrapper | None = None


def _get_ragas_embeddings() -> LangchainEmbeddingsWrapper:
    """answer_relevancy needs an embeddings backend, and ragas's own
    auto-detected default is broken under current ragas + langchain-openai
    -- confirmed by running it, not assumed. With no explicit `embeddings=`,
    evaluate() instantiates ragas's MODERN embeddings provider
    (ragas.embeddings.OpenAIEmbeddings), but the legacy `answer_relevancy`
    metric class (imported above -- ragas.metrics, not .metrics.collections)
    calls `.embed_query()`, an interface only the LEGACY wrapper implements.
    Mixing legacy metric + modern embeddings throws `AttributeError:
    'OpenAIEmbeddings' object has no attribute 'embed_query'`. Using the
    matching legacy wrapper (LangchainEmbeddingsWrapper, itself deprecated
    but functional) alongside the legacy metric class fixes it.

    Built once, not per call -- same reasoning as the model singletons in
    app/rag/embed.py and app/rag/rerank.py.
    """
    global _ragas_embeddings
    if _ragas_embeddings is None:
        _ragas_embeddings = LangchainEmbeddingsWrapper(OpenAIEmbeddings())
    return _ragas_embeddings


async def check_faithfulness(question: str, answer: GeneratedAnswer, result: RetrievalResult) -> dict:
    context_text = _render_context(result)
    contexts = [c.strip() for c in context_text.split("<<<DOC") if c.strip()]
    if not contexts:
        return {"passed": None, "score": None, "note": "no context to evaluate against"}

    # Column names confirmed against a real run of current ragas
    # (question/answer/contexts -> user_input/response/retrieved_contexts).
    dataset = Dataset.from_dict({
        "user_input": [question], "response": [answer.text], "retrieved_contexts": [contexts],
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

async def check_answer_relevancy(question: str, answer: GeneratedAnswer, result: RetrievalResult) -> dict:
    """Different question from faithfulness: not 'is this grounded in
    context' but 'does this answer actually address what was asked'. An
    answer can be perfectly faithful and still dodge the question -- e.g.
    a diachronic question answered using only current-version facts, fully
    grounded, but not actually answering whether anything changed."""
    context_text = _render_context(result)
    contexts = [c.strip() for c in context_text.split("<<<DOC") if c.strip()]
    if not contexts:
        return {"passed": None, "score": None, "note": "no context to evaluate against"}

    # answer_relevancy's required columns are just user_input/response (no
    # contexts) -- but we still gate on "was anything retrieved at all"
    # above, since scoring relevancy on a question nothing was retrieved
    # for isn't meaningful.
    dataset = Dataset.from_dict({
        "user_input": [question], "response": [answer.text],
    })
    scores = evaluate(dataset, metrics=[ragas_answer_relevancy], embeddings=_get_ragas_embeddings())
    score = scores["answer_relevancy"][0]
    return {
        "passed": score >= 0.7,
        "score": score,
        "note": "Measures topical relevance to the question, not factual correctness or groundedness.",
    }


async def check_context_quality(question: str, answer: GeneratedAnswer, result: RetrievalResult) -> dict:
    """Grades RETRIEVAL, not generation.

    CORRECTED (was wrong originally, D19): assumed context_precision needed
    no ground truth, based on prior research. Running it for real threw a
    ValueError requiring a 'reference' column -- this Ragas version's
    context_precision DOES need ground truth, same as context_recall.
    Both are gated on the same expected_answer field, skipped together
    when absent, rather than one crashing and one silently working as
    originally (wrongly) designed.

    D19 also found context_precision and context_recall wanted the SAME
    ground-truth data under two DIFFERENT column names ('reference' vs
    'ground_truth') on ragas 0.2.10 -- confirmed via the real API error at
    the time, not documentation. Re-confirmed against current ragas
    (0.4.3): that split is gone. Both metrics now read 'reference', so one
    dataset and one evaluate() call covers both instead of two of each.
    """
    context_text = _render_context(result)
    contexts = [c.strip() for c in context_text.split("<<<DOC") if c.strip()]
    if not contexts:
        return {"passed": None, "note": "no context to evaluate against"}

    ground_truth = getattr(check_context_quality, "_current_ground_truth", None)
    if not ground_truth:
        return {
            "passed": None,
            "context_precision": None,
            "context_recall": None,
            "note": "skipped -- both context_precision and context_recall require expected_answer/ground_truth on this golden-set item, which is absent",
        }

    dataset = Dataset.from_dict({
        "user_input": [question], "response": [answer.text],
        "retrieved_contexts": [contexts], "reference": [ground_truth],
    })
    scores = evaluate(dataset, metrics=[ragas_context_precision, ragas_context_recall])

    precision = scores["context_precision"][0]
    recall = scores["context_recall"][0]
    return {
        "passed": precision >= 0.7,
        "context_precision": precision,
        "context_recall": recall,
        "note": "Both keyed on 'reference' as of ragas 0.4.3 (confirmed via a real run, supersedes D19's two-column-name finding) -- skipped together when expected_answer is absent on the item.",
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

    cited_in_question = extract_citations(question).section_paths
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
    "answer_relevancy": check_answer_relevancy,
    "context_quality": check_context_quality,
    "date_binding": check_date_binding,
    "citation_presence": check_citation_presence,
    "citation_coverage": check_citation_coverage,
    "abstention": check_abstention,
}
