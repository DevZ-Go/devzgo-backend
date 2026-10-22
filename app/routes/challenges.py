"""Technical challenges and assessments.

Recruiters create challenges, invite developers and review submissions by hand against a rubric.
Developers accept invitations or open public challenges, submit a repository link or a DevZ-Go project,
and decide whether a released review appears on their portfolio. Deadlines are checked here in UTC.
A submitted project must already be visible to the recruiter; nothing private is opened up.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session, selectinload

from app.core.dependencies import get_current_user_optional, require_developer, require_recruiter
from app.core.rate_limit import rate_limit
from app.db.session import get_db
from app.models.challenge import (
    DEFAULT_CRITERIA,
    ChallengeInvitation,
    ChallengeReview,
    ChallengeSubmission,
    TechnicalChallenge,
)
from app.models.collaboration import ProjectCollaborator
from app.models.profile import UserProfile
from app.models.project import Project
from app.models.user import User
from app.schemas.challenge import (
    AssessmentsOut,
    ChallengeCounts,
    ChallengeDetail,
    ChallengeIn,
    ChallengeSummary,
    DeveloperInvitation,
    DeveloperSubmission,
    InvitationOut,
    InviteIn,
    InviteResult,
    RecruiterChallenge,
    ReviewIn,
    ReviewOut,
    SharedAssessment,
    SharingIn,
    SubmissionIn,
    SubmissionOut,
    SubmissionProject,
)
from app.services.social import can_view_project, display_name, may_message, notify, profile_open_to, user_summary

router = APIRouter(tags=["Challenges"])

DAILY_INVITES = 50
LOCKED = ("in_review", "reviewed")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(moment: Optional[datetime]) -> Optional[datetime]:
    if moment is None:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _accepting(ch: TechnicalChallenge) -> bool:
    deadline = _aware(ch.deadline_at)
    return ch.status == "open" and (deadline is None or _now() < deadline)


def _company(user: User) -> Optional[str]:
    return user.profile.company if user.profile and user.profile.company else None


def _summary(ch: TechnicalChallenge) -> ChallengeSummary:
    return ChallengeSummary(
        id=ch.id, title=ch.title, challenge_type=ch.challenge_type, technologies=list(ch.technologies or []),
        difficulty=ch.difficulty, estimated_minutes=ch.estimated_minutes, deadline_at=ch.deadline_at,
        visibility=ch.visibility, status=ch.status, accepting=_accepting(ch), recruiter=user_summary(ch.recruiter),
        company=_company(ch.recruiter), published_at=ch.published_at, created_at=ch.created_at,
    )


def _detail(ch: TechnicalChallenge) -> ChallengeDetail:
    return ChallengeDetail(**_summary(ch).model_dump(), description=ch.description, instructions=ch.instructions,
                           criteria=ch.criteria or [])


def _recruiter_view(db: Session, ch: TechnicalChallenge) -> RecruiterChallenge:
    invites = dict(db.query(ChallengeInvitation.status, func.count()).filter(
        ChallengeInvitation.challenge_id == ch.id).group_by(ChallengeInvitation.status).all())
    subs = dict(db.query(ChallengeSubmission.status, func.count()).filter(
        ChallengeSubmission.challenge_id == ch.id).group_by(ChallengeSubmission.status).all())
    has_reviews = db.query(ChallengeReview.id).join(ChallengeSubmission).filter(
        ChallengeSubmission.challenge_id == ch.id).first() is not None
    counts = ChallengeCounts(
        invited=sum(invites.values()) - invites.get("withdrawn", 0), accepted=invites.get("accepted", 0),
        submitted=subs.get("submitted", 0), in_review=subs.get("in_review", 0), reviewed=subs.get("reviewed", 0),
    )
    return RecruiterChallenge(**_detail(ch).model_dump(), counts=counts, has_reviews=has_reviews)


def _review_out(review: Optional[ChallengeReview], for_developer: bool) -> Optional[ReviewOut]:
    if review is None or (for_developer and review.status != "released"):
        return None
    return ReviewOut(status=review.status, ratings=review.ratings or {}, feedback=review.feedback,
                     released_at=review.released_at, updated_at=review.updated_at)


def _submission_out(sub: ChallengeSubmission, for_developer: bool) -> SubmissionOut:
    project = SubmissionProject(id=sub.project.id, title=sub.project.title) if sub.project else None
    return SubmissionOut(
        id=sub.id, challenge_id=sub.challenge_id, developer=user_summary(sub.developer), repo_url=sub.repo_url,
        project=project, explanation=sub.explanation, status=sub.status, revision=sub.revision,
        submitted_at=sub.submitted_at, updated_at=sub.updated_at, withdrawn_at=sub.withdrawn_at,
        share_ratings=sub.share_ratings, share_feedback=sub.share_feedback,
        review=_review_out(sub.review, for_developer),
    )


def _own_challenge(db: Session, challenge_id: UUID, me: User) -> TechnicalChallenge:
    ch = db.query(TechnicalChallenge).filter_by(id=challenge_id, recruiter_id=me.id).first()
    if not ch:
        raise HTTPException(status_code=404, detail="Challenge not found")
    return ch


def _criteria(data: ChallengeIn) -> list[dict]:
    rows = [c.model_dump() for c in data.criteria] if data.criteria else [dict(c) for c in DEFAULT_CRITERIA]
    keys = [c["key"] for c in rows]
    if len(set(keys)) != len(keys):
        raise HTTPException(status_code=400, detail="Each rubric criterion needs its own key")
    return rows


def _check_deadline(deadline: Optional[datetime]) -> None:
    if deadline is not None and _aware(deadline) <= _now():
        raise HTTPException(status_code=400, detail="The deadline must be in the future")


# ---------------- recruiter: challenges ----------------
@router.post("/challenges", response_model=RecruiterChallenge, status_code=status.HTTP_201_CREATED,
             dependencies=[rate_limit("challenge_create", 10, 600)])
def create_challenge(data: ChallengeIn, db: Session = Depends(get_db), me: User = Depends(require_recruiter)):
    _check_deadline(data.deadline_at)
    ch = TechnicalChallenge(
        recruiter_id=me.id, title=data.title.strip(), challenge_type=data.challenge_type, technologies=data.technologies,
        difficulty=data.difficulty, estimated_minutes=data.estimated_minutes, description=data.description.strip(),
        instructions=data.instructions.strip(), criteria=_criteria(data), deadline_at=data.deadline_at,
        visibility=data.visibility, status="draft",
    )
    db.add(ch)
    db.commit()
    db.refresh(ch)
    return _recruiter_view(db, ch)


@router.get("/challenges/mine", response_model=List[RecruiterChallenge])
def my_challenges(db: Session = Depends(get_db), me: User = Depends(require_recruiter)):
    rows = db.query(TechnicalChallenge).filter_by(recruiter_id=me.id).order_by(TechnicalChallenge.created_at.desc()).all()
    return [_recruiter_view(db, ch) for ch in rows]


@router.put("/challenges/{challenge_id}", response_model=RecruiterChallenge)
def update_challenge(challenge_id: UUID, data: ChallengeIn, db: Session = Depends(get_db),
                     me: User = Depends(require_recruiter)):
    ch = _own_challenge(db, challenge_id, me)
    if ch.status == "closed":
        raise HTTPException(status_code=400, detail="Closed challenges cannot be edited")
    if data.deadline_at != ch.deadline_at:
        _check_deadline(data.deadline_at)
    criteria = _criteria(data)
    reviewed = db.query(ChallengeReview.id).join(ChallengeSubmission).filter(
        ChallengeSubmission.challenge_id == ch.id).first()
    if reviewed and criteria != (ch.criteria or []):
        raise HTTPException(status_code=400, detail="The rubric cannot change once a review has started")
    if ch.status == "open" and data.visibility != ch.visibility:
        raise HTTPException(status_code=400, detail="Visibility cannot change after publishing")
    ch.title, ch.challenge_type, ch.technologies = data.title.strip(), data.challenge_type, data.technologies
    ch.difficulty, ch.estimated_minutes = data.difficulty, data.estimated_minutes
    ch.description, ch.instructions, ch.criteria = data.description.strip(), data.instructions.strip(), criteria
    ch.deadline_at, ch.visibility = data.deadline_at, data.visibility
    db.commit()
    db.refresh(ch)
    return _recruiter_view(db, ch)


@router.post("/challenges/{challenge_id}/publish", response_model=RecruiterChallenge)
def publish_challenge(challenge_id: UUID, db: Session = Depends(get_db), me: User = Depends(require_recruiter)):
    ch = _own_challenge(db, challenge_id, me)
    if ch.status == "open":
        raise HTTPException(status_code=400, detail="This challenge is already open")
    _check_deadline(ch.deadline_at)
    ch.status, ch.closed_at = "open", None
    ch.published_at = ch.published_at or _now()
    if ch.visibility == "public":
        company = _company(me) or "A recruiter"
        opted_in = (
            db.query(User.id)
            .join(UserProfile, UserProfile.user_id == User.id)
            .filter(User.account_type != "recruiter", UserProfile.notify_challenges.is_(True), User.id != me.id)
            .all()
        )
        for (developer_id,) in opted_in:
            notify(db, developer_id, me, "challenge_posted", str(ch.id),
                   f"{company} posted a technical challenge: {ch.title}.")
    db.commit()
    db.refresh(ch)
    return _recruiter_view(db, ch)


@router.post("/challenges/{challenge_id}/close", response_model=RecruiterChallenge)
def close_challenge(challenge_id: UUID, db: Session = Depends(get_db), me: User = Depends(require_recruiter)):
    ch = _own_challenge(db, challenge_id, me)
    if ch.status != "open":
        raise HTTPException(status_code=400, detail="Only open challenges can be closed")
    ch.status, ch.closed_at = "closed", _now()
    db.commit()
    db.refresh(ch)
    return _recruiter_view(db, ch)


@router.delete("/challenges/{challenge_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_challenge(challenge_id: UUID, db: Session = Depends(get_db), me: User = Depends(require_recruiter)):
    ch = _own_challenge(db, challenge_id, me)
    if db.query(ChallengeSubmission.id).filter_by(challenge_id=ch.id).first():
        raise HTTPException(status_code=409, detail="Developers have submitted work. Close the challenge instead.")
    db.delete(ch)
    db.commit()


# ---------------- recruiter: invitations ----------------
def _may_invite(db: Session, me: User, developer: Optional[User]) -> Optional[str]:
    if developer is None or developer.is_recruiter:
        return "Not a developer account"
    profile = developer.profile
    if profile is not None and profile.discoverable is False:
        return "Not discoverable"
    if not profile_open_to(db, developer.id, me.id):
        return "Profile is only open to their connections"
    if not ((profile and profile.open_to_work) or may_message(db, me.id, developer)):
        return "Only takes messages from connections and is not open to work"
    return None


@router.post("/challenges/{challenge_id}/invitations", response_model=InviteResult,
             dependencies=[rate_limit("challenge_invite", 10, 600)])
def invite_developers(challenge_id: UUID, data: InviteIn, db: Session = Depends(get_db),
                      me: User = Depends(require_recruiter)):
    ch = _own_challenge(db, challenge_id, me)
    if not _accepting(ch):
        raise HTTPException(status_code=400, detail="Publish the challenge (with a future deadline) before inviting")
    sent_today = db.query(ChallengeInvitation.id).join(TechnicalChallenge).filter(
        TechnicalChallenge.recruiter_id == me.id, ChallengeInvitation.created_at >= _now() - timedelta(days=1)).count()
    invited: list[UUID] = []
    skipped: dict[str, str] = {}
    company = _company(me) or "a company"
    for developer_id in dict.fromkeys(data.developer_ids):
        developer = db.query(User).options(selectinload(User.profile)).filter(User.id == developer_id).first()
        reason = _may_invite(db, me, developer)
        existing = db.query(ChallengeInvitation).filter_by(challenge_id=ch.id, developer_id=developer_id).first()
        if reason is None and existing and existing.status != "withdrawn":
            reason = "Already invited"
        if reason is None and sent_today + len(invited) >= DAILY_INVITES:
            reason = f"Daily limit of {DAILY_INVITES} invitations reached"
        if reason:
            skipped[str(developer_id)] = reason
            continue
        if existing:
            existing.status, existing.message, existing.responded_at = "pending", data.message, None
            existing.created_at = _now()
            invite = existing
        else:
            invite = ChallengeInvitation(challenge_id=ch.id, developer_id=developer_id, message=data.message, status="pending")
            db.add(invite)
        db.flush()
        notify(db, developer_id, me, "challenge_invitation", str(ch.id),
               f"{display_name(me)} ({company}) invited you to a technical challenge: {ch.title}.")
        invited.append(developer_id)
    db.commit()
    return InviteResult(invited=invited, skipped=skipped)


@router.get("/challenges/{challenge_id}/invitations", response_model=List[InvitationOut])
def challenge_invitations(challenge_id: UUID, db: Session = Depends(get_db), me: User = Depends(require_recruiter)):
    ch = _own_challenge(db, challenge_id, me)
    rows = db.query(ChallengeInvitation).filter_by(challenge_id=ch.id).order_by(ChallengeInvitation.created_at.desc()).all()
    return [InvitationOut(id=r.id, challenge_id=r.challenge_id, developer=user_summary(r.developer), message=r.message,
                          status=r.status, created_at=r.created_at, responded_at=r.responded_at) for r in rows]


@router.delete("/challenges/{challenge_id}/invitations/{invitation_id}", status_code=status.HTTP_204_NO_CONTENT)
def withdraw_invitation(challenge_id: UUID, invitation_id: UUID, db: Session = Depends(get_db),
                        me: User = Depends(require_recruiter)):
    ch = _own_challenge(db, challenge_id, me)
    invite = db.query(ChallengeInvitation).filter_by(id=invitation_id, challenge_id=ch.id, status="pending").first()
    if not invite:
        raise HTTPException(status_code=404, detail="No pending invitation to withdraw")
    invite.status, invite.responded_at = "withdrawn", _now()
    db.commit()


# ---------------- recruiter: submissions and reviews ----------------
@router.get("/challenges/{challenge_id}/submissions", response_model=List[SubmissionOut])
def challenge_submissions(challenge_id: UUID, db: Session = Depends(get_db), me: User = Depends(require_recruiter)):
    ch = _own_challenge(db, challenge_id, me)
    rows = (db.query(ChallengeSubmission).filter(ChallengeSubmission.challenge_id == ch.id,
                                                  ChallengeSubmission.status != "withdrawn")
            .order_by(ChallengeSubmission.submitted_at.desc()).all())
    return [_submission_out(s, for_developer=False) for s in rows]


def _own_submission(db: Session, challenge_id: UUID, submission_id: UUID, me: User) -> ChallengeSubmission:
    ch = _own_challenge(db, challenge_id, me)
    sub = db.query(ChallengeSubmission).filter_by(id=submission_id, challenge_id=ch.id).first()
    if not sub or sub.status == "withdrawn":
        raise HTTPException(status_code=404, detail="Submission not found")
    return sub


@router.get("/challenges/{challenge_id}/submissions/{submission_id}", response_model=SubmissionOut)
def submission_detail(challenge_id: UUID, submission_id: UUID, db: Session = Depends(get_db),
                      me: User = Depends(require_recruiter)):
    return _submission_out(_own_submission(db, challenge_id, submission_id, me), for_developer=False)


@router.put("/challenges/{challenge_id}/submissions/{submission_id}/review", response_model=SubmissionOut)
def save_review(challenge_id: UUID, submission_id: UUID, data: ReviewIn, db: Session = Depends(get_db),
                me: User = Depends(require_recruiter)):
    """Save a draft (the developer cannot see it) or release it. Saving starts the review, which locks resubmission."""
    sub = _own_submission(db, challenge_id, submission_id, me)
    review = sub.review
    if review is not None and review.status == "released":
        raise HTTPException(status_code=400, detail="This review was already released")
    keys = {c["key"] for c in sub.challenge.criteria or []}
    unknown = set(data.ratings) - keys
    if unknown:
        raise HTTPException(status_code=400, detail=f"Unknown rubric criteria: {', '.join(sorted(unknown))}")
    if data.release and (set(data.ratings) != keys or not (data.feedback or "").strip()):
        raise HTTPException(status_code=400, detail="Rate every criterion and write feedback before releasing")
    if review is None:
        review = ChallengeReview(submission_id=sub.id, reviewer_id=me.id)
        db.add(review)
    review.ratings, review.feedback = dict(data.ratings), (data.feedback or "").strip() or None
    if data.release:
        review.status, review.released_at = "released", _now()
        sub.status = "reviewed"
        notify(db, sub.developer_id, me, "challenge_feedback", str(sub.id),
               f"{display_name(me)} released feedback on your submission for {sub.challenge.title}.")
    else:
        review.status = "draft"
        sub.status = "in_review"
    db.commit()
    db.refresh(sub)
    return _submission_out(sub, for_developer=False)


# ---------------- developer: challenges and submissions ----------------
def _invitation(db: Session, ch: TechnicalChallenge, developer_id: UUID) -> Optional[ChallengeInvitation]:
    return db.query(ChallengeInvitation).filter_by(challenge_id=ch.id, developer_id=developer_id).first()


def _developer_may_view(db: Session, ch: TechnicalChallenge, user: Optional[User]) -> bool:
    if user is None:
        return False
    if ch.recruiter_id == user.id:
        return True
    if user.is_recruiter or ch.status == "draft":
        return False
    if db.query(ChallengeSubmission.id).filter_by(challenge_id=ch.id, developer_id=user.id).first():
        return True
    invite = _invitation(db, ch, user.id)
    if invite and invite.status in ("pending", "accepted"):
        return True
    return ch.visibility == "public" and ch.status == "open"


@router.get("/challenges/{challenge_id}", response_model=ChallengeDetail)
def get_challenge(challenge_id: UUID, db: Session = Depends(get_db),
                  current_user: Optional[User] = Depends(get_current_user_optional)):
    ch = db.query(TechnicalChallenge).filter_by(id=challenge_id).first()
    if not ch or not _developer_may_view(db, ch, current_user):
        raise HTTPException(status_code=404, detail="Challenge not found")
    return _detail(ch)


def _respond(db: Session, invitation_id: UUID, me: User, answer: str) -> DeveloperInvitation:
    invite = db.query(ChallengeInvitation).filter_by(id=invitation_id, developer_id=me.id).first()
    if not invite or invite.status == "withdrawn":
        raise HTTPException(status_code=404, detail="Invitation not found")
    if invite.status != "pending":
        raise HTTPException(status_code=400, detail="You already answered this invitation")
    invite.status, invite.responded_at = answer, _now()
    db.commit()
    db.refresh(invite)
    return DeveloperInvitation(id=invite.id, status=invite.status, message=invite.message, created_at=invite.created_at,
                               challenge=_summary(invite.challenge))


@router.post("/assessments/invitations/{invitation_id}/accept", response_model=DeveloperInvitation)
def accept_invitation(invitation_id: UUID, db: Session = Depends(get_db), me: User = Depends(require_developer)):
    return _respond(db, invitation_id, me, "accepted")


@router.post("/assessments/invitations/{invitation_id}/decline", response_model=DeveloperInvitation)
def decline_invitation(invitation_id: UUID, db: Session = Depends(get_db), me: User = Depends(require_developer)):
    return _respond(db, invitation_id, me, "declined")


def _check_project(db: Session, project_id: Optional[UUID], me: User, recruiter_id: UUID) -> None:
    if project_id is None:
        return
    project = db.query(Project).filter_by(id=project_id).first()
    if not project:
        raise HTTPException(status_code=400, detail="Project not found")
    mine = project.owner_id == me.id or db.query(ProjectCollaborator.id).filter_by(
        project_id=project.id, user_id=me.id).first() is not None
    if not mine:
        raise HTTPException(status_code=400, detail="Submit a project you own or belong to")
    if not can_view_project(db, project, recruiter_id):
        raise HTTPException(status_code=400, detail="The recruiter cannot open this project. Make it public, or share a repository link instead.")


@router.put("/challenges/{challenge_id}/submission", response_model=DeveloperSubmission,
            dependencies=[rate_limit("challenge_submit", 10, 600)])
def submit_work(challenge_id: UUID, data: SubmissionIn, db: Session = Depends(get_db),
                me: User = Depends(require_developer)):
    """Submit, or resubmit before the deadline as long as the review has not started."""
    ch = db.query(TechnicalChallenge).filter_by(id=challenge_id).first()
    if not ch or not _developer_may_view(db, ch, me):
        raise HTTPException(status_code=404, detail="Challenge not found")
    if not _accepting(ch):
        raise HTTPException(status_code=400, detail="This challenge is no longer accepting submissions")
    invite = _invitation(db, ch, me.id)
    if ch.visibility == "invitation" and not (invite and invite.status == "accepted"):
        raise HTTPException(status_code=403, detail="Accept the invitation before submitting")
    if not data.repo_url and not data.project_id:
        raise HTTPException(status_code=400, detail="Add a repository link or choose one of your projects")
    _check_project(db, data.project_id, me, ch.recruiter_id)

    sub = db.query(ChallengeSubmission).filter_by(challenge_id=ch.id, developer_id=me.id).first()
    first = sub is None
    if sub is None:
        sub = ChallengeSubmission(challenge_id=ch.id, developer_id=me.id, revision=1)
        db.add(sub)
    elif sub.status in LOCKED:
        raise HTTPException(status_code=400, detail="The review has started, so this submission can no longer change")
    else:
        sub.revision += 1
    sub.invitation_id = invite.id if invite else None
    sub.repo_url, sub.project_id, sub.explanation = data.repo_url, data.project_id, data.explanation.strip()
    sub.status, sub.withdrawn_at, sub.submitted_at = "submitted", None, _now()
    db.flush()
    notify(db, ch.recruiter_id, me, "challenge_submission", str(ch.id),
           f"{display_name(me)} {'submitted' if first else 'updated their submission for'} {ch.title}.")
    db.commit()
    db.refresh(sub)
    return DeveloperSubmission(**_submission_out(sub, for_developer=True).model_dump(), challenge=_detail(ch))


@router.delete("/challenges/{challenge_id}/submission", status_code=status.HTTP_204_NO_CONTENT)
def withdraw_submission(challenge_id: UUID, db: Session = Depends(get_db), me: User = Depends(require_developer)):
    sub = db.query(ChallengeSubmission).filter_by(challenge_id=challenge_id, developer_id=me.id).first()
    if not sub or sub.status == "withdrawn":
        raise HTTPException(status_code=404, detail="No submission to withdraw")
    if sub.status in LOCKED:
        raise HTTPException(status_code=400, detail="The review has started, so this submission can no longer be withdrawn")
    sub.status, sub.withdrawn_at = "withdrawn", _now()
    db.commit()


@router.get("/assessments", response_model=AssessmentsOut)
def my_assessments(db: Session = Depends(get_db), me: User = Depends(require_developer)):
    invites = (db.query(ChallengeInvitation).join(TechnicalChallenge)
               .filter(ChallengeInvitation.developer_id == me.id, ChallengeInvitation.status != "withdrawn",
                       TechnicalChallenge.status != "draft")
               .order_by(ChallengeInvitation.created_at.desc()).all())
    subs = (db.query(ChallengeSubmission).filter(ChallengeSubmission.developer_id == me.id)
            .order_by(ChallengeSubmission.submitted_at.desc()).all())
    involved = {i.challenge_id for i in invites} | {s.challenge_id for s in subs}
    public = (db.query(TechnicalChallenge)
              .filter(TechnicalChallenge.visibility == "public", TechnicalChallenge.status == "open",
                      TechnicalChallenge.recruiter_id != me.id)
              .order_by(TechnicalChallenge.published_at.desc()).limit(50).all())
    return AssessmentsOut(
        invitations=[DeveloperInvitation(id=i.id, status=i.status, message=i.message, created_at=i.created_at,
                                         challenge=_summary(i.challenge)) for i in invites],
        open_challenges=[_summary(ch) for ch in public if ch.id not in involved and _accepting(ch)],
        submissions=[DeveloperSubmission(**_submission_out(s, for_developer=True).model_dump(), challenge=_detail(s.challenge))
                     for s in subs],
    )


@router.put("/assessments/submissions/{submission_id}/sharing", response_model=DeveloperSubmission)
def set_sharing(submission_id: UUID, data: SharingIn, db: Session = Depends(get_db), me: User = Depends(require_developer)):
    """Show a released review on the portfolio. Written feedback is a separate choice and needs the ratings shared too."""
    sub = db.query(ChallengeSubmission).filter_by(id=submission_id, developer_id=me.id).first()
    if not sub:
        raise HTTPException(status_code=404, detail="Submission not found")
    if not (sub.review and sub.review.status == "released"):
        raise HTTPException(status_code=400, detail="Only reviewed submissions can be shared")
    sub.share_ratings = data.share_ratings
    sub.share_feedback = data.share_ratings and data.share_feedback
    db.commit()
    db.refresh(sub)
    return DeveloperSubmission(**_submission_out(sub, for_developer=True).model_dump(), challenge=_detail(sub.challenge))


@router.get("/profiles/{user_id}/assessments", response_model=List[SharedAssessment])
def shared_assessments(user_id: UUID, db: Session = Depends(get_db),
                       current_user: Optional[User] = Depends(get_current_user_optional)):
    """Reviewed challenges this developer chose to show, visible to whoever may see their profile."""
    viewer_id = current_user.id if current_user else None
    if not profile_open_to(db, user_id, viewer_id):
        return []
    rows = (db.query(ChallengeSubmission).join(ChallengeReview)
            .filter(ChallengeSubmission.developer_id == user_id, ChallengeSubmission.share_ratings.is_(True),
                    ChallengeReview.status == "released")
            .order_by(ChallengeReview.released_at.desc()).all())
    out = []
    for sub in rows:
        ch, review = sub.challenge, sub.review
        ratings = review.ratings or {}
        out.append(SharedAssessment(
            submission_id=sub.id, title=ch.title, challenge_type=ch.challenge_type, difficulty=ch.difficulty,
            technologies=list(ch.technologies or []), company=_company(ch.recruiter), reviewer=user_summary(ch.recruiter),
            reviewed_at=review.released_at,
            ratings=[{"label": c["label"], "rating": ratings[c["key"]]} for c in ch.criteria or [] if c["key"] in ratings],
            feedback=review.feedback if sub.share_feedback else None,
        ))
    return out
