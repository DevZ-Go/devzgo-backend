"""Resume / LinkedIn CSV extraction. Proposals only — never auto-publish."""

from __future__ import annotations

import csv
import io
import re
from typing import Any

# Lightweight heuristics. OCR is intentionally not enabled.

MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}

MONTH_TOKEN = (
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|"
    r"Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?|\d{1,2})"
)
CURRENT_TOKEN = r"(?:Present|Current|Now|Today)"

# Jan 2020 – Dec 2021 | 01/2020 - 06/2022 | 2020-2022 | Jan 2020 – Present
DATE_RANGE = re.compile(
    rf"(?P<sm>{MONTH_TOKEN})?[.\s/,-]*(?P<sy>20\d{{2}}|19\d{{2}})\s*[-–—to]+\s*"
    rf"(?P<em>{MONTH_TOKEN}|{CURRENT_TOKEN})?[.\s/,-]*(?P<ey>20\d{{2}}|19\d{{2}}|{CURRENT_TOKEN})?",
    re.I,
)

SINGLE_DATE = re.compile(
    rf"(?P<sm>{MONTH_TOKEN})?[.\s/,-]*(?P<sy>20\d{{2}}|19\d{{2}})",
    re.I,
)

SECTION_HEADERS = re.compile(
    r"(?im)^(?:\s*)("
    r"experience|work experience|work history|employment|professional experience|"
    r"education|academic|academics|"
    r"projects|personal projects|"
    r"skills|technical skills|technologies|"
    r"certifications?|licenses?|certificates|"
    r"awards|honors|publications|summary|profile|objective|interests"
    r")\s*:?\s*$"
)

EDU_HINT = re.compile(
    r"\b(university|college|institute|school|academy|polytechnic|"
    r"bachelor|master|doctorate|diploma|associate|phd|mba|"
    r"b\.?\s?sc|m\.?\s?sc|b\.?\s?a\.?\b|m\.?\s?a\.?\b|b\.?\s?eng|m\.?\s?eng|"
    r"high school|secondary)\b",
    re.I,
)

DEGREE_HINT = re.compile(
    r"(?i)\b(bachelor(?:'s)?|master(?:'s)?|phd|doctorate|diploma|associate|"
    r"b\.?\s?sc\.?|m\.?\s?sc\.?|b\.?\s?a\.?|m\.?\s?a\.?|b\.?\s?eng\.?|m\.?\s?eng\.?|mba)\b"
    r"(?:\s+(?:of|in)\s+([A-Za-z][A-Za-z0-9 &\-/]{2,80}))?"
)

LOCATION_HINT = re.compile(
    r"\b([A-Z][a-zA-Z.]+(?:\s+[A-Z][a-zA-Z.]+){0,2}),\s*([A-Z]{2}|[A-Z][a-z]+)\b"
)

SKILL_CATALOG = [
    "React", "React Native", "TypeScript", "JavaScript", "Python", "Java", "Kotlin",
    "Swift", "C++", "C#", "Go", "Rust", "Ruby", "PHP", "SQL", "PostgreSQL", "MySQL",
    "MongoDB", "Redis", "Docker", "Kubernetes", "AWS", "Azure", "GCP", "Node.js",
    "Express", "FastAPI", "Django", "Flask", "Spring", "GraphQL", "Next.js", "Vue",
    "Angular", "Tailwind", "HTML", "CSS", "Git", "Linux", "Terraform", "CI/CD",
]

CATEGORY_RULES = [
    (re.compile(r"\b(intern|internship)\b", re.I), "internship"),
    (re.compile(r"\b(co-?op|cooperative)\b", re.I), "coop"),
    (re.compile(r"\b(freelance|self-employed|independent contractor)\b", re.I), "freelance"),
    (re.compile(r"\b(contract|contractor)\b", re.I), "contract"),
    (re.compile(r"\b(volunteer|volunteering)\b", re.I), "volunteer"),
    (re.compile(r"\b(part[- ]?time)\b", re.I), "part_time"),
]


def _month(token: str | None) -> int | None:
    if not token:
        return None
    raw = token.strip().lower().rstrip(".")
    if raw.isdigit():
        n = int(raw)
        return n if 1 <= n <= 12 else None
    return MONTHS.get(raw[:9])


