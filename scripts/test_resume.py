import asyncio, asyncpg, sys
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from app.core.config import get_settings
from app.agents.grounding_loop import build_graph, AgentContext

THREAD_ID = "resume-test-1"

async def run_partial():
    settings = get_settings()
    pool = await asyncpg.create_pool(settings.postgres_dsn, min_size=1, max_size=5)
    async with AsyncPostgresSaver.from_conn_string(settings.postgres_dsn) as cp:
        await cp.setup()
        graph = build_graph(checkpointer=cp)
        config = {"configurable": {"thread_id": THREAD_ID}}
        # Run just the first step (retrieve) then stop -- simulates a crash
        # before generate/check run.
        async for _ in graph.astream(
            {"question": "has 314.2 changed since 2023", "retries": 0, "total_cost": 0.0},
            context=AgentContext(pool=pool),
            config=config,
        ):
            print("completed a step, stopping here to simulate crash")
            break
    await pool.close()

async def resume():
    settings = get_settings()
    pool = await asyncpg.create_pool(settings.postgres_dsn, min_size=1, max_size=5)
    async with AsyncPostgresSaver.from_conn_string(settings.postgres_dsn) as cp:
        graph = build_graph(checkpointer=cp)
        config = {"configurable": {"thread_id": THREAD_ID}}
        # Passing None as input resumes from the last checkpoint for this thread_id
        result = await graph.ainvoke(None, context=AgentContext(pool=pool), config=config)
        print("resumed and completed:", result["grounded"], result["retries"])
    await pool.close()

if __name__ == "__main__":
    if sys.argv[1] == "partial":
        asyncio.run(run_partial())
    else:
        asyncio.run(resume())
