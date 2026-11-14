import asyncio
import json
import time

from fastapi import Request

from app.runs import RunStore


async def stream_run(store: RunStore, owner: str, run_id: str, request: Request):
    previous = None
    heartbeat = time.monotonic()
    while not await request.is_disconnected():
        state = await asyncio.to_thread(store.stream_state, owner, run_id)
        if state is None:
            return
        if state['updated_at'] != previous:
            previous = state['updated_at']
            yield f"id: {previous}\nevent: run\ndata: {json.dumps(state, ensure_ascii=False)}\n\n"
            heartbeat = time.monotonic()
        elif time.monotonic() - heartbeat >= 15:
            yield ": keepalive\n\n"
            heartbeat = time.monotonic()
        if state['status'] not in {'queued', 'running'}:
            return
        await asyncio.sleep(0.2)