def _is_current(token: str | None) -> bool:
    return bool(token and re.search(r"present|current|now|today", token, re.I))


def _parse_range_groups(match: re.Match[str]) -> dict[str, Any]:
    sy_raw = match.group("sy") or ""
    ey_raw = match.group("ey") or ""
    em_raw = match.group("em") or ""
    is_current = _is_current(ey_raw) or _is_current(em_raw)
    return {
        "start_year": int(sy_raw) if sy_raw.isdigit() else None,
        "start_month": _month(match.group("sm")),
        "end_year": None if is_current else (int(ey_raw) if ey_raw.isdigit() else None),
        "end_month": None if is_current else _month(em_raw if not _is_current(em_raw) else None),
        "is_current": is_current,
    }


def detect_format(filename: str, content_type: str | None) -> str | None:
    name = (filename or "").lower()
    ctype = (content_type or "").lower()
    if name.endswith(".pdf") or "pdf" in ctype:
        return "pdf"
    if name.endswith(".docx") or "wordprocessingml" in ctype or "officedocument" in ctype:
        return "docx"
    if name.endswith(".csv") or "csv" in ctype or "text/plain" in ctype:
        return "linkedin_csv"
    return None


def validate_magic(fmt: str, raw: bytes) -> bool:
    if fmt == "pdf":
        return raw[:5] == b"%PDF-"
    if fmt == "docx":
        return raw[:2] == b"PK"  # zip/OOXML
    if fmt == "linkedin_csv":
        try:
            raw[:200].decode("utf-8", errors="strict")
            return True
        except UnicodeDecodeError:
            return False
    return False


def extract_text_pdf(raw: bytes) -> str:
    try:
        import fitz  # PyMuPDF
    except ImportError as exc:
        raise RuntimeError("PyMuPDF is required for PDF resume import") from exc
    doc = fitz.open(stream=raw, filetype="pdf")
    parts = []
    for page in doc:
        parts.append(page.get_text("text"))
    return "\n".join(parts)


def extract_text_docx(raw: bytes) -> str:
    try:
        from docx import Document
    except ImportError as exc:
        raise RuntimeError("python-docx is required for DOCX resume import") from exc
    document = Document(io.BytesIO(raw))
    return "\n".join(p.text for p in document.paragraphs if p.text.strip())


def extract_linkedin_csv(raw: bytes) -> list[dict[str, Any]]:
    text = raw.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    rows = []
    for row in reader:
        company = row.get("Company Name") or row.get("Company") or row.get("organization")
        title = row.get("Title") or row.get("Job Title") or row.get("title")
        if not company and not title:
            continue
        started = row.get("Started On") or row.get("Start Date") or ""
        finished = row.get("Finished On") or row.get("End Date") or ""
        rows.append({
            "organization": (company or "").strip(),
            "title": (title or "").strip(),
            "started_on": started.strip(),
            "finished_on": finished.strip(),
            "description": (row.get("Description") or "").strip(),
            "location": (row.get("Location") or "").strip() or None,
        })
    return rows


def _parse_date_token(token: str) -> tuple[int | None, int | None, bool]:
    token = (token or "").strip()
    if not token:
        return None, None, False
    if re.search(r"present|current|now|today", token, re.I):
        return None, None, True
    m = SINGLE_DATE.search(token)
    if not m:
        return None, None, False
    return int(m.group("sy")), _month(m.group("sm")), False


def _scrub(text: str) -> str:
    return re.sub(r"(?i)ignore (all|previous) instructions.*", " ", text)


def _split_sections(text: str) -> dict[str, str]:
    """Map normalized section name → body text."""
    lines = text.splitlines()
    headers: list[tuple[int, str]] = []
    for idx, line in enumerate(lines):
        m = SECTION_HEADERS.match(line.strip())
        if m:
            headers.append((idx, m.group(1).lower()))
    if not headers:
        return {"body": text}

    alias = {
        "work experience": "experience",
        "work history": "experience",
        "employment": "experience",
        "professional experience": "experience",
        "academic": "education",
        "academics": "education",
        "personal projects": "projects",
        "technical skills": "skills",
        "technologies": "skills",
        "certification": "certifications",
        "certificates": "certifications",
        "license": "certifications",
        "licenses": "certifications",
    }
    sections: dict[str, str] = {}
    for i, (start, name) in enumerate(headers):
        end = headers[i + 1][0] if i + 1 < len(headers) else len(lines)
        key = alias.get(name, name)
        body = "\n".join(lines[start + 1:end]).strip()
        sections[key] = (sections.get(key, "") + "\n" + body).strip()
    # Prefatory content before first header (often summary) — do not treat as experience.
    first = headers[0][0]
    if first > 0:
        sections["preamble"] = "\n".join(lines[:first]).strip()
    return sections


