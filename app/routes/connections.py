from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import or_, and_, not_
from typing import List, Optional
from uuid import UUID

from app.db.session import get_db
from app.models.user import User
from app.models.connection import Connection
from app.models.notification import Notification
from app.core.dependencies import get_current_user
from app.schemas.network import (
    ConnectionRequestCreate,
    ConnectionResponse,
    ConnectionStatusResponse,
    ConnectionUserSummary,
)

router = APIRouter(prefix="/connections", tags=["Connections"])

def _user_summary(user: User) -> ConnectionUserSummary:
    name = user.profile.full_name if user.profile and user.profile.full_name else user.username
    headline = user.profile.headline if user.profile else None
    avatar = user.profile.avatar_url if user.profile else None
    return ConnectionUserSummary(
        id=user.id,
        username=user.username,
        full_name=name,
        headline=headline,
        avatar_url=avatar,
    )

@router.get("", response_model=List[ConnectionResponse])
def get_user_connections(
    status_filter: Optional[str] = "accepted",
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    query = db.query(Connection).filter(
        or_(
            Connection.requester_id == current_user.id,
            Connection.receiver_id == current_user.id,
        )
    )

    if status_filter:
        query = query.filter(Connection.status == status_filter)

    connections = query.all()
    results = []
    for c in connections:
        partner = c.receiver if c.requester_id == current_user.id else c.requester
        results.append(
            ConnectionResponse(
                id=c.id,
                requester_id=c.requester_id,
                receiver_id=c.receiver_id,
                status=c.status,
                created_at=c.created_at,
                updated_at=c.updated_at,
                partner=_user_summary(partner),
            )
        )
    return results

@router.get("/pending", response_model=List[ConnectionResponse])
def get_pending_incoming_connections(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    connections = db.query(Connection).filter(
        Connection.receiver_id == current_user.id,
        Connection.status == "pending",
    ).all()
    return [
        ConnectionResponse(
            id=c.id,
            requester_id=c.requester_id,
            receiver_id=c.receiver_id,
            status=c.status,
            created_at=c.created_at,
            updated_at=c.updated_at,
            partner=_user_summary(c.requester),
        )
        for c in connections
    ]

@router.get("/status/{user_id}", response_model=ConnectionStatusResponse)
def get_connection_status(
    user_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if user_id == current_user.id:
        return ConnectionStatusResponse(user_id=user_id, status="self")

    conn = db.query(Connection).filter(
        or_(
            and_(Connection.requester_id == current_user.id, Connection.receiver_id == user_id),
            and_(Connection.requester_id == user_id, Connection.receiver_id == current_user.id),
        )
    ).first()

    if not conn or conn.status == "removed":
        return ConnectionStatusResponse(user_id=user_id, status=None)

    if conn.status == "accepted":
        return ConnectionStatusResponse(user_id=user_id, status="accepted", connection_id=conn.id)

    if conn.status == "pending":
        if conn.requester_id == current_user.id:
            return ConnectionStatusResponse(user_id=user_id, status="pending_sent", connection_id=conn.id)
        else:
            return ConnectionStatusResponse(user_id=user_id, status="pending_received", connection_id=conn.id)

    return ConnectionStatusResponse(user_id=user_id, status=conn.status, connection_id=conn.id)

@router.post("/request", response_model=ConnectionResponse)
def send_connection_request(
    data: ConnectionRequestCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if data.target_user_id == current_user.id:
        raise HTTPException(status_code=400, detail="Cannot connect to yourself")

    target = db.query(User).filter(User.id == data.target_user_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="Target developer not found")

    existing = db.query(Connection).filter(
        or_(
            and_(Connection.requester_id == current_user.id, Connection.receiver_id == data.target_user_id),
            and_(Connection.requester_id == data.target_user_id, Connection.receiver_id == current_user.id),
        )
    ).first()

    if existing:
        if existing.status == "accepted":
            raise HTTPException(status_code=400, detail="Already connected")
        if existing.status == "pending":
            raise HTTPException(status_code=400, detail="Connection request already pending")
        # If previously declined or removed, reset to pending
        existing.requester_id = current_user.id
        existing.receiver_id = data.target_user_id
        existing.status = "pending"
        conn = existing
    else:
        conn = Connection(
            requester_id=current_user.id,
            receiver_id=data.target_user_id,
            status="pending",
        )
        db.add(conn)

    # Send notification
    sender_name = current_user.profile.full_name if current_user.profile and current_user.profile.full_name else current_user.username
    notif = Notification(
        recipient_id=data.target_user_id,
        actor_id=current_user.id,
        type="connection_request",
        related_id=str(current_user.id),
        message=f"{sender_name} sent you a connection request.",
    )
    db.add(notif)

    db.commit()
    db.refresh(conn)

    return ConnectionResponse(
        id=conn.id,
        requester_id=conn.requester_id,
        receiver_id=conn.receiver_id,
        status=conn.status,
        created_at=conn.created_at,
        updated_at=conn.updated_at,
        partner=_user_summary(target),
    )

@router.put("/{connection_id}/accept", response_model=ConnectionResponse)
def accept_connection(
    connection_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    conn = db.query(Connection).filter(Connection.id == connection_id).first()
    if not conn:
        raise HTTPException(status_code=404, detail="Connection request not found")

    if conn.receiver_id != current_user.id:
        raise HTTPException(status_code=403, detail="You can only accept requests sent to you")

    conn.status = "accepted"

    # Notify requester
    acceptor_name = current_user.profile.full_name if current_user.profile and current_user.profile.full_name else current_user.username
    notif = Notification(
        recipient_id=conn.requester_id,
        actor_id=current_user.id,
        type="connection_accepted",
        related_id=str(current_user.id),
        message=f"{acceptor_name} accepted your connection request.",
    )
    db.add(notif)

    db.commit()
    db.refresh(conn)

    return ConnectionResponse(
        id=conn.id,
        requester_id=conn.requester_id,
        receiver_id=conn.receiver_id,
        status=conn.status,
        created_at=conn.created_at,
        updated_at=conn.updated_at,
        partner=_user_summary(conn.requester),
    )

@router.put("/{connection_id}/decline")
def decline_connection(
    connection_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    conn = db.query(Connection).filter(Connection.id == connection_id).first()
    if not conn:
        raise HTTPException(status_code=404, detail="Connection request not found")

    if conn.receiver_id != current_user.id:
        raise HTTPException(status_code=403, detail="You can only decline requests sent to you")

    conn.status = "declined"
    db.commit()
    return {"message": "Connection request declined"}

@router.delete("/{connection_id}")
def remove_or_cancel_connection(
    connection_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    conn = db.query(Connection).filter(Connection.id == connection_id).first()
    if not conn:
        raise HTTPException(status_code=404, detail="Connection not found")

    if conn.requester_id != current_user.id and conn.receiver_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to modify this connection")

    db.delete(conn)
    db.commit()
    return {"message": "Connection removed successfully"}

@router.get("/suggested", response_model=List[ConnectionUserSummary])
def get_suggested_developers(
    limit: int = 5,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # Get all user IDs current user is connected or pending with
    connections = db.query(Connection).filter(
        or_(
            Connection.requester_id == current_user.id,
            Connection.receiver_id == current_user.id,
        )
    ).all()
    excluded_ids = {
        c.receiver_id if c.requester_id == current_user.id else c.requester_id
        for c in connections
    }
    excluded_ids.add(current_user.id)

    candidates = db.query(User).filter(
        not_(User.id.in_(excluded_ids))
    ).limit(limit).all()

    return [_user_summary(u) for u in candidates]
