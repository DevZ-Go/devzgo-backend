"""Rule-based plain-English insights from evidence claims. No invention beyond evidence."""

from __future__ import annotations

from app.services.project_intelligence.analyzer import RawClaim
from app.services.project_intelligence.constants import COMPOSER_VERSION


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

    purpose = None
    if documented:
        purpose = documented[0].wording.replace("README describes the project as: ", "").strip()
    what_it_does = _what_it_does(title, purpose, routes, pages, frameworks)
    problem = (
        "The README describes a purpose, but that is documentation—not independently verified."
        if documented
        else "The available source does not clearly confirm a specific real-world problem."
    )
    audience = _audience(pages, routes, frameworks)
    capabilities = _capabilities(routes, pages, auth, models)
    built = _built(frameworks, routes, pages, models, testing)
    how_simple = _how_simple(frameworks, routes, pages)
    how_tech = _how_tech(frameworks, routes, models, auth)

    uncertainties: list[str] = [
        "Source files alone do not prove the project is deployed, used by real users, or fully working.",
        "Feature lists are limited to what the analyzer could confirm from selected source files.",
    ]
    if truncated:
        uncertainties.append(
            f"Analysis coverage was partial (~{coverage_pct}%). Some files were skipped due to size limits."
        )
    if not purpose:
        uncertainties.append("Project purpose is uncertain because no clear README overview was found.")
    if not routes and not pages:
        uncertainties.append("No clear API routes or UI pages were confirmed in the scanned files.")

    everyday = {
        "one_sentence": what_it_does,
        "what_it_does": what_it_does,
        "problem": problem if documented else "Not confirmed from the available evidence.",
        "who_could_use_it": audience,
        "capabilities": capabilities,
        "what_was_built": built,
        "how_it_works": how_simple,
        "uncertainties": uncertainties,
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
    }
    return {
        "everyday": everyday,
        "technical": technical,
        "uncertainties": uncertainties,
        "composer_version": COMPOSER_VERSION,
    }


def _what_it_does(title: str, purpose: str | None, routes, pages, frameworks) -> str:
    if purpose:
        return purpose if purpose.endswith(".") else purpose + "."
    bits = []
    if pages:
        bits.append("screens people can open")
    if routes:
        bits.append("server endpoints that respond to requests")
    if frameworks:
        bits.append(f"built with {', '.join(frameworks[:4])}")
    if bits:
        return f"“{title}” appears to provide {' and '.join(bits[:2])}" + (
            f", {bits[2]}." if len(bits) > 2 else "."
        )
    return (
        f"“{title}” is a software project in this portfolio, but its everyday purpose "
        "could not be confirmed from the scanned source alone."
    )


def _audience(pages, routes, frameworks) -> str:
    if pages and routes:
        return "People who need a web application with both a user interface and a backend."
    if pages:
        return "People who would use a browser-based interface."
    if routes:
        return "Other software or developers that call an API."
    if frameworks:
        return "Not confirmed beyond developers familiar with the listed technologies."
    return "Not confirmed from the available evidence."


def _capabilities(routes, pages, auth, models) -> list[str]:
    caps: list[str] = []
    if pages:
        caps.append(f"Open about {min(len(pages), 12)} distinct screens or pages found in source.")
    if routes:
        caps.append(f"Call about {min(len(routes), 40)} API routes found in source.")
    if auth:
        caps.append("Use sign-in or access-control related code that appears in the project.")
    if models:
        caps.append("Work with structured data models defined in the code.")
    if not caps:
        caps.append("Specific user-facing capabilities could not be confirmed yet.")
    return caps


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


def _how_simple(frameworks, routes, pages) -> str:
    if pages and routes:
        return (
            "In simple terms: people use screens in a browser or app, and those screens talk to "
            "a server that handles requests and data."
        )
    if pages:
        return "In simple terms: people interact with screens defined in the project’s interface code."
    if routes:
        return "In simple terms: other programs send requests to this project’s server endpoints."
    if frameworks:
        return f"In simple terms: the project is organized around {', '.join(frameworks[:3])}."
    return "A simple explanation of how it works could not be confirmed from the scanned files."


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
