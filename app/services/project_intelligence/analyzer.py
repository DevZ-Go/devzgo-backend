"""Deterministic Project Intelligence analyzer. Never executes uploaded projects."""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from app.services.project_analysis import AnalysisResult, analyze_workspace
from app.services.project_intelligence.constants import ANALYZER_VERSION, MAX_CLAIMS, MAX_WALL_SECONDS
from app.services.project_intelligence.parsers import parse_javascript, parse_python
from app.services.project_intelligence.selectors import SelectionResult, read_text, select_files

DOCKER_NAMES = frozenset({"dockerfile", "docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"})
TEST_RE = re.compile(
    r"(?:^|/)(?:tests?|__tests__|spec)/|_test\.py$|\.(?:test|spec)\.[cm]?[jt]sx?$",
    re.I,
)
DEP_FILES = frozenset({
    "package.json", "requirements.txt", "pyproject.toml", "pipfile", "go.mod", "cargo.toml",
    "gemfile", "composer.json", "pubspec.yaml",
})


@dataclass
class RawClaim:
    claim_type: str
    wording: str
    support_class: str
    source_file: str | None = None
    source_location: str | None = None
    visibility: str = "team"
    extra: dict = field(default_factory=dict)
    refs: list[dict] = field(default_factory=list)


@dataclass
class AnalysisBundle:
    claims: list[RawClaim]
    coverage_pct: float
    truncated: bool
    content_hash: str
    file_count: int
    byte_total: int
    selection: SelectionResult
    languages: list[dict]
    tech_names: list[str]
    directory_summary: list[str]
    analyzer_version: str = ANALYZER_VERSION


def _loc(line: int | None) -> str | None:
    if line is None:
        return None
    return f"L{line}"


def _add(claims: list[RawClaim], claim: RawClaim) -> None:
    if len(claims) >= MAX_CLAIMS:
        return
    claims.append(claim)


def analyze_tree(root: Path, prior: AnalysisResult | None = None) -> AnalysisBundle:
    """Inspect selected source files and emit structured evidence claims."""
    started = time.monotonic()
    selection = select_files(root)
    prior = prior or analyze_workspace(root)
    claims: list[RawClaim] = []

    top_dirs = sorted({f.relative.split("/", 1)[0] for f in selection.files if "/" in f.relative})[:20]
    for d in top_dirs:
        _add(claims, RawClaim(
            claim_type="directory",
            wording=f"The project includes a {d}/ folder.",
            support_class="source_supported",
            source_file=d,
            visibility="public",
            extra={"directory": d},
        ))

    for lang in (prior.languages or [])[:12]:
        name = lang.get("name") if isinstance(lang, dict) else None
        pct = lang.get("percentage") if isinstance(lang, dict) else None
        if not name:
            continue
        _add(claims, RawClaim(
            claim_type="language",
            wording=f"Source files suggest {name}" + (f" (~{pct}%)" if pct is not None else "") + ".",
            support_class="source_supported",
            visibility="public",
            extra={"language": name, "percentage": pct},
        ))

    for tech in (prior.tech_names or [])[:30]:
        _add(claims, RawClaim(
            claim_type="framework",
            wording=f"Dependencies or source imports indicate {tech}.",
            support_class="source_supported",
            visibility="public",
            extra={"tech": tech},
        ))

    for item in selection.files:
        if time.monotonic() - started > MAX_WALL_SECONDS:
            break
        if len(claims) >= MAX_CLAIMS:
            break
        rel = item.relative
        name = rel.rsplit("/", 1)[-1].lower()
        text = read_text(item.path)
        if not text:
            continue

        if name in DEP_FILES or name in DOCKER_NAMES:
            kind = "dependency_manifest" if name in DEP_FILES else "deploy_config"
            _add(claims, RawClaim(
                claim_type=kind,
                wording=f"Found {name} configuration in the project.",
                support_class="source_supported",
                source_file=rel,
                visibility="public",
                refs=[{"source_file": rel, "line_start": 1, "label": name}],
            ))
            if name == "package.json":
                _deps_from_package_json(claims, rel, text)
            elif name in {"requirements.txt", "pyproject.toml"}:
                _deps_from_python_manifest(claims, rel, text, name)

        if TEST_RE.search(rel):
            _add(claims, RawClaim(
                claim_type="testing",
                wording="Automated test files are present in the source tree.",
                support_class="source_supported",
                source_file=rel,
                visibility="public",
                refs=[{"source_file": rel, "label": "test file"}],
            ))

        if name.startswith("readme"):
            _readme_claims(claims, rel, text)

        suffix = Path(name).suffix.lower()
        if suffix in {".py", ".pyw"}:
            findings = parse_python(rel, text)
            _emit_py(claims, findings)
        elif suffix in {".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts"}:
            findings = parse_javascript(rel, text)
            _emit_js(claims, findings)

    incomplete = selection.truncated or (time.monotonic() - started > MAX_WALL_SECONDS)
    coverage = selection.coverage_pct if not incomplete else min(selection.coverage_pct, 99.0)

    return AnalysisBundle(
        claims=claims,
        coverage_pct=coverage,
        truncated=incomplete,
        content_hash=selection.content_hash,
        file_count=len(selection.files),
        byte_total=selection.total_bytes,
        selection=selection,
        languages=list(prior.languages or []),
        tech_names=list(prior.tech_names or []),
        directory_summary=top_dirs,
    )


