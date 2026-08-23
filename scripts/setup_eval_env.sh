#!/bin/bash
# Separate venv for eval/ragas tooling. Cannot share an environment with
# the main app -- ragas's ChatVertexAI import requires an old
# langchain-community that conflicts with langgraph's requirements.
# See DECISIONS.md D37.
uv venv .venv-eval
uv pip install --python .venv-eval "ragas==0.2.10" "langchain-community==0.3.27" pyyaml asyncpg
