"""Quality gates for Project Intelligence explanations (deterministic + AI)."""

from __future__ import annotations

import re
from typing import Any

BRIEF_SCHEMA_VERSION = "brief_v2"
QUALITY_VALID = "valid"
QUALITY_DETERMINISTIC = "deterministic"
QUALITY_LOW = "low_quality"
QUALITY_REJECTED = "rejected"
QUALITY_STALE = "stale"

_INSTALL_LINE = re.compile(
    r"(?i)^\s*("
    r"npm\s+(install|run|ci|create)|"
    r"pnpm\s+|yarn\s+|bun\s+|"
    r"pip\s+install|pipenv|poetry\s+install|"
    r"uvicorn\s+|flask\s+run|rails\s+s|"
    r"docker(-compose)?\s+|kubectl\s+|"
    r"export\s+[A-Z_]+=|set\s+[A-Z_]+=|"
    r"cd\s+\S+|mkdir\s+|clone\s+|"
    r"http://localhost|https://localhost|127\.0\.0\.1|"
    r"npx\s+|vite\s+|create-react-app|create-vite"
    r")"
)
_MARKDOWN_TABLE = re.compile(r"^\s*\|.+\|\s*$", re.M)
_CODE_FENCE = re.compile(r"```[\s\S]*?```")
_HEADING = re.compile(r"^#{1,6}\s+", re.M)
_VITE_BOILER = re.compile(
    r"(?i)("
    r"vite\s*\+|react\s*\+|"
    r"minimal setup to get react working|"
    r"working in vite with hmr|"
    r"click\s+on\s+the\s+(vite|hmr)|"
    r"edit\s+`?src/App|official\s+vite\s+plugins|"
    r"expanding\s+the\s+eslint|typescript\s+template|"
    r"eslint rules"
    r")"
)
_GENERIC_PHRASES = re.compile(
    r"(?i)^(not confirmed|unknown|n/?a|todo|tbd|none|see readme|"
    r"imported from github\.?|a software project|"
    r".*,\s*imported from github\.?)\.?$"
)
_IMPORT_GENERIC = re.compile(r"(?i)imported from github")


def strip_readme_noise(text: str, *, max_chars: int = 320) -> str:
    """Keep a short human overview; drop install docs, tables, and template fluff."""
    if not text:
        return ""
    cleaned = _CODE_FENCE.sub(" ", text)
    cleaned = _HEADING.sub("", cleaned)
    paras: list[str] = []
    for raw in re.split(r"\n\s*\n", cleaned):
        para = re.sub(r"\s+", " ", raw).strip(" -*\t")
        if not para or len(para) < 40:
            continue
        if _MARKDOWN_TABLE.search(para):
            continue
        if _VITE_BOILER.search(para):
            continue
        lines = [ln for ln in para.splitlines() if not _INSTALL_LINE.search(ln)]
        para = " ".join(ln.strip() for ln in lines if ln.strip())
        para = re.sub(r"\s+", " ", para).strip()
        if not para or len(para) < 40:
            continue
        if _looks_like_install_blob(para):
            continue
        paras.append(para)
        if sum(len(p) for p in paras) >= max_chars:
            break
    if not paras:
        return ""
    out = " ".join(paras)
    return out[:max_chars].rstrip(" ,;:")


def _looks_like_install_blob(text: str) -> bool:
    hits = len(_INSTALL_LINE.findall(text))
    if hits >= 2:
        return True
    lower = text.lower()
    markers = ("npm install", "pip install", "getting started", "prerequisites", "environment variable")
    return sum(1 for m in markers if m in lower) >= 2


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def _field_texts(payload: dict[str, Any]) -> list[str]:
    texts: list[str] = []
    for key in (
        "headline",
        "overview",
        "purpose",
        "audience",
        "context",
        "scope",
        "workflow",
        "what_is_this",
        "what_it_does",
        "why_use_it",
        "who_benefits",
        "how_it_works",
        "short_description",
        "how_it_works_simple",
    ):
        val = str(payload.get(key) or "").strip()
        if val:
            texts.append(val)
    for key in ("features", "key_features", "capabilities", "limitations"):
        for item in payload.get(key) or []:
            if isinstance(item, str) and item.strip():
                texts.append(item.strip())
    return texts


