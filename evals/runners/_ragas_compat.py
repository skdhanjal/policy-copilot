"""Compatibility shim for a confirmed, still-open upstream ragas bug.

ragas/llms/base.py unconditionally does
`from langchain_community.chat_models.vertexai import ChatVertexAI` at
IMPORT TIME. LangChain's v1.0 restructuring (Oct 2025) moved that class to
langchain_google_vertexai; modern langchain-community no longer has the
submodule at all. Result: `import ragas` crashes for every user who isn't
on VertexAI -- confirmed via a clean install of ragas==0.4.3 alongside
langgraph==1.2.11 (reproduced the real ModuleNotFoundError before writing
this fix). Tracked upstream, still open as of this writing:
vibrantlabsai/ragas#2741, #2745, #2753 -- proposed fallback-import fixes
(#2793, #2837, #2957) exist but aren't merged/released.

This project never uses VertexAI -- the ragas judge calls OpenAI directly
(see DECISIONS.md D18). Stubbing the missing submodule in sys.modules
before ragas is ever imported sidesteps the bug entirely, without waiting
on upstream and without patching installed site-packages files (which
would silently break on every dependency upgrade).

This is what actually replaces the old .venv/.venv-eval split (DECISIONS.md
D37-D39): those decisions were correct against ragas==0.2.10, which at the
time had a genuine, unresolvable pip/uv dependency-resolution conflict
with langgraph's langchain-core requirement. Current ragas (0.4.3) declares
no version bound on langchain-core/langchain-community at all -- confirmed
via PyPI metadata -- so that resolution conflict no longer exists. The
ChatVertexAI import above is the only remaining blocker, and it's a
runtime bug, not a resolver conflict, so a shim is the correct fix rather
than a second virtualenv.

Import this before anything else in any module that imports ragas
(directly or transitively). evals/runners/checks.py does this as its
first import.
"""

from __future__ import annotations

import sys
import types

if "langchain_community.chat_models.vertexai" not in sys.modules:
    _stub = types.ModuleType("langchain_community.chat_models.vertexai")

    class ChatVertexAI:  # pragma: no cover -- never actually instantiated
        def __init__(self, *args, **kwargs):
            raise RuntimeError(
                "ChatVertexAI stub (evals/runners/_ragas_compat.py): this "
                "project does not use VertexAI. If you're seeing this, "
                "something started requesting it for real -- replace this "
                "shim with the real langchain-google-vertexai integration."
            )

    _stub.ChatVertexAI = ChatVertexAI
    sys.modules["langchain_community.chat_models.vertexai"] = _stub
