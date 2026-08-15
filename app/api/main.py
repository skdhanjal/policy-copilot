from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager

import asyncpg
from redis.asyncio import Redis
from app.core.config import get_settings

from fastapi import FastAPI, Response, status

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
        try:
            await task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="Policy Copilot", lifespan=lifespan)


@app.get("/healthz")
async def healthz(response: Response) -> dict:
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
    """Readiness: should traffic be routed here right now?

    Opposite philosophy to /healthz. This one DOES check dependencies. A
    failure removes the instance from the load balancer WITHOUT killing it,
    so it can recover and rejoin. That is why dependency checks belong here
    and never in liveness.
    """

    async def check(name: str, coro) -> tuple[str, str]:
        try:
            # Bounded wait. A hung backend must not hang the probe itself --
            # otherwise the prober times out and the orchestrator cannot tell
            # "dependency is slow" from "process is wedged".
            await asyncio.wait_for(coro, timeout=2.0)
            return name, "ok"
        except Exception as exc:
            return name, f"fail: {type(exc).__name__}"

    results = dict(
        await asyncio.gather(
            check("postgres", app.state.pg.fetchval("SELECT 1")),
            check("redis", app.state.redis.ping()),
        )
    )

    ready = all(v == "ok" for v in results.values())
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return {"ready": ready, "dependencies": results}

@app.get("/debug/block")
async def debug_block(seconds: float = 8.0) -> dict:
    """DELETE BEFORE DEPLOYING. Deliberately blocks the event loop.

    time.sleep() is synchronous -- it blocks the OS thread. Since the event
    loop IS that thread, everything stops. This is exactly what a runaway
    regex, a large JSON parse, or a `requests.get()` call does by accident.
    """
    time.sleep(seconds)
    return {"blocked_for_s": seconds}