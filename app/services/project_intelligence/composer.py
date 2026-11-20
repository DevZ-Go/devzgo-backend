"""Rule-based plain-English insights from evidence claims. No invention beyond evidence."""

from __future__ import annotations

import re

from app.services.project_intelligence.analyzer import RawClaim
from app.services.project_intelligence.constants import COMPOSER_VERSION
from app.services.project_intelligence.quality import BRIEF_SCHEMA_VERSION, strip_readme_noise


def compose_insights(
    *,
    title: str,
    claims: list[RawClaim],
    tech_names: list[str],
    truncated: bool,
    coverage_pct: float,
) -> dict:
    by_type: dict[str, list[RawClaim]] = {}
    for c in claims:
        by_type.setdefault(c.claim_type, []).append(c)

    documented = by_type.get("documented_purpose", [])
    routes = by_type.get("api_route", [])
    pages = by_type.get("ui_page", [])
    models = by_type.get("data_model", [])
    auth = by_type.get("authentication", [])
    testing = by_type.get("testing", [])
    frameworks = [c.extra.get("tech") for c in by_type.get("framework", []) if c.extra.get("tech")]
    frameworks = list(dict.fromkeys([*(tech_names or []), *[f for f in frameworks if f]]))[:12]

    purpose_raw = None
    if documented:
        purpose_raw = (
            documented[0].extra.get("overview")
            or documented[0].wording.replace("README describes the project as: ", "").strip()
        )
        purpose_raw = strip_readme_noise(purpose_raw or "", max_chars=280) or None

    classification = _classify(pages, routes, frameworks, title, purpose_raw)
    what_it_does = _what_it_does(title, purpose_raw, routes, pages, frameworks, classification)
    purpose = _purpose(purpose_raw, classification, pages, routes)
    audience = _audience(classification, pages, routes, frameworks)
    capabilities = _capabilities(routes, pages, auth, models, classification)
    built = _built(frameworks, routes, pages, models, testing)
    how_simple = _how_simple(classification, frameworks, routes, pages)
    how_tech = _how_tech(frameworks, routes, models, auth)
    scope = _scope(pages, routes, models, frameworks, truncated)
    context = _context(purpose_raw, testing, frameworks, title)

    uncertainties: list[str] = [
        "Source files alone do not prove the project is deployed or used by real people.",
        "Feature lists are limited to what could be confirmed from selected source files.",
    ]
    if truncated:
        uncertainties.append(
            f"Analysis coverage was partial (~{coverage_pct}%). Some files were skipped due to size limits."
        )
    if not purpose_raw:
        uncertainties.append("No clear project overview was found outside the code structure.")
    if not routes and not pages:
        uncertainties.append("No clear API routes or UI pages were confirmed in the scanned files.")

    brief = {
        "schema_version": BRIEF_SCHEMA_VERSION,
        "headline": _headline(title, purpose_raw, classification),
        "overview": what_it_does,
        "features": capabilities[:6],
        "purpose": purpose,
        "audience": audience,
        "context": context,
        "scope": scope,
        "workflow": how_simple,
        "limitations": uncertainties[:3],
        "category": classification["category"],
        "stack_nature": classification["stack_nature"],
        "standalone": classification["standalone"],
        "source": "deterministic",
    }
    # Drop empty optionals
    for key in ("purpose", "audience", "context", "scope", "workflow"):
        if not brief.get(key) or str(brief[key]).lower().startswith("not confirmed"):
            brief.pop(key, None)

    everyday = {
        "one_sentence": what_it_does,
        "what_it_does": what_it_does,
        "problem": purpose or "",
        "who_could_use_it": audience or "",
        "capabilities": capabilities,
        "what_was_built": built,
        "how_it_works": how_simple,
        "uncertainties": uncertainties,
        "project_brief": brief,
    }
    technical = {
        "architecture": how_tech,
        "technologies": frameworks,
        "api_routes": [
            {"method": c.extra.get("method"), "path": c.extra.get("path"), "file": c.source_file}
            for c in routes[:25]
        ],
        "ui_pages": [{"name": c.extra.get("name") or c.source_file, "file": c.source_file} for c in pages[:25]],
        "data_models": [{"name": c.extra.get("name"), "file": c.source_file} for c in models[:25]],
        "authentication_signals": len(auth) > 0,
        "testing_signals": len(testing) > 0,
        "coverage_pct": coverage_pct,
        "truncated": truncated,
        "classification": classification,
    }
    return {
        "everyday": everyday,
        "technical": technical,
        "uncertainties": uncertainties,
        "composer_version": COMPOSER_VERSION,
        "project_brief": brief,
    }


