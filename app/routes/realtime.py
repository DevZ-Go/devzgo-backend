import asyncio
import contextlib

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from starlette.concurrency import run_in_threadpool

from app.core.dependencies import user_from_token
from app.db.session import SessionLocal
from app.realtime.broadcaster import broadcaster

router = APIRouter(tags=["Realtime"])


def _user_id(token) -> str | None:
    with SessionLocal() as db:
        user = user_from_token(db, token if isinstance(token, str) else None)
        return str(user.id) if user else None


@router.websocket("/ws")
async def realtime_socket(websocket: WebSocket):
    """Client sends {"token": "<JWT>"} as the first message (keeps the token out of URLs and logs)."""
    await websocket.accept()
    try:
        first = await asyncio.wait_for(websocket.receive_json(), timeout=10)
        user_id = await run_in_threadpool(_user_id, first.get("token") if isinstance(first, dict) else None)
        if not user_id:
            raise ValueError("unauthorized")
    except (asyncio.TimeoutError, ValueError, WebSocketDisconnect):
        with contextlib.suppress(Exception):
            await websocket.close(code=4401)
        return

    queue = broadcaster.subscribe(user_id)

    async def pump():
        while True:
            await websocket.send_json(await queue.get())

    sender = asyncio.create_task(pump())
    try:
        await websocket.send_json({"type": "ready"})
        while True:  # client pings keep proxies from closing idle sockets
            if await websocket.receive_text() == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        pass
    finally:
        sender.cancel()
        broadcaster.unsubscribe(user_id, queue)
