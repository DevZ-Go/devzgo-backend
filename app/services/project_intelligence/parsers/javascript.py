"""Bounded JS/TS/React regex inspection (parse-only; no execution)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_ROUTE_PATTERNS = [
    re.compile(r"""(?:app|router|Router)\.(get|post|put|patch|delete|use)\s*\(\s*['"`]([^'"`]+)['"`]""", re.I),
    re.compile(r"""@(Get|Post|Put|Patch|Delete)\s*\(\s*['"`]([^'"`]+)['"`]""", re.I),
    re.compile(r"""createBrowserRouter|createRoutesFromElements|Route\s+path\s*=\s*['"`]([^'"`]+)['"`]""", re.I),
]
_PAGE_HINT = re.compile(
    r"(?:pages?|routes?|app)/(?:[\w\-]+/)*([\w\-]+)(?:/page|/index)?\.(?:tsx?|jsx?)$",
    re.I,
)
_REACT = re.compile(r"""\bfrom\s+['"]react['"]|\bimport\s+React\b|\buseState\b|\buseEffect\b""")
_NEXT = re.compile(r"""\bfrom\s+['"]next(?:/|\b)|next\.config""")
_EXPRESS = re.compile(r"""\bfrom\s+['"]express['"]|\brequire\s*\(\s*['"]express['"]""")
_AUTH = re.compile(r"\b(passport|jsonwebtoken|jose|next-auth|clerk|auth0|bcrypt)\b", re.I)
_FW = [
    ("React", re.compile(r"""['"]react['"]""")),
    ("Next.js", re.compile(r"""['"]next['"]""")),
    ("Vite", re.compile(r"""['"]vite['"]""")),
    ("Express.js", re.compile(r"""['"]express['"]""")),
    ("NestJS", re.compile(r"""['"]@nestjs/""")),
    ("TanStack Query", re.compile(r"""['"]@tanstack/react-query['"]""")),
]


@dataclass
class JsFindings:
    entry_points: list[dict] = field(default_factory=list)
    api_routes: list[dict] = field(default_factory=list)
    ui_pages: list[dict] = field(default_factory=list)
    auth_hints: list[dict] = field(default_factory=list)
    frameworks: list[str] = field(default_factory=list)
    integrations: list[str] = field(default_factory=list)


def parse_javascript(relative: str, source: str) -> JsFindings:
    out = JsFindings()
    name = relative.rsplit("/", 1)[-1].lower()

    if name in {"main.tsx", "main.ts", "main.jsx", "main.js", "index.tsx", "index.ts", "app.tsx", "app.ts", "server.js", "server.ts"}:
        out.entry_points.append({"file": relative, "kind": "js_entry", "line": 1})

    for label, pattern in _FW:
        if pattern.search(source) and label not in out.frameworks:
            out.frameworks.append(label)

    if _REACT.search(source) and "React" not in out.frameworks:
        out.frameworks.append("React")
    if _NEXT.search(source) and "Next.js" not in out.frameworks:
        out.frameworks.append("Next.js")
    if _EXPRESS.search(source) and "Express.js" not in out.frameworks:
        out.frameworks.append("Express.js")

    for pattern in _ROUTE_PATTERNS:
        for match in pattern.finditer(source):
            groups = match.groups()
            if len(groups) == 2:
                method, path = groups[0], groups[1]
            else:
                method, path = "ROUTE", groups[0]
            line = source[: match.start()].count("\n") + 1
            out.api_routes.append({
                "file": relative,
                "method": str(method).upper(),
                "path": path,
                "line": line,
            })

    page = _PAGE_HINT.search(relative.replace("\\", "/"))
    if page or "/pages/" in relative or "/routes/" in relative or relative.endswith("page.tsx") or relative.endswith("page.jsx"):
        out.ui_pages.append({"file": relative, "name": page.group(1) if page else name, "line": 1})

    for match in _AUTH.finditer(source):
        line = source[: match.start()].count("\n") + 1
        out.auth_hints.append({"file": relative, "name": match.group(1), "line": line})

    return out