def _classify(pages, routes, frameworks, title: str, purpose: str | None) -> dict:
    fw = {str(f).lower() for f in (frameworks or [])}
    title_l = (title or "").lower()
    purpose_l = (purpose or "").lower()
    blob = f"{title_l} {purpose_l}"

    if any(k in blob for k in ("game", "gofish", "chess", "puzzle", "play")) or "unity" in fw:
        category = "game"
    elif any(k in blob for k in ("android", "ios", "mobile", "flutter", "react native")):
        category = "mobile"
    elif any(k in blob for k in ("library", "sdk", "package", "cli tool")):
        category = "library"
    elif pages and routes:
        category = "website"
    elif pages:
        category = "website"
    elif routes:
        category = "backend"
    else:
        category = "other"

    if pages and routes:
        stack = "fullstack"
    elif pages and not routes:
        stack = "frontend"
    elif routes and not pages:
        stack = "backend"
    elif "react" in fw or "vue" in fw or "angular" in fw or "vite" in fw:
        stack = "frontend"
    elif "fastapi" in fw or "django" in fw or "flask" in fw or "express" in fw:
        stack = "backend"
    else:
        stack = "unknown"

    # Frontend-only with many pages → likely UI of a larger product
    standalone = True
    if stack == "frontend" and not routes:
        standalone = False
    if category == "library":
        standalone = False

    return {
        "category": category,
        "stack_nature": stack,
        "standalone": standalone,
        "page_count": len(pages),
        "route_count": len(routes),
    }


def _headline(title: str, purpose: str | None, classification: dict) -> str:
    if purpose and len(purpose) > 20:
        # First sentence-ish
        cut = purpose.split(". ")[0].strip()
        if len(cut) > 20 and cut.lower() != (title or "").lower():
            return cut[:140]
    cat = classification.get("category")
    stack = classification.get("stack_nature")
    if cat == "game":
        return f"{title}: a playable game project"
    if stack == "frontend":
        return f"{title}: a browser interface for an application"
    if stack == "backend":
        return f"{title}: a server that handles application requests"
    if stack == "fullstack":
        return f"{title}: a web application with screens and a server"
    return title


def _what_it_does(title: str, purpose: str | None, routes, pages, frameworks, classification) -> str:
    if purpose:
        return purpose if purpose.endswith(".") else purpose + "."
    cat = classification.get("category")
    stack = classification.get("stack_nature")
    if cat == "game" and pages:
        return (
            f"“{title}” looks like an interactive game with on-screen play areas "
            "defined in the interface code."
        )
    if stack == "fullstack":
        return (
            f"“{title}” appears to be a web application: people use screens in a browser, "
            "and those screens talk to a server that handles data and requests."
        )
    if stack == "frontend":
        return (
            f"“{title}” appears to be the visual interface of an application—screens and "
            "interaction flows defined in frontend source, likely part of a larger system."
        )
    if stack == "backend":
        return (
            f"“{title}” appears to be a backend service: other programs send requests to "
            "endpoints defined in the source."
        )
    bits = []
    if pages:
        bits.append("screens people can open")
    if routes:
        bits.append("server endpoints that respond to requests")
    if frameworks:
        bits.append(f"organized around {', '.join(frameworks[:3])}")
    if bits:
        return f"“{title}” appears to provide {' and '.join(bits[:2])}."
    return (
        f"“{title}” is a software project in this portfolio. A clearer everyday purpose "
        "was not obvious from the scanned files alone."
    )


def _purpose(purpose_raw: str | None, classification: dict, pages, routes) -> str | None:
    if purpose_raw:
        return purpose_raw if purpose_raw.endswith(".") else purpose_raw + "."
    cat = classification.get("category")
    if cat == "game":
        return "Lets people play an interactive game built as a software project."
    if classification.get("stack_nature") == "frontend":
        return "Gives people a visual way to interact with application features."
    if routes and not pages:
        return "Provides server capabilities that other software can call."
    if pages:
        return "Offers browser-based screens for people to complete tasks in the app."
    return None


def _audience(classification: dict, pages, routes, frameworks) -> str | None:
    cat = classification.get("category")
    if cat == "game":
        return "People looking for a simple interactive game to try."
    if cat == "educational" or cat == "mobile":
        return "People exploring a student or demo project in this category."
    if pages and routes:
        return "People who need a web application with both a user interface and a backend."
    if classification.get("stack_nature") == "frontend":
        return "People who would use the product’s screens in a browser (as part of a larger app)."
    if routes:
        return "Other software or developers that call an API."
    if frameworks:
        return None
    return None


def _human_page_name(page) -> str:
    raw = str((page.extra or {}).get("name") or page.source_file or "").strip()
    if not raw:
        return ""
    # Prefer last path segment without extension
    base = raw.rsplit("/", 1)[-1]
    base = re.sub(r"\.(tsx?|jsx?|vue|svelte)$", "", base, flags=re.I)
    if base.lower() in {"page", "index", "main", "app"}:
        parent = raw.rsplit("/", 2)
        if len(parent) >= 2:
            base = parent[-2]
    label = re.sub(r"[-_]+", " ", base).strip()
    return label.title() if label else ""