def score_explanation_quality(payload: dict[str, Any] | None, *, title: str = "") -> dict[str, Any]:
    """
    Return {status, reasons, score}.
    status: valid | low_quality | rejected
    """
    data = dict(payload or {})
    reasons: list[str] = []
    texts = _field_texts(data)
    joined = "\n".join(texts)
    title_n = _norm(title)

    if not texts:
        return {"status": QUALITY_REJECTED, "reasons": ["empty"], "score": 0}

    # Title-only / name echo / generic GitHub import blurbs
    def _is_substance(t: str) -> bool:
        n = _norm(t)
        if not n or n == title_n:
            return False
        if title_n and n.startswith(title_n) and len(n) <= len(title_n) + 8:
            return False
        if _IMPORT_GENERIC.search(t) and len(t) < len(title or "") + 40:
            return False
        if _GENERIC_PHRASES.match(t.strip()):
            return False
        return len(t) > max(12, len(title or "") + 2)

    meaningful = [t for t in texts if _is_substance(t)]
    if not meaningful:
        reasons.append("repeats_project_name")

    # Same sentence reused across sections
    norms = [_norm(t) for t in texts if len(t) > 20]
    if norms and len(set(norms)) < max(1, len(norms) // 2):
        reasons.append("repetitive_sections")

    if _MARKDOWN_TABLE.search(joined) or "```" in joined:
        reasons.append("raw_markdown")

    if _looks_like_install_blob(joined) or _INSTALL_LINE.search(joined):
        reasons.append("install_instructions")

    if _VITE_BOILER.search(joined):
        reasons.append("template_boilerplate")

    jargon_hits = len(
        re.findall(
            r"\b(API|JWT|ORM|CRUD|endpoint|middleware|webpack|Dockerfile|CI/CD|GraphQL|SQL)\b",
            joined,
        )
    )
    if jargon_hits >= 8 and len(joined) < 900:
        reasons.append("excessive_jargon")

    unknownish = sum(1 for t in texts if _GENERIC_PHRASES.match(t.strip()) or "not confirmed" in t.lower())
    if unknownish >= max(2, len(texts) // 2):
        reasons.append("mostly_unknown")

    # Very long README paste
    if any(len(t) > 900 for t in texts):
        reasons.append("overlong_excerpt")

    score = 100 - 18 * len(set(reasons))
    if "repeats_project_name" in reasons:
        score -= 20
    score = max(0, min(100, score))

    hard = {"repeats_project_name", "raw_markdown", "install_instructions", "template_boilerplate", "empty"}
    if reasons and hard.intersection(reasons) and score < 55:
        status = QUALITY_REJECTED if score < 35 else QUALITY_LOW
    elif score < 45:
        status = QUALITY_LOW
    elif reasons and score < 65:
        status = QUALITY_LOW
    else:
        status = QUALITY_VALID

    return {"status": status, "reasons": reasons, "score": score}


def is_valid_ai_explanation(payload: dict[str, Any] | None, *, title: str = "") -> bool:
    result = score_explanation_quality(payload, title=title)
    return result["status"] == QUALITY_VALID and result["score"] >= 60


def brief_from_legacy_simple(simple: dict[str, Any] | None, *, title: str = "", features: list | None = None) -> dict[str, Any]:
    """Normalize legacy Q&A fields into a flexible brief (may still be low quality)."""
    s = dict(simple or {})
    feats = list(features or s.get("key_features") or s.get("features") or s.get("capabilities") or [])
    feats = [str(f).strip() for f in feats if str(f).strip()][:6]
    headline = (s.get("headline") or s.get("what_is_this") or s.get("what_it_does") or title or "").strip()
    overview = (
        s.get("overview")
        or s.get("what_it_does")
        or s.get("how_it_works")
        or s.get("short_description")
        or ""
    ).strip()
    # Avoid title-as-overview
    if _norm(overview) == _norm(title) or _norm(overview) == _norm(headline):
        overview = (s.get("how_it_works") or s.get("why_use_it") or "").strip()
    brief = {
        "schema_version": s.get("schema_version") or BRIEF_SCHEMA_VERSION,
        "headline": headline[:160] if headline and _norm(headline) != _norm(title) else (overview[:120] or title)[:160],
        "overview": overview[:700],
        "features": feats,
        "purpose": (s.get("purpose") or s.get("why_use_it") or "").strip()[:400],
        "audience": (s.get("audience") or s.get("who_benefits") or "").strip()[:300],
        "context": (s.get("context") or "").strip()[:300],
        "scope": (s.get("scope") or "").strip()[:300],
        "workflow": (s.get("workflow") or s.get("how_it_works") or "").strip()[:700],
        "limitations": list(s.get("limitations") or s.get("uncertainties") or [])[:4],
        "category": s.get("category") or "",
        "stack_nature": s.get("stack_nature") or "",
        "standalone": s.get("standalone"),
    }
    # Drop empty optional strings
    for key in ("purpose", "audience", "context", "scope", "workflow", "category", "stack_nature"):
        if not brief.get(key):
            brief.pop(key, None)
    if brief.get("standalone") is None:
        brief.pop("standalone", None)
    if not brief.get("limitations"):
        brief.pop("limitations", None)
    if not brief.get("features"):
        brief.pop("features", None)
    return brief


def developer_brief(title: str, short: str, full: str) -> dict[str, Any]:
    """Safe non-AI overview from developer-authored text only (never dump full README-like blobs)."""
    short = (short or "").strip()
    full = (full or "").strip()
    overview = short
    if not overview and full:
        overview = strip_readme_noise(full, max_chars=420) or full[:280]
    if not overview:
        return {}
    if _norm(overview) == _norm(title):
        return {}
    brief = {
        "schema_version": BRIEF_SCHEMA_VERSION,
        "headline": (short[:120] if short and _norm(short) != _norm(title) else title)[:160],
        "overview": overview[:700],
        "source": "developer",
    }
    if full and full != short and len(full) < 600 and not _looks_like_install_blob(full):
        extra = strip_readme_noise(full, max_chars=400)
        if extra and _norm(extra) != _norm(overview):
            brief["workflow"] = extra
    return brief


def ai_package_to_brief(data: dict[str, Any], *, title: str = "") -> dict[str, Any]:
    """Map model JSON (v2 or legacy) into a brief dict."""
    features = list(data.get("key_features") or data.get("features") or [])[:6]
    limitations = list(data.get("limitations") or data.get("uncertainties") or [])[:4]
    brief = {
        "schema_version": BRIEF_SCHEMA_VERSION,
        "headline": (data.get("headline") or data.get("what_is_this") or data.get("short_description") or title or "")[:160],
        "overview": (
            data.get("overview")
            or data.get("what_it_does")
            or data.get("short_description")
            or ""
        )[:700],
        "features": [str(f).strip() for f in features if str(f).strip()],
        "purpose": (data.get("purpose") or data.get("why_use_it") or "")[:400],
        "audience": (data.get("audience") or data.get("who_benefits") or "")[:300],
        "context": (data.get("context") or "")[:300],
        "scope": (data.get("scope") or "")[:300],
        "workflow": (data.get("workflow") or data.get("how_it_works_simple") or data.get("how_it_works") or "")[:700],
        "limitations": [str(x).strip() for x in limitations if str(x).strip()],
        "category": (data.get("category") or "")[:40],
        "stack_nature": (data.get("stack_nature") or "")[:40],
        "standalone": data.get("standalone"),
    }
    return brief_from_legacy_simple(brief, title=title, features=brief.get("features"))