def _looks_like_education(*parts: str) -> bool:
    blob = " ".join(p for p in parts if p)
    return bool(EDU_HINT.search(blob))


def _infer_category(title: str, org: str = "") -> str:
    blob = f"{title} {org}"
    for pattern, category in CATEGORY_RULES:
        if pattern.search(blob):
            return category
    return "full_time"


def _extract_location(*parts: str) -> str | None:
    for part in parts:
        if not part:
            continue
        m = LOCATION_HINT.search(part)
        if m:
            return f"{m.group(1)}, {m.group(2)}"[:160]
    return None


def _collect_details(lines: list[str], start_idx: int) -> tuple[str, list[str], list[str]]:
    """Pull bullets after a role/date until the next dated entry or blank stretch."""
    description_parts: list[str] = []
    responsibilities: list[str] = []
    achievements: list[str] = []
    j = start_idx + 1
    while j < len(lines) and len(description_parts) < 12:
        nxt = lines[j].strip(" •-\t*")
        if not nxt:
            break
        if DATE_RANGE.search(lines[j]) or SECTION_HEADERS.match(lines[j].strip()):
            break
        if EDU_HINT.search(nxt) and len(nxt) < 80:
            break
        lower = nxt.lower()
        if lower.startswith(("achieved", "awarded", "increased", "reduced", "improved", "delivered")):
            achievements.append(nxt[:300])
        else:
            responsibilities.append(nxt[:300])
        description_parts.append(nxt)
        j += 1
    description = "\n".join(description_parts)[:2000]
    return description, responsibilities[:8], achievements[:6]


def _parse_role_block(lines: list[str], date_idx: int) -> dict[str, Any] | None:
    date_line = lines[date_idx]
    match = DATE_RANGE.search(date_line)
    if not match:
        return None
    dates = _parse_range_groups(match)
    title = ""
    org = ""
    # Common layouts: Org / Title / Dates  OR  Title @ Org / Dates  OR  Title / Org / Dates
    prev1 = lines[date_idx - 1].strip(" •-\t") if date_idx > 0 else ""
    prev2 = lines[date_idx - 2].strip(" •-\t") if date_idx >= 2 else ""

    at_split = re.split(r"\s+@\s+|\s+[–—|-]\s+", prev1, maxsplit=1)
    if len(at_split) == 2 and len(at_split[0]) < 120 and len(at_split[1]) < 120:
        title, org = at_split[0].strip(), at_split[1].strip()
    elif prev2 and len(prev2) < 120 and not DATE_RANGE.search(prev2):
        # Prefer shorter line as org when both look like headings
        if len(prev2) <= len(prev1) + 5:
            org, title = prev2, prev1
        else:
            title, org = prev2, prev1
    else:
        title = prev1 or "Role"

    # Date line may also carry leftover title fragments after the range
    remainder = DATE_RANGE.sub("", date_line).strip(" •-\t|,")
    location = _extract_location(remainder, prev1, prev2, date_line)
    if remainder and len(remainder) < 80 and not org:
        org = remainder

    if _looks_like_education(title, org, date_line):
        return None

    description, responsibilities, achievements = _collect_details(lines, date_idx)
    return {
        "organization": (org or "Unknown organization")[:200],
        "title": (title or "Role")[:200],
        "category": _infer_category(title, org),
        **dates,
        "location": location,
        "description": description,
        "responsibilities": responsibilities,
        "achievements": achievements,
        "source_location": f"line:{date_idx + 1}",
        "confidence": "medium" if org and dates.get("start_year") else "low",
    }


