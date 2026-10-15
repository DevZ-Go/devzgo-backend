from fastapi import Request
from fastapi.responses import JSONResponse


class Conflict(Exception):
    """409 with a readable `detail` plus a stable `code` (and any ids) the frontend can act on."""

    def __init__(self, code: str, detail: str, **extra):
        self.body = {"detail": detail, "code": code, **{k: str(v) for k, v in extra.items()}}


async def conflict_handler(_: Request, exc: Conflict) -> JSONResponse:
    return JSONResponse(status_code=409, content=exc.body)
