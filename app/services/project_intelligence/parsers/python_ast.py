"""Python AST parse-only: FastAPI routes, models, auth hints, entry points."""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field


@dataclass
class PyFindings:
    entry_points: list[dict] = field(default_factory=list)
    api_routes: list[dict] = field(default_factory=list)
    models: list[dict] = field(default_factory=list)
    auth_hints: list[dict] = field(default_factory=list)
    frameworks: list[str] = field(default_factory=list)
    integrations: list[str] = field(default_factory=list)


_ROUTE_DECOS = re.compile(
    r"^(get|post|put|patch|delete|head|options|api_route|websocket|route)$", re.I
)
_AUTH_NAMES = frozenset({
    "oauth2passwordbearer", "httprenderer", "security", "depends", "get_current_user",
    "require_user", "jwt", "create_access_token", "verify_password", "authenticate",
})
_FW_IMPORTS = {
    "fastapi": "FastAPI",
    "flask": "Flask",
    "django": "Django",
    "sqlalchemy": "SQLAlchemy",
    "pydantic": "Pydantic",
    "celery": "Celery",
    "stripe": "Stripe",
    "openai": "OpenAI",
    "boto3": "AWS",
    "redis": "Redis",
}
_ENTRY_FILES = frozenset({"main.py", "app.py", "manage.py", "wsgi.py", "asgi.py", "run.py"})


def parse_python(relative: str, source: str) -> PyFindings:
    out = PyFindings()
    name = relative.rsplit("/", 1)[-1].lower()
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return out

    if name in _ENTRY_FILES or relative.endswith("/main.py") or relative.endswith("/app.py"):
        out.entry_points.append({"file": relative, "kind": "python_entry", "line": 1})

    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            mods: list[str] = []
            if isinstance(node, ast.Import):
                mods = [a.name.split(".")[0] for a in node.names]
            elif node.module:
                mods = [node.module.split(".")[0]]
            for mod in mods:
                label = _FW_IMPORTS.get(mod.lower())
                if label and label not in out.frameworks:
                    out.frameworks.append(label)
                if mod.lower() in {"stripe", "openai", "boto3", "sendgrid", "twilio"}:
                    if label and label not in out.integrations:
                        out.integrations.append(label or mod)

        if isinstance(node, ast.ClassDef):
            bases = []
            for b in node.bases:
                if isinstance(b, ast.Name):
                    bases.append(b.id)
                elif isinstance(b, ast.Attribute):
                    bases.append(b.attr)
            if any(b in {"Base", "BaseModel", "Model", "Document"} for b in bases):
                out.models.append({"file": relative, "name": node.name, "line": getattr(node, "lineno", None)})

        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for dec in node.decorator_list:
                method, path = _decorator_route(dec)
                if method:
                    out.api_routes.append({
                        "file": relative,
                        "method": method.upper(),
                        "path": path or f"/{node.name}",
                        "line": getattr(node, "lineno", None),
                        "handler": node.name,
                    })
            lower = node.name.lower()
            if lower in _AUTH_NAMES or any(k in lower for k in ("auth", "login", "password", "token")):
                out.auth_hints.append({"file": relative, "name": node.name, "line": getattr(node, "lineno", None)})

    return out


def _decorator_route(dec: ast.AST) -> tuple[str | None, str | None]:
    """Return (http_method, path) from @app.get('/x') or @router.post(...)."""
    if isinstance(dec, ast.Call):
        func = dec.func
        method = None
        if isinstance(func, ast.Attribute) and _ROUTE_DECOS.match(func.attr or ""):
            method = func.attr
        elif isinstance(func, ast.Name) and _ROUTE_DECOS.match(func.id or ""):
            method = func.id
        path = None
        if dec.args:
            arg0 = dec.args[0]
            if isinstance(arg0, ast.Constant) and isinstance(arg0.value, str):
                path = arg0.value
        return (method, path) if method else (None, None)
    if isinstance(dec, ast.Attribute) and _ROUTE_DECOS.match(dec.attr or ""):
        return dec.attr, None
    return None, None
