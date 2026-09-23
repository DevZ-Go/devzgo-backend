from uuid import UUID
from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime

# ==================== PROFILES ====================
class UserProfileBase(BaseModel):
    full_name: Optional[str] = None
    headline: Optional[str] = None
    bio: Optional[str] = None
    avatar_url: Optional[str] = None
    github_url: Optional[str] = None
    linkedin_url: Optional[str] = None
    website_url: Optional[str] = None
    skills: Optional[str] = None
    location: Optional[str] = None

class UserProfileUpdate(UserProfileBase):
    pass

class UserProfileResponse(UserProfileBase):
    id: UUID
    user_id: UUID
    username: str
    email: str
    created_at: datetime
    projects_count: int = 0
    connections_count: int = 0
    collaborations_count: int = 0
    connection_status: Optional[str] = None  # None, "pending_sent", "pending_received", "connected"

    class Config:
        from_attributes = True

# ==================== LIKES & COMMENTS ====================
class ActivityCommentCreate(BaseModel):
    comment_text: str

class ActivityCommentResponse(BaseModel):
    id: UUID
    activity_id: UUID
    author_id: UUID
    author_name: str
    author_username: str
    author_avatar: Optional[str] = None
    comment_text: str
    created_at: datetime

    class Config:
        from_attributes = True

class ActivityLikeResponse(BaseModel):
    activity_id: UUID
    likes_count: int
    is_liked: bool

# ==================== FEED / NETWORK ACTIVITIES ====================
class FeedPostCreate(BaseModel):
    title: Optional[str] = None
    content: str
    activity_type: str = "post"
    project_id: Optional[UUID] = None
    media_url: Optional[str] = None

class ProjectPreview(BaseModel):
    id: UUID
    title: str
    short_description: Optional[str] = None
    cover_image_url: Optional[str] = None
    tech_stacks: List[str] = []
    category: Optional[str] = None
    github_url: Optional[str] = None
    complexity: Optional[str] = None
    contribution_info: Optional[str] = None
    owner_username: Optional[str] = None

    class Config:
        from_attributes = True

class FeedActivityResponse(BaseModel):
    id: UUID
    user_id: UUID
    author_name: str
    author_username: str
    author_avatar: Optional[str] = None
    author_headline: Optional[str] = None
    activity_type: str
    title: Optional[str] = None
    content: Optional[str] = None
    media_url: Optional[str] = None
    is_demo: bool = False
    created_at: datetime

    project: Optional[ProjectPreview] = None
    likes_count: int = 0
    is_liked: bool = False
    comments_count: int = 0
    comments: List[ActivityCommentResponse] = []

    class Config:
        from_attributes = True

# ==================== CONNECTIONS ====================
class ConnectionRequestCreate(BaseModel):
    target_user_id: UUID

class ConnectionUserSummary(BaseModel):
    id: UUID
    username: str
    full_name: Optional[str] = None
    headline: Optional[str] = None
    avatar_url: Optional[str] = None

class ConnectionResponse(BaseModel):
    id: UUID
    requester_id: UUID
    receiver_id: UUID
    status: str
    created_at: datetime
    updated_at: Optional[datetime] = None
    partner: ConnectionUserSummary

    class Config:
        from_attributes = True

class ConnectionStatusResponse(BaseModel):
    user_id: UUID
    status: Optional[str] = None  # None, "pending_sent", "pending_received", "accepted"
    connection_id: Optional[UUID] = None

# ==================== MESSAGES ====================
class MessageCreate(BaseModel):
    receiver_id: UUID
    content: str

class MessageResponse(BaseModel):
    id: UUID
    sender_id: UUID
    receiver_id: UUID
    content: str
    read: bool
    created_at: datetime
    is_outgoing: bool = False

    class Config:
        from_attributes = True

class ConversationResponse(BaseModel):
    other_user: ConnectionUserSummary
    last_message: MessageResponse
    unread_count: int = 0

# ==================== COLLABORATIONS ====================
class CollaborationRequestCreate(BaseModel):
    message: Optional[str] = None
    role: str = "Collaborator"

class CollaborationRequestResponse(BaseModel):
    id: UUID
    project_id: UUID
    project_title: str
    requester_id: UUID
    requester: ConnectionUserSummary
    project_owner_id: UUID
    message: Optional[str] = None
    role: Optional[str] = None
    status: str
    created_at: datetime

    class Config:
        from_attributes = True

class ProjectCollaboratorResponse(BaseModel):
    id: UUID
    project_id: UUID
    user_id: UUID
    username: str
    full_name: Optional[str] = None
    headline: Optional[str] = None
    avatar_url: Optional[str] = None
    role: str
    created_at: datetime

    class Config:
        from_attributes = True

# ==================== NOTIFICATIONS ====================
class NotificationResponse(BaseModel):
    id: UUID
    recipient_id: UUID
    actor_id: UUID
    actor_name: str
    actor_username: str
    actor_avatar: Optional[str] = None
    type: str
    related_id: Optional[str] = None
    message: str
    read: bool
    created_at: datetime

    class Config:
        from_attributes = True

# ==================== DASHBOARD ANALYTICS (FOR MANASI) ====================
class ActivityMetricItem(BaseModel):
    date: str
    count: int

class DashboardAnalyticsResponse(BaseModel):
    total_posts: int
    likes_received: int
    comments_received: int
    total_connections: int
    pending_connections: int
    collaboration_requests_sent: int
    collaboration_requests_received: int
    accepted_collaborations: int
    messages_sent: int
    messages_received: int
    projects_shared: int
    activity_timeline: List[ActivityMetricItem] = []
