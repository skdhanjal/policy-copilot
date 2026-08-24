from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager

import asyncpg
from redis.asyncio import Redis
from app.core.config import get_settings

from fastapi import FastAPI, Response, status
from pydantic import BaseModel
from app.rag.retrieval import retrieve, InjectionDetected
from app.rag.generate import generate
import uuid
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from app.agents.grounding_loop import build_graph, AgentContext

# How often the heartbeat wakes up.
HEARTBEAT_INTERVAL_S = 1.0
# How far behind schedule the loop must fall before we call it stalled.
# Generous on purpose: liveness means "wedged", not "slow".
STALL_THRESHOLD_S = 5.0


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Runs once on startup, and again on shutdown after the `yield`.

    This is where background tasks and connection pools are created, because
    it is the only place guaranteed to run inside the running event loop.
    """
    app.state.last_tick = time.monotonic()
    app.state.worst_lag_s = 0.0

    async def heartbeat() -> None:
        while True:
            await asyncio.sleep(HEARTBEAT_INTERVAL_S)

            now = time.monotonic()
            # Actual elapsed time minus what we asked to sleep. On a healthy
            # loop this is ~0.001s. If the loop was blocked, we woke up late
            # by exactly the duration of the blockage -- that lateness IS the
            # measurement. We cannot observe the stall while it happens; we
            # observe it by noticing we are late.
            lag = (now - app.state.last_tick) - HEARTBEAT_INTERVAL_S
            app.state.last_tick = now
            if lag > app.state.worst_lag_s:
                app.state.worst_lag_s = lag

    settings = get_settings()
    app.state.pg = await asyncpg.create_pool(
        settings.postgres_dsn, min_size=1, max_size=10
    )
    app.state.redis = Redis.from_url(settings.redis_dsn, decode_responses=True)
    
    app.state.checkpointer_cm = AsyncPostgresSaver.from_conn_string(settings.postgres_dsn)
    app.state.checkpointer = await app.state.checkpointer_cm.__aenter__()
    await app.state.checkpointer.setup()
    app.state.agent_graph = build_graph(checkpointer=app.state.checkpointer)

    
    task = asyncio.create_task(heartbeat())
    # Hold a reference. asyncio only keeps weak references to tasks, so a
    # task with no strong reference can be garbage collected mid-flight.
    # This is a real Python gotcha, not defensive noise.
    app.state.heartbeat_task = task

    try:
        yield
    finally:
        task.cancel()
        await app.state.pg.close()
        await app.state.redis.aclose()
        await app.state.checkpointer_cm.__aexit__(None, None, None)
        try:
            await task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="Policy Copilot", lifespan=lifespan)

class QueryRequest(BaseModel):
    question: str


@app.post("/query")
async def query(req: QueryRequest, response: Response) -> dict:
    thread_id = str(uuid.uuid4())
    ctx = AgentContext(pool=app.state.pg, redis=app.state.redis)
    config = {"configurable": {"thread_id": thread_id}}

    try:
        state = await app.state.agent_graph.ainvoke(
            {"question": req.question, "retries": 0, "total_cost": 0.0},
            context=ctx, config=config,
        )
    except InjectionDetected:
        response.status_code = status.HTTP_400_BAD_REQUEST
        return {"error": "request blocked", "reason": "injection_detected"}

    answer = state["answer"]
    result = state["result"]

    if answer.rate_limited:
        response.status_code = status.HTTP_429_TOO_MANY_REQUESTS

    return {
        "text": answer.text,
        "cited_sections": answer.cited_sections,
        "unverifiable_citations": answer.unverifiable_citations,
        "grounding_failed": answer.grounding_failed,
        "output_pii_leak": answer.output_pii_leak,
        "rate_limited": answer.rate_limited,
        "pii_found": result.pii_found,
        "intent": result.intent.value,
        "agent_retries": state["retries"],
        "agent_total_cost_usd": round(state["total_cost"], 6),
        "agent_grounded": state["grounded"],
    }


@app.get("/health-live")
async def health_live(response: Response) -> dict:
    """Liveness. Checks nothing external, on purpose.

    Note what this can and cannot see: if the loop is blocked right now, this
    function is not running at all and the caller will time out. That timeout
    is the real liveness signal. The values below are for observability.
    """
    since_tick = time.monotonic() - app.state.last_tick
    stalled = since_tick > STALL_THRESHOLD_S

    if stalled:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return {
        "status": "stalled" if stalled else "ok",
        # Healthy: 0.0 to 1.0 (time since the last heartbeat tick).
        "since_last_tick_s": round(since_tick, 3),
        # Healthy: under ~0.05. This is the number worth alerting on.
        "worst_lag_s": round(app.state.worst_lag_s, 3),
    }

@app.get("/readyz")
async def readyz(response: Response) -> dict:
    pool = app.state.pg
    idle = pool.get_idle_size()
    size = pool.get_size()
    max_size = pool.get_max_size()
    saturation = 1 - (idle / max_size) if max_size else 0.0

    async def check(name: str, coro) -> tuple[str, str]:
        try:
            await asyncio.wait_for(coro, timeout=2.0)
            return name, "ok"
        except Exception as exc:
            return name, f"fail: {type(exc).__name__}"

    # Only Redis gets an active probe. Postgres health is read from pool
    # stats -- free, instant, doesn't queue behind real traffic.
    redis_name, redis_status = await check("redis", app.state.redis.ping())

    pg_status = "ok"
    if saturation >= 0.9:
        pg_status = "degraded: pool saturated"
    elif size == 0:
        # Pool never successfully opened a connection at all -- genuinely down.
        pg_status = "fail: no connections"

    ready = pg_status == "ok" and redis_status == "ok"
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return {
        "ready": ready,
        "dependencies": {"postgres": pg_status, "redis": redis_status},
        "pool": {"size": size, "idle": idle, "max": max_size, "saturation": round(saturation, 2)},
    }