def parse_experiences_from_text(text: str) -> list[dict[str, Any]]:
    """Heuristic experience parser — section-aware, skips education-like rows."""
    cleaned = _scrub(text)
    sections = _split_sections(cleaned)
    # Prefer dedicated experience section; otherwise use body minus education/certs/skills.
    if "experience" in sections:
        block = sections["experience"]
    else:
        skip_keys = {"education", "certifications", "skills", "awards", "preamble", "summary", "profile", "objective"}
        parts = [body for key, body in sections.items() if key not in skip_keys and key != "education"]
        block = "\n".join(parts) if parts else cleaned
        # Still strip an inline Education section if headers were missed.
        block = re.sub(
            r"(?is)\beducation\b.*?(?=\b(?:experience|work experience|employment|projects|skills|certifications?)\b|\Z)",
            " ",
            block,
        )

    lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
    items: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for i, line in enumerate(lines):
        if not DATE_RANGE.search(line):
            continue
        parsed = _parse_role_block(lines, i)
        if not parsed:
            continue
        key = (parsed["organization"].lower(), parsed["title"].lower())
        if key in seen:
            continue
        seen.add(key)
        items.append(parsed)
        if len(items) >= 40:
            break
    return items


def parse_education_from_text(text: str) -> list[dict[str, Any]]:
    """Heuristic Education section parser — proposals only."""
    cleaned = _scrub(text)
    sections = _split_sections(cleaned)
    if "education" in sections:
        block = sections["education"]
    else:
        match = re.search(
            r"(?is)\beducation\b(.*?)(?=\b(?:experience|work experience|employment|projects|skills|certifications?|awards)\b|\Z)",
            cleaned,
        )
        block = match.group(1) if match else ""
    if not block.strip():
        return []

    lines = [ln.strip(" •-\t") for ln in block.splitlines() if ln.strip()]
    items: list[dict[str, Any]] = []
    i = 0
    while i < len(lines) and len(items) < 8:
        line = lines[i]
        date = DATE_RANGE.search(line)
        single = SINGLE_DATE.search(line) if not date else None
        if date or (single and EDU_HINT.search(line)):
            dates = _parse_range_groups(date) if date else {
                "start_year": int(single.group("sy")) if single and single.group("sy") else None,
                "start_month": _month(single.group("sm")) if single else None,
                "end_year": None, "end_month": None, "is_current": False,
            }
            institution = ""
            degree = None
            field = None
            if i > 0:
                institution = lines[i - 1][:200]
            if i >= 2 and (DEGREE_HINT.search(lines[i - 2]) or len(lines[i - 2]) < 120):
                degree_line = lines[i - 2]
                dm = DEGREE_HINT.search(degree_line)
                if dm:
                    degree = dm.group(0)[:200]
                    field = (dm.group(2) or "").strip()[:200] or None
                else:
                    degree = degree_line[:200]
            if not institution and EDU_HINT.search(line):
                institution = DATE_RANGE.sub("", line).strip(" •-\t|,")[:200] or "Institution"
            if institution:
                items.append({
                    "institution": institution or "Institution",
                    "degree": degree,
                    "field_of_study": field,
                    **dates,
                    "description": "",
                    "source_location": f"education:line:{i + 1}",
                    "confidence": "medium" if dates.get("start_year") or dates.get("end_year") else "low",
                })
        elif EDU_HINT.search(line):
            degree = None
            field = None
            dates = {"start_year": None, "start_month": None, "end_year": None, "end_month": None, "is_current": False}
            consumed = 0
            for look in range(1, 3):
                if i + look >= len(lines):
                    break
                nxt = lines[i + look]
                range_m = DATE_RANGE.search(nxt)
                if range_m:
                    dates = _parse_range_groups(range_m)
                    consumed = max(consumed, look)
                    continue
                dm = DEGREE_HINT.search(nxt) or (DEGREE_HINT.search(line) if look == 1 else None)
                if dm and not degree:
                    degree = dm.group(0)[:200]
                    field = (dm.group(2) or "").strip()[:200] or None
                    consumed = max(consumed, look)
                elif look == 1 and not degree and not DATE_RANGE.search(nxt) and len(nxt) < 120:
                    degree = nxt[:200]
                    consumed = max(consumed, look)
            if not degree:
                dm = DEGREE_HINT.search(line)
                if dm:
                    degree = dm.group(0)[:200]
                    field = (dm.group(2) or "").strip()[:200] or None
            items.append({
                "institution": line[:200],
                "degree": degree,
                "field_of_study": field,
                **dates,
                "description": "",
                "source_location": f"education:line:{i + 1}",
                "confidence": "medium" if dates.get("start_year") or dates.get("end_year") else "low",
            })
            i += consumed
        i += 1

    seen: set[str] = set()
    out = []
    for item in items:
        key = str(item.get("institution") or "").lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def parse_certifications_from_text(text: str) -> list[dict[str, Any]]:
    cleaned = _scrub(text)
    sections = _split_sections(cleaned)
    block = sections.get("certifications") or ""
    if not block:
        match = re.search(
            r"(?is)\bcertifications?\b(.*?)(?=\b(?:experience|education|projects|skills|awards)\b|\Z)",
            cleaned,
        )
        block = match.group(1) if match else ""
    if not block.strip():
        return []

    items: list[dict[str, Any]] = []
    for idx, raw_line in enumerate(block.splitlines()):
        line = raw_line.strip(" •-\t")
        if not line or len(line) < 3 or SECTION_HEADERS.match(line):
            continue
        year = None
        month = None
        range_m = DATE_RANGE.search(line)
        single_m = SINGLE_DATE.search(line) if not range_m else None
        if range_m:
            parsed = _parse_range_groups(range_m)
            year = parsed.get("end_year") or parsed.get("start_year")
            month = parsed.get("end_month") or parsed.get("start_month")
            line = DATE_RANGE.sub("", line).strip(" •-\t|,")
        elif single_m:
            year = int(single_m.group("sy")) if single_m.group("sy") and str(single_m.group("sy")).isdigit() else None
            month = _month(single_m.group("sm"))
            line = SINGLE_DATE.sub("", line).strip(" •-\t|,")
        if not line:
            continue
        parts = re.split(r"\s+[–—|-]\s+|\s+by\s+|\s+from\s+", line, maxsplit=1, flags=re.I)
        name = parts[0].strip()[:200]
        issuer = parts[1].strip()[:200] if len(parts) > 1 else None
        if len(name) < 3:
            continue
        items.append({
            "name": name,
            "issuer": issuer,
            "year": year,
            "month": month,
            "source_location": f"certifications:line:{idx + 1}",
            "confidence": "medium" if year else "low",
        })
        if len(items) >= 12:
            break
    return items


