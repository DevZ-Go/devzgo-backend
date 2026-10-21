"""Small in-memory request limits per signed-in user, enough to stop floods and runaway retry loops.
Counts are per backend process, so with several instances each enforces the limit on its own."""

import time
from collections import defaultdict, deque
from threading import Lock

from fastapi import Depends, HTTPException, Request

_hits: dict[str, deque] = defaultdict(deque)
_lock = Lock()


def rate_limit(name: str, times: int, seconds: int):
    """Dependency: at most `times` calls to `name` per `seconds` for one caller (bearer token, else IP)."""
    def check(request: Request) -> None:
        caller = request.headers.get("authorization") or (request.client.host if request.client else "?")
        key, now = f"{name}:{caller}", time.monotonic()
        with _lock:
            hits = _hits[key]
            while hits and now - hits[0] > seconds:
                hits.popleft()
            if len(hits) >= times:
                raise HTTPException(status_code=429, detail="Too many requests. Please slow down.",
                                    headers={"Retry-After": str(seconds)})
            hits.append(now)
    return Depends(check)
