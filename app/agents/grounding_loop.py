"""Self-correcting agent: retrieve -> generate -> check date_binding ->
retry generate on failure, up to MAX_RETRIES. Targets the still-open
diachronic-notification-event-001 case (D20)."""

from dataclasses import dataclass
import time
from typing import TypedDict
import asyncpg
from langgraph.graph import StateGraph, END
from langgraph.runtime import Runtime

from app.rag.retrieval import retrieve, RetrievalResult
from app.rag.generate import generate, GeneratedAnswer
from app.rag.eval_metrics import check_date_bindings

MAX_RETRIES = 2
MAX_COST_USD = 0.05  # ~2-3x a single diachronic call's real cost (~$0.02)
MAX_WALL_CLOCK_S = 30.0

class AgentState(TypedDict):
    pool: asyncpg.Pool
    question: str
    result: RetrievalResult
    answer: GeneratedAnswer
    retries: int
    grounded: bool
    total_cost: float
    start_time: float

@dataclass
class AgentContext:
    pool: asyncpg.Pool
    
async def retrieve_node(state: AgentState, runtime: Runtime[AgentContext]) -> dict:
    result = await retrieve(runtime.context.pool, state["question"])
    return {"result": result, "start_time": time.monotonic()}


async def generate_node(state: AgentState) -> dict:
    retries = state.get("retries", 0)
    q = state["question"]
    if retries > 0:
        q = f"{state['question']}\n\n(Retry {retries}: your previous answer misattributed a fact to the wrong version. Only attribute a claim to a version if it appears verbatim in THAT version's block.)"
    answer = await generate(state["result"], q)
    
    call_cost = answer.llm_call.actual_cost_usd if answer.llm_call else 0.0
    total_cost = state.get("total_cost", 0.0) + call_cost
    
    return {"answer": answer, "retries": retries + 1, "total_cost": total_cost}
    

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
    
    if state.get("total_cost", 0.0) >= MAX_COST_USD:
        return END
    
    elapsed = time.monotonic() - state.get("start_time", time.monotonic())
    if elapsed >= MAX_WALL_CLOCK_S:
        return END
    
    return "generate"


def build_graph(checkpointer=None):
    g = StateGraph(AgentState, context_schema=AgentContext)
    g.add_node("retrieve", retrieve_node)
    g.add_node("generate", generate_node)
    g.add_node("check", check_node)
    
    g.set_entry_point("retrieve")
    g.add_edge("retrieve", "generate")
    g.add_edge("generate", "check")
    g.add_conditional_edges("check", route_after_check, {"generate": "generate", END: END})
    
    return g.compile(checkpointer=checkpointer)