def parse_skills_from_text(text: str) -> list[str]:
    cleaned = _scrub(text)
    sections = _split_sections(cleaned)
    found: list[str] = []
    seen: set[str] = set()

    def add(name: str):
        key = name.lower()
        if key in seen or len(name) < 2:
            return
        seen.add(key)
        found.append(name)

    skill_block = sections.get("skills") or ""
    if skill_block:
        for token in re.split(r"[,|/•\n;]+", skill_block):
            name = token.strip(" .-")
            if 1 < len(name) <= 40 and not DATE_RANGE.search(name):
                add(name)

    catalog_pattern = re.compile(
        r"\b(?:" + "|".join(re.escape(s).replace(r"\ ", r"\s+") for s in sorted(SKILL_CATALOG, key=len, reverse=True)) + r")\b",
        re.I,
    )
    for hit in catalog_pattern.findall(cleaned):
        # Preserve catalog casing when possible
        canon = next((s for s in SKILL_CATALOG if s.lower() == hit.lower()), hit)
        add(canon)
    return found[:40]


def build_extracted_items(fmt: str, raw: bytes) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Return (extracted item payloads, free analysis summary)."""
    items: list[dict[str, Any]] = []
    analysis: dict[str, Any] = {
        "format": fmt,
        "missing_sections": [],
        "parsing_notes": [],
        "skills_mentioned": [],
        "duplicate_suspects": [],
        "scanned_or_empty": False,
    }

    if fmt == "linkedin_csv":
        for idx, row in enumerate(extract_linkedin_csv(raw)):
            sy, sm, _ = _parse_date_token(row["started_on"])
            ey, em, current = _parse_date_token(row["finished_on"])
            items.append({
                "item_type": "experience",
                "field_path": f"positions[{idx}]",
                "extracted_value": {
                    "organization": row["organization"] or "Unknown organization",
                    "title": row["title"] or "Role",
                    "category": _infer_category(row["title"], row["organization"]),
                    "start_year": sy, "start_month": sm,
                    "end_year": None if current else ey,
                    "end_month": None if current else em,
                    "is_current": current or not row["finished_on"],
                    "location": row["location"],
                    "description": row["description"],
                    "responsibilities": [],
                    "achievements": [],
                },
                "source_location": f"csv:row:{idx + 2}",
                "confidence": "medium",
            })
        if not items:
            analysis["missing_sections"].append("positions")
            analysis["parsing_notes"].append("No Positions-like rows found in CSV")
        return items, analysis

    if fmt == "pdf":
        text = extract_text_pdf(raw)
    else:
        text = extract_text_docx(raw)

    if len(text.strip()) < 40:
        analysis["scanned_or_empty"] = True
        analysis["parsing_notes"].append("Little or no extractable text — use manual entry (OCR is disabled)")
        analysis["missing_sections"].extend(["experience", "education", "skills"])
        return items, analysis

    for idx, exp in enumerate(parse_experiences_from_text(text)):
        items.append({
            "item_type": "experience",
            "field_path": f"experience[{idx}]",
            "extracted_value": {
                "organization": exp["organization"],
                "title": exp["title"],
                "category": exp["category"],
                "start_year": exp["start_year"],
                "start_month": exp["start_month"],
                "end_year": exp["end_year"],
                "end_month": exp["end_month"],
                "is_current": exp["is_current"],
                "location": exp.get("location"),
                "description": exp.get("description") or "",
                "responsibilities": exp.get("responsibilities") or [],
                "achievements": exp.get("achievements") or [],
            },
            "source_location": exp.get("source_location"),
            "confidence": exp.get("confidence", "low"),
        })

    skill_hits = parse_skills_from_text(text)
    analysis["skills_mentioned"] = skill_hits
    for idx, skill in enumerate(skill_hits):
        items.append({
            "item_type": "skill",
            "field_path": f"skills[{idx}]",
            "extracted_value": {"name": skill},
            "source_location": "skills_section_or_catalog",
            "confidence": "medium",
        })

    for idx, edu in enumerate(parse_education_from_text(text)):
        payload = dict(edu)
        items.append({
            "item_type": "education",
            "field_path": f"education[{idx}]",
            "extracted_value": {
                "institution": payload.get("institution"),
                "degree": payload.get("degree"),
                "field_of_study": payload.get("field_of_study"),
                "start_year": payload.get("start_year"),
                "start_month": payload.get("start_month"),
                "end_year": payload.get("end_year"),
                "end_month": payload.get("end_month"),
                "is_current": payload.get("is_current"),
                "description": payload.get("description") or "",
            },
            "source_location": payload.get("source_location") or "education_section",
            "confidence": payload.get("confidence") or "low",
        })

    for idx, cert in enumerate(parse_certifications_from_text(text)):
        items.append({
            "item_type": "certification",
            "field_path": f"certifications[{idx}]",
            "extracted_value": {
                "name": cert.get("name"),
                "issuer": cert.get("issuer"),
                "year": cert.get("year"),
                "month": cert.get("month"),
            },
            "source_location": cert.get("source_location"),
            "confidence": cert.get("confidence", "low"),
        })

    if not any(i["item_type"] == "experience" for i in items):
        analysis["missing_sections"].append("experience")
    if not any(i["item_type"] == "education" for i in items):
        analysis["missing_sections"].append("education")
    if not any(i["item_type"] == "skill" for i in items):
        analysis["missing_sections"].append("skills")

    # Final guard: drop experience rows that still look like education.
    filtered = []
    for item in items:
        if item["item_type"] == "experience":
            val = item["extracted_value"]
            if _looks_like_education(str(val.get("organization") or ""), str(val.get("title") or "")):
                analysis["parsing_notes"].append(
                    f"Skipped education-like experience: {val.get('title')} @ {val.get('organization')}"
                )
                continue
        filtered.append(item)
    items = filtered

    seen: dict[tuple, int] = {}
    for item in items:
        if item["item_type"] != "experience":
            continue
        key = (str(item["extracted_value"].get("organization", "")).lower(),
               str(item["extracted_value"].get("title", "")).lower())
        seen[key] = seen.get(key, 0) + 1
    analysis["duplicate_suspects"] = [
        {"organization": k[0], "title": k[1], "count": c} for k, c in seen.items() if c > 1
    ]
    return items, analysis
