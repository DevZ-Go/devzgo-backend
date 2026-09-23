from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import or_, func
from datetime import datetime, timedelta

from app.db.session import get_db
from app.models.user import User
from app.models.network import NetworkActivity, ActivityLike, ActivityComment
from app.models.connection import Connection
from app.models.message import Message
from app.models.collaboration import CollaborationRequest, ProjectCollaborator
from app.core.dependencies import get_current_user
from app.schemas.network import DashboardAnalyticsResponse, ActivityMetricItem

router = APIRouter(prefix="/analytics", tags=["Analytics"])

@router.get("/dashboard-summary", response_model=DashboardAnalyticsResponse)
def get_dashboard_summary(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    uid = current_user.id

    # 1. Total posts created by user
    total_posts = db.query(NetworkActivity).filter(NetworkActivity.user_id == uid).count()

    # 2. Likes received on user's activities
    user_activity_ids = [a.id for a in db.query(NetworkActivity.id).filter(NetworkActivity.user_id == uid).all()]
    likes_received = 0
    comments_received = 0
    if user_activity_ids:
        likes_received = db.query(ActivityLike).filter(ActivityLike.activity_id.in_(user_activity_ids)).count()
        comments_received = db.query(ActivityComment).filter(ActivityComment.activity_id.in_(user_activity_ids)).count()

    # 3. Connections
    total_connections = db.query(Connection).filter(
        or_(Connection.requester_id == uid, Connection.receiver_id == uid),
        Connection.status == "accepted",
    ).count()

    pending_connections = db.query(Connection).filter(
        Connection.receiver_id == uid,
        Connection.status == "pending",
    ).count()

    # 4. Collaboration stats
    collab_sent = db.query(CollaborationRequest).filter(CollaborationRequest.requester_id == uid).count()
    collab_received = db.query(CollaborationRequest).filter(CollaborationRequest.project_owner_id == uid).count()
    accepted_collab = db.query(ProjectCollaborator).filter(ProjectCollaborator.user_id == uid).count()

    # 5. Messages
    msgs_sent = db.query(Message).filter(Message.sender_id == uid).count()
    msgs_received = db.query(Message).filter(Message.receiver_id == uid).count()

    # 6. Projects shared
    projects_shared = len(current_user.projects) if current_user.projects else 0

    # 7. Activity timeline over past 7 days
    today = datetime.utcnow().date()
    timeline = []
    for i in range(6, -1, -1):
        day = today - timedelta(days=i)
        start_dt = datetime(day.year, day.month, day.day)
        end_dt = start_dt + timedelta(days=1)
        count = db.query(NetworkActivity).filter(
            NetworkActivity.user_id == uid,
            NetworkActivity.created_at >= start_dt,
            NetworkActivity.created_at < end_dt,
        ).count()
        timeline.append(ActivityMetricItem(date=day.strftime("%b %d"), count=count))

    return DashboardAnalyticsResponse(
        total_posts=total_posts,
        likes_received=likes_received,
        comments_received=comments_received,
        total_connections=total_connections,
        pending_connections=pending_connections,
        collaboration_requests_sent=collab_sent,
        collaboration_requests_received=collab_received,
        accepted_collaborations=accepted_collab,
        messages_sent=msgs_sent,
        messages_received=msgs_received,
        projects_shared=projects_shared,
        activity_timeline=timeline,
    )
