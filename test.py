import asyncio
import time

async def agent():
    print("Agent: starting...")
    await asyncio.sleep(2)
    print("Agent: finished")
    return "result"

async def main():
    task = asyncio.create_task(agent())
    print("Coordinator: not awaiting the task")
    await asyncio.sleep(3)
    print("Coordinator: done")

# asyncio.run(main())
import asyncio

# Async generator simulating token streaming
async def stream_tokens():
    print("Generator: starting token stream...")
    for i in range(5):
        await asyncio.sleep(1)  # simulate delay between tokens
        print(f"Generator: yielding token {i}")
        yield f"token-{i}"
    print("Generator: finished streaming")
    
    
async def print_interval(interval: int = 1):
    while True:
        await asyncio.sleep(interval)
        print(f'fetching the value after {interval} seconds.')    

# Coordinator consuming the async generator
async def main():
    interval_task = asyncio.create_task(print_interval())
    
    print("Coordinator: starting to consume tokens")
    
    async for token in stream_tokens():
        print(f"Coordinator: received {token}")
    print("Coordinator: all tokens received")
    
    # yield 1
    
    # interval_task.cancel()

asyncio.run(main())

