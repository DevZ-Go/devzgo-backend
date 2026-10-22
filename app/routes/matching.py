"""Job description matching for recruiters. Descriptions are read once and never stored."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.dependencies import require_recruiter
from app.core.rate_limit import rate_limit
from app.db.session import get_db
from app.models.user import User
from app.schemas.match import ExtractRequest, ExtractResponse, MatchRequest, MatchResponse
from app.services.jd_match import MatchFilters, catalog_names, extract, match

router = APIRouter(prefix="/recruiting/match", tags=["Recruiting"])


@router.post("/extract", response_model=ExtractResponse, dependencies=[rate_limit("match-extract", 30, 60)])
def extract_requirements(data: ExtractRequest, db: Session = Depends(get_db), _: User = Depends(require_recruiter)):
    """Suggested required and preferred technologies for the recruiter to edit before searching."""
    return extract(data.text, catalog_names(db))


@router.post("", response_model=MatchResponse, dependencies=[rate_limit("match-search", 30, 60)])
def match_developers(data: MatchRequest, db: Session = Depends(get_db), me: User = Depends(require_recruiter)):
    """Developers ranked by visible evidence for the edited requirements, each with the reasons behind the percentage."""
    if not [n for n in data.required + data.preferred if n.strip()]:
        raise HTTPException(status_code=400, detail="Add at least one required or preferred technology")
    filters = MatchFilters(
        open_to_work=data.open_to_work, work_type=data.work_type, arrangement=data.arrangement,
        location=data.location.strip(), min_relevant_projects=data.min_relevant_projects,
        require_all_required=data.require_all_required,
    )
    return match(db, me.id, data.required, data.preferred, filters)