def _deps_from_package_json(claims: list[RawClaim], rel: str, text: str) -> None:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return
    deps = {}
    for key in ("dependencies", "devDependencies", "peerDependencies"):
        block = data.get(key) or {}
        if isinstance(block, dict):
            deps.update(block)
    for name in list(deps)[:40]:
        _add(claims, RawClaim(
            claim_type="dependency",
            wording=f"package.json lists dependency {name}.",
            support_class="source_supported",
            source_file=rel,
            visibility="team",
            extra={"package": name},
            refs=[{"source_file": rel, "label": name}],
        ))


def _deps_from_python_manifest(claims: list[RawClaim], rel: str, text: str, name: str) -> None:
    if name == "requirements.txt":
        for line in text.splitlines()[:80]:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            pkg = re.split(r"[<>=~\[]", line, maxsplit=1)[0].strip()
            if pkg:
                _add(claims, RawClaim(
                    claim_type="dependency",
                    wording=f"requirements.txt lists {pkg}.",
                    support_class="source_supported",
                    source_file=rel,
                    visibility="team",
                    extra={"package": pkg},
                    refs=[{"source_file": rel, "label": pkg}],
                ))
        return
    # pyproject — light parse
    for match in re.finditer(r"""["']([A-Za-z0-9_.\-]+)["']\s*[>=<]""", text):
        pkg = match.group(1)
        if pkg.lower() in {"python"}:
            continue
        _add(claims, RawClaim(
            claim_type="dependency",
            wording=f"pyproject.toml mentions {pkg}.",
            support_class="source_supported",
            source_file=rel,
            visibility="team",
            extra={"package": pkg},
            refs=[{"source_file": rel, "label": pkg}],
        ))


def _readme_claims(claims: list[RawClaim], rel: str, text: str) -> None:
    # README is untrusted documentation — mark as documented, not runtime-verified.
    from app.services.project_intelligence.quality import strip_readme_noise

    words = len(re.findall(r"[A-Za-z]{2,}", text))
    _add(claims, RawClaim(
        claim_type="documentation",
        wording="A README is present. Descriptions in it are documentation, not proof the software runs.",
        support_class="documented",
        source_file=rel,
        visibility="public",
        extra={"words": words},
        refs=[{"source_file": rel, "line_start": 1, "label": "README"}],
    ))
    # Prefer a cleaned overview — skip install steps, Vite boilerplate, and markdown tables.
    snippet = strip_readme_noise(text, max_chars=280)
    if snippet and len(snippet) > 40:
        _add(claims, RawClaim(
            claim_type="documented_purpose",
            wording=f"README describes the project as: {snippet}",
            support_class="documented",
            source_file=rel,
            visibility="public",
            extra={"overview": snippet},
            refs=[{"source_file": rel, "label": "README overview"}],
        ))


