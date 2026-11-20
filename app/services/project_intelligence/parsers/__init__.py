"""Language-specific parse-only extractors. Never execute project code."""

from app.services.project_intelligence.parsers.javascript import parse_javascript
from app.services.project_intelligence.parsers.python_ast import parse_python

__all__ = ["parse_python", "parse_javascript"]
