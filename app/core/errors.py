from fastapi import Request
from fastapi.responses import JSONResponse


class ApiError(Exception):
    """An error with a readable `detail` plus a stable `code` (and any ids) the frontend can act on."""
    status_code = 400

    def __init__(self, code: str, detail: str, **extra):
        self.body = {"detail": detail, "code": code, **{k: str(v) for k, v in extra.items()}}


class Conflict(ApiError):
    status_code = 409


class NotFound(ApiError):
    status_code = 404


async def api_error_handler(_: Request, exc: ApiError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content=exc.body)
