"""Profile roles and account types.

The account type (developer or recruiter) is fixed at sign-up. The profile role is a label a developer
may change; it can never move an account across the developer/recruiter line.
"""

from fastapi import HTTPException

ROLES = ("Developer", "Designer", "Tester", "Student", "Researcher", "Data", "Security", "Product", "Recruiter")
ACCOUNT_TYPES = ("developer", "recruiter")


def account_type_for(role: str | None) -> str:
    return "recruiter" if role == "Recruiter" else "developer"


def assign_identity(user, profile, role: str | None, company: str | None, company_sent: bool, role_sent: bool) -> None:
    """Apply a role change and the company that goes with a recruiter. Raises 400 when the pair is incomplete
    or when the role would contradict the account type."""
    recruiter = user.account_type == "recruiter"
    if role_sent:
        if role not in ROLES:
            raise HTTPException(status_code=400, detail="Choose one of the listed roles")
        if recruiter and role != "Recruiter":
            raise HTTPException(status_code=400, detail="Recruiter accounts keep the Recruiter role")
        if not recruiter and role == "Recruiter":
            raise HTTPException(
                status_code=400,
                detail="Recruiter is an account type chosen at sign-up. Create a recruiter account to recruit.",
            )
        profile.role = role
    if recruiter:
        profile.role = "Recruiter"
        chosen = company if company_sent else profile.company
        chosen = " ".join((chosen or "").split())
        if len(chosen) < 2:
            raise HTTPException(status_code=400, detail="Add the company or agency you recruit for")
        profile.company = chosen[:120]
    elif company_sent:
        profile.company = " ".join((company or "").split())[:120] or None
