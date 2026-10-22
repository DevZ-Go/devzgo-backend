"""Profile roles. Recruiter is a real account type and always names a company or agency."""

from fastapi import HTTPException

ROLES = ("Developer", "Designer", "Tester", "Student", "Researcher", "Data", "Security", "Product", "Recruiter")


def assign_identity(profile, role: str | None, company: str | None, company_sent: bool, role_sent: bool) -> None:
    """Apply a role change and the company that goes with a recruiter. Raises 400 when the pair is incomplete."""
    if role_sent:
        if role not in ROLES:
            raise HTTPException(status_code=400, detail="Choose one of the listed roles")
        profile.role = role
    active = profile.role
    if active == "Recruiter":
        chosen = company if company_sent else profile.company
        chosen = " ".join((chosen or "").split())
        if len(chosen) < 2:
            raise HTTPException(status_code=400, detail="Add the company or agency you recruit for")
        profile.company = chosen[:120]
    elif company_sent:
        profile.company = " ".join((company or "").split())[:120] or None