def _emit_py(claims: list[RawClaim], findings) -> None:
    for ep in findings.entry_points[:5]:
        _add(claims, RawClaim(
            claim_type="entry_point",
            wording=f"Python entry-style file found at {ep['file']}.",
            support_class="source_supported",
            source_file=ep["file"],
            source_location=_loc(ep.get("line")),
            visibility="public",
            refs=[{"source_file": ep["file"], "line_start": ep.get("line"), "label": "entry"}],
        ))
    for route in findings.api_routes[:40]:
        _add(claims, RawClaim(
            claim_type="api_route",
            wording=f"API route {route.get('method')} {route.get('path')} appears in source.",
            support_class="source_supported",
            source_file=route["file"],
            source_location=_loc(route.get("line")),
            visibility="team",
            extra={"method": route.get("method"), "path": route.get("path")},
            refs=[{"source_file": route["file"], "line_start": route.get("line"), "label": route.get("path")}],
        ))
    for model in findings.models[:30]:
        _add(claims, RawClaim(
            claim_type="data_model",
            wording=f"Data model class {model['name']} appears in source.",
            support_class="source_supported",
            source_file=model["file"],
            source_location=_loc(model.get("line")),
            visibility="team",
            extra={"name": model["name"]},
            refs=[{"source_file": model["file"], "line_start": model.get("line"), "label": model["name"]}],
        ))
    for auth in findings.auth_hints[:15]:
        _add(claims, RawClaim(
            claim_type="authentication",
            wording=f"Authentication-related code ({auth['name']}) appears in source.",
            support_class="source_supported",
            source_file=auth["file"],
            source_location=_loc(auth.get("line")),
            visibility="team",
            refs=[{"source_file": auth["file"], "line_start": auth.get("line"), "label": auth["name"]}],
        ))
    for fw in findings.frameworks:
        _add(claims, RawClaim(
            claim_type="framework",
            wording=f"Source imports indicate {fw}.",
            support_class="source_supported",
            visibility="public",
            extra={"tech": fw},
        ))
    for integ in findings.integrations:
        _add(claims, RawClaim(
            claim_type="integration",
            wording=f"Integration with {integ} appears in source imports.",
            support_class="source_supported",
            visibility="team",
            extra={"tech": integ},
        ))


def _emit_js(claims: list[RawClaim], findings) -> None:
    for ep in findings.entry_points[:5]:
        _add(claims, RawClaim(
            claim_type="entry_point",
            wording=f"JavaScript/TypeScript entry-style file found at {ep['file']}.",
            support_class="source_supported",
            source_file=ep["file"],
            source_location=_loc(ep.get("line")),
            visibility="public",
            refs=[{"source_file": ep["file"], "line_start": ep.get("line"), "label": "entry"}],
        ))
    for route in findings.api_routes[:40]:
        _add(claims, RawClaim(
            claim_type="api_route",
            wording=f"Route {route.get('method')} {route.get('path')} appears in source.",
            support_class="source_supported",
            source_file=route["file"],
            source_location=_loc(route.get("line")),
            visibility="team",
            extra={"method": route.get("method"), "path": route.get("path")},
            refs=[{"source_file": route["file"], "line_start": route.get("line"), "label": route.get("path")}],
        ))
    for page in findings.ui_pages[:30]:
        _add(claims, RawClaim(
            claim_type="ui_page",
            wording=f"UI page or screen file found: {page.get('name')}.",
            support_class="source_supported",
            source_file=page["file"],
            visibility="public",
            refs=[{"source_file": page["file"], "label": page.get("name")}],
        ))
    for auth in findings.auth_hints[:15]:
        _add(claims, RawClaim(
            claim_type="authentication",
            wording=f"Authentication-related library or symbol ({auth['name']}) appears in source.",
            support_class="source_supported",
            source_file=auth["file"],
            source_location=_loc(auth.get("line")),
            visibility="team",
            refs=[{"source_file": auth["file"], "line_start": auth.get("line"), "label": auth["name"]}],
        ))
    for fw in findings.frameworks:
        _add(claims, RawClaim(
            claim_type="framework",
            wording=f"Source indicates {fw}.",
            support_class="source_supported",
            visibility="public",
            extra={"tech": fw},
        ))
