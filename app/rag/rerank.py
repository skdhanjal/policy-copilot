"""Cross-encoder reranking. Second stage after resolve()'s embedding
search -- scores query+chunk pairs jointly, more accurate but too slow
for whole-corpus search, viable on a small candidate set."""

from sentence_transformers import CrossEncoder

_model: CrossEncoder | None = None


def _get_model() -> CrossEncoder:
    global _model
    if _model is None:
        _model = CrossEncoder("cross-encoder/ms-marco-MiniLM-L-6-v2")
    return _model


def rerank(query: str, chunks: list, top_n: int) -> list:
    """chunks: list of ResolvedChunk. Returns top_n reordered by cross-encoder score."""
    if len(chunks) <= top_n:
        return chunks
    pairs = [(query, c.text) for c in chunks]
    scores = _get_model().predict(pairs)
    ranked = sorted(zip(chunks, scores), key=lambda x: x[1], reverse=True)
    return [c for c, _ in ranked[:top_n]]
