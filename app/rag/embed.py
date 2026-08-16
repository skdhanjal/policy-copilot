"""Local embedding via sentence-transformers.

Deliberately NOT behind the gateway (ADR-4). The gateway controls hosted LLM
provider calls -- routing, fallback, cost tracking across vendors. This is a
local model with no API key, no per-call cost, and no vendor to fail over
from, so none of the gateway's reasons for existing apply here.

all-MiniLM-L6-v2: 384-dim output, small and fast enough to run ingest on a
laptop CPU in a reasonable time. Good enough for Phase 1's job -- proving the
retrieval MECHANISM works -- not chosen for state-of-the-art retrieval
quality. Revisit in Phase 4 if eval scores show retrieval quality, not
mechanism, is the bottleneck.
"""

from __future__ import annotations

from sentence_transformers import SentenceTransformer

EMBEDDING_DIM = 384

_model: SentenceTransformer | None = None


def _get_model() -> SentenceTransformer:
    # Loaded once per process, not per call -- model load is the expensive
    # part (reads weights from disk), same reasoning as the connection pool
    # in Phase 0: don't pay a fixed cost repeatedly inside a loop.
    global _model
    if _model is None:
        _model = SentenceTransformer("all-MiniLM-L6-v2")
    return _model


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Batch embed. Always call this with a list, even for one item --
    batching is where sentence-transformers gets its real speedup."""
    model = _get_model()
    vectors = model.encode(texts, show_progress_bar=False, convert_to_numpy=True)
    return vectors.tolist()
