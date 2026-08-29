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
from app.rag.eval_metrics import check_date_bindings, BindingCheck

MAX_RETRIES = 2
MAX_COST_USD = 0.05  # ~2-3x a single diachronic call's real cost (~$0.02)
MAX_WALL_CLOCK_S = 30.0

class AgentState(TypedDict):
    question: str
    result: RetrievalResult
    answer: GeneratedAnswer
    retries: int
    grounded: bool
    total_cost: float
    start_time: float
    suspect_bindings: list[BindingCheck]


def _build_retry_question(question: str, suspects: list[BindingCheck]) -> str:
    """Turn check_node's flagged bindings into a targeted correction prompt.

    Previously the retry just repeated a generic "don't misattribute
    facts" warning regardless of what was actually wrong, which is why it
    only fixed the case ~1/3 of the time (D20). Pointing at the exact
    flagged sentence -- and, when known, the version it actually belongs
    to -- gives the model something concrete to correct instead of a
    repeat of the same non-deterministic mistake.
    """
    if not suspects:
        return (
            f"{question}\n\n(Retry: your previous answer misattributed a fact "
            "to the wrong version. Only attribute a claim to a version if it "
            "appears verbatim in THAT version's block.)"
        )

    lines = [
        f"{question}\n\nYour previous answer had the following date-attribution "
        "error(s). Fix them specifically, do not just repeat a similar claim:"
    ]
    for b in suspects:
        line = (
            f'- You wrote: "{b.sentence}" and attributed this to {b.claimed_date}, '
            f"but that exact content does not appear in the {b.claimed_date} version's block."
        )
        if b.likely_correct_date:
            line += f" It actually appears in the {b.likely_correct_date} version -- attribute it there instead."
        else:
            line += " Re-check every version block and attribute it to whichever one actually contains this text, or state you cannot verify it."
        lines.append(line)
    return "\n".join(lines)

@dataclass
class AgentContext:
    pool: asyncpg.Pool
    redis: object = None
    
async def retrieve_node(state: AgentState, runtime: Runtime[AgentContext]) -> dict:
    result = await retrieve(runtime.context.pool, state["question"])
    return {"result": result, "start_time": time.monotonic()}


async def generate_node(state: AgentState, runtime: Runtime[AgentContext]) -> dict:
    retries = state.get("retries", 0)
    q = state["question"]
    if retries > 0:
        q = _build_retry_question(state["question"], state.get("suspect_bindings", []))

    answer = await generate(state["result"], q, redis=runtime.context.redis)
    
    call_cost = answer.llm_call.actual_cost_usd if answer.llm_call else 0.0
    total_cost = state.get("total_cost", 0.0) + call_cost
    
    return {"answer": answer, "retries": retries + 1, "total_cost": total_cost}
    

def check_node(state: AgentState) -> dict:
    all_versions = [v for versions in state["result"].lineages.values() for v in versions]
    if not all_versions:
        return {"grounded": True, "suspect_bindings": []}
    bindings = check_date_bindings(state["answer"].text, all_versions)
    suspect = [b for b in bindings if b.date_exists_in_lineage and b.content_verified is False]
    return {"grounded": len(suspect) == 0, "suspect_bindings": suspect}


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