def _capabilities(routes, pages, auth, models, classification) -> list[str]:
    caps: list[str] = []
    cat = classification.get("category")
    page_names = []
    for p in pages[:8]:
        name = _human_page_name(p)
        if name and name not in page_names:
            page_names.append(name)

    if cat == "game" and pages:
        if page_names:
            caps.append(f"Play through screens such as {', '.join(page_names[:4])}.")
        else:
            caps.append("Play through interactive screens defined in the game interface.")
    elif page_names:
        caps.append(f"Open screens including {', '.join(page_names[:5])}.")
    elif pages:
        caps.append(f"Move between about {min(len(pages), 12)} distinct screens found in source.")

    route_paths = []
    for r in routes[:10]:
        path = (r.extra or {}).get("path")
        if path and path not in route_paths:
            route_paths.append(str(path))
    if route_paths:
        caps.append(f"Server endpoints include {', '.join(route_paths[:5])}.")
    elif routes:
        caps.append(f"Use about {min(len(routes), 40)} server routes found in source.")

    if auth:
        caps.append("Includes sign-in or access-control related code.")
    if models:
        model_names = []
        for m in models[:5]:
            name = (m.extra or {}).get("name")
            if name:
                model_names.append(str(name))
        if model_names:
            caps.append(f"Works with data such as {', '.join(model_names)}.")
        else:
            caps.append("Works with structured data models defined in the code.")
    if classification.get("stack_nature") == "frontend" and not routes:
        caps.append("Focuses on the visual layer; server logic may live in a separate project.")
    return caps[:6]


def _built(frameworks, routes, pages, models, testing) -> str:
    parts = []
    if frameworks:
        parts.append(f"technologies such as {', '.join(frameworks[:6])}")
    if routes:
        parts.append("API route handlers")
    if pages:
        parts.append("UI pages")
    if models:
        parts.append("data models")
    if testing:
        parts.append("automated tests")
    if not parts:
        return "The scanned source did not clearly show what was implemented beyond a file tree."
    return "The developer’s source includes " + ", ".join(parts) + "."


def _how_simple(classification: dict, frameworks, routes, pages) -> str:
    stack = classification.get("stack_nature")
    cat = classification.get("category")
    if cat == "game":
        return (
            "In simple terms: a person opens the game, sees the play area on screen, "
            "and interacts with controls defined in the interface code."
        )
    if stack == "fullstack" or (pages and routes):
        return (
            "In simple terms: people use screens in a browser, and those screens talk to "
            "a server that handles requests and data."
        )
    if stack == "frontend" or pages:
        return (
            "In simple terms: people interact with screens defined in the project’s interface code. "
            "This may be the front door of a larger application."
        )
    if stack == "backend" or routes:
        return "In simple terms: other programs send requests to this project’s server endpoints."
    if frameworks:
        return f"In simple terms: the project is organized around {', '.join(frameworks[:3])}."
    return ""


def _scope(pages, routes, models, frameworks, truncated: bool) -> str:
    n_pages, n_routes, n_models = len(pages), len(routes), len(models)
    if n_pages <= 3 and n_routes <= 5 and n_models <= 3:
        base = "A focused project with a small set of screens or endpoints."
    elif n_pages >= 8 or n_routes >= 20 or n_models >= 10:
        base = "A multi-part application with several distinct modules visible in source."
    elif n_pages and n_routes:
        base = "A multi-page application with separate interface and server pieces."
    elif n_pages:
        base = "A small interactive interface with a focused set of screens."
    elif n_routes:
        base = "A compact backend service with a focused set of endpoints."
    elif frameworks:
        base = "A software project organized around a recognizable stack."
    else:
        base = "Scope is hard to judge from the scanned files alone."
    if truncated:
        return base + " Some files were skipped, so this is a lower-bound view."
    return base


def _context(purpose_raw: str | None, testing, frameworks, title: str) -> str | None:
    blob = f"{(purpose_raw or '')} {(title or '')}".lower()
    if any(k in blob for k in ("assignment", "homework", "course project", "lab ", "tutorial")):
        return "Looks like educational or coursework-style work based on naming and documentation cues."
    if any(k in blob for k in ("prototype", "demo", "proof of concept", "poc")):
        return "Reads as a prototype or demonstration rather than a finished commercial product."
    if testing and len(testing) >= 3:
        return "Includes automated tests, which often signals a more established codebase."
    return None


def _how_tech(frameworks, routes, models, auth) -> str:
    bits = []
    if frameworks:
        bits.append(f"Stack signals: {', '.join(frameworks[:8])}.")
    if routes:
        bits.append(f"{len(routes)} route definitions were detected in source.")
    if models:
        bits.append(f"{len(models)} model-like classes were detected.")
    if auth:
        bits.append("Authentication-related symbols were detected.")
    bits.append("Presence of source does not mean the system is running in production.")
    return " ".join(bits)
