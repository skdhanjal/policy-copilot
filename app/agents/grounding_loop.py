"""Self-correcting agent: retrieve -> generate -> check date_binding ->
retry generate on failure, up to MAX_RETRIES. Targets the still-open
diachronic-notification-event-001 case (D20)."""

from typing import TypedDict
import asyncpg
from langgraph.graph import StateGraph, END

from app.rag.retrieval import retrieve, RetrievalResult
from app.rag.generate import generate, GeneratedAnswer
from app.rag.eval_metrics import check_date_bindings

MAX_RETRIES = 2

class AgentState(TypedDict):
    pool: asyncpg.Pool
    question: str
    result: RetrievalResult
    answer: GeneratedAnswer
    retries: int
    grounded: bool


async def retrieve_node(state: AgentState) -> dict:
    result = await retrieve(state["pool"], state["question"])
    return {"result": result}


async def generate_node(state: AgentState) -> dict:
    retries = state.get("retries", 0)
    q = state["question"]
    if retries > 0:
        q = f"{state['question']}\n\n(Retry {retries}: your previous answer misattributed a fact to the wrong version. Only attribute a claim to a version if it appears verbatim in THAT version's block.)"
    answer = await generate(state["result"], q)
    return {"answer": answer, "retries": retries + 1}


def check_node(state: AgentState) -> dict:
    all_versions = [v for versions in state["result"].lineages.values() for v in versions]
    if not all_versions:
        return {"grounded": True}
    bindings = check_date_bindings(state["answer"].text, all_versions)
    suspect = [b for b in bindings if b.date_exists_in_lineage and b.content_verified is False]
    return {"grounded": len(suspect) == 0}


def route_after_check(state: AgentState) -> str:
    if state["grounded"] or state.get("retries", 0) >= MAX_RETRIES:
        return END
    return "generate"


def build_graph():
    g = StateGraph(AgentState)
    g.add_node("retrieve", retrieve_node)
    g.add_node("generate", generate_node)
    g.add_node("check", check_node)
    
    g.set_entry_point("retrieve")
    g.add_edge("retrieve", "generate")
    g.add_edge("generate", "check")
    g.add_conditional_edges("check", route_after_check, {"generate": "generate", END: END})
    
    return g.compile()
