from uuid import UUID
from pydantic import AfterValidator, BaseModel, Field
from typing import Annotated, Dict, List, Literal, Optional
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def _link(value: Optional[str]) -> Optional[str]:
    """Empty clears the field; otherwise only http(s) links or our own /storage/ paths."""
    value = (value or "").strip()
    if not value:
        return None
    if not value.startswith(("http://", "https://", "/storage/")):
        raise ValueError("must be an http(s) link")
    return value


Link = Annotated[Optional[str], Field(default=None, max_length=500), AfterValidator(_link)]


def _timezone(value: str) -> str:
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError("Unknown timezone")
    return value


TimeZone = Annotated[str, Field(min_length=1, max_length=64), AfterValidator(_timezone)]
ShortText = Annotated[Optional[str], Field(default=None, max_length=255)]

# ==================== PROFILES ====================
class UserProfileBase(BaseModel):
    full_name: Optional[str] = None
    headline: Optional[str] = None
    role: Optional[str] = None
    bio: Optional[str] = None
    avatar_url: Optional[str] = None
    github_url: Optional[str] = None
    linkedin_url: Optional[str] = None
    website_url: Optional[str] = None
    skills: Optional[str] = None
    location: Optional[str] = None

class UserProfileUpdate(UserProfileBase):
    full_name: ShortText = None
    headline: ShortText = None
    role: Optional[str] = Field(default=None, max_length=40)
    company: Optional[str] = Field(default=None, max_length=120)
    bio: Optional[str] = Field(default=None, max_length=2000)
    avatar_url: Link = None
    github_url: Link = None
    linkedin_url: Link = None
    website_url: Link = None
    skills: Optional[str] = Field(default=None, max_length=1000)
    location: ShortText = None
    timezone: Optional[TimeZone] = None
    profile_visibility: Optional[Literal["public", "connections"]] = None
    message_policy: Optional[Literal["anyone", "connections"]] = None
    comment_policy: Optional[Literal["anyone", "connections"]] = None
    activity_visibility: Optional[Literal["public", "connections"]] = None
    discoverable: Optional[bool] = None
    allow_connection_requests: Optional[bool] = None
    connection_visibility: Optional[Literal["public", "connections", "private"]] = None
    collaboration_policy: Optional[Literal["anyone", "connections", "none"]] = None
    email_visibility: Optional[Literal["public", "connections", "private"]] = None
    resume_visibility: Optional[Literal["public", "connections", "private"]] = None
    notify_likes: Optional[bool] = None
    notify_comments: Optional[bool] = None
    notify_shares: Optional[bool] = None
    notify_connections: Optional[bool] = None
    notify_messages: Optional[bool] = None
    notify_collaborations: Optional[bool] = None

class UserProfileResponse(UserProfileBase):
    id: UUID
    user_id: UUID
    username: str
    email: Optional[str] = None  # only filled in on /profiles/me
    timezone: Optional[str] = None  # only filled in on /profiles/me
    created_at: datetime
    projects_count: int = 0
    connections_count: int = 0
    collaborations_count: int = 0
    company: Optional[str] = None
    guide_seen: Optional[bool] = None
    connection_status: Optional[str] = None  # None, "self", "pending_sent", "pending_received", "connected"
    connection_id: Optional[UUID] = None
    restricted: bool = False
    can_message: bool = True
    accepts_connections: bool = True
    profile_visibility: Optional[str] = None
    message_policy: Optional[str] = None
    comment_policy: Optional[str] = None
    activity_visibility: Optional[str] = None
    discoverable: Optional[bool] = None
    allow_connection_requests: Optional[bool] = None
    connection_visibility: Optional[str] = None
    collaboration_policy: Optional[str] = None
    email_visibility: Optional[str] = None
    resume_url: Optional[str] = None
    resume_visibility: Optional[str] = None
    connections_visible: bool = True
    notify_likes: Optional[bool] = None
    notify_comments: Optional[bool] = None
    notify_shares: Optional[bool] = None
    notify_connections: Optional[bool] = None
    notify_messages: Optional[bool] = None
    notify_collaborations: Optional[bool] = None

    class Config:
        from_attributes = True

class GroupMember(BaseModel):
    username: str
    role: str

class CollabGroup(BaseModel):
    project_id: UUID
    title: str
    visibility: str
    role: str
    members: List[GroupMember]

class LanguageShare(BaseModel):
    name: str
    percentage: float

class TechCount(BaseModel):
    name: str
    count: int

class Standing(BaseModel):
    projects: int
    commits: int
    pushes: int
    connections: Optional[int] = None
    activity: Optional[int] = None
    last_push: Optional[datetime] = None
    languages: List[LanguageShare] = []
    tech: List[TechCount] = []
    top: Dict[str, int] = {}

class TalentCard(BaseModel):
    id: UUID
    username: str
    full_name: Optional[str] = None
    headline: Optional[str] = None
    role: Optional[str] = None
    company: Optional[str] = None
    avatar_url: Optional[str] = None
    location: Optional[str] = None
    skills: Optional[str] = None
    projects: int = 0
    commits: int = 0
    pushes: int = 0
    connections: Optional[int] = None
    activity: Optional[int] = None
    last_push: Optional[datetime] = None
    tech: List[str] = []

# ==================== LIKES & COMMENTS ====================
class ActivityCommentCreate(BaseModel):
    comment_text: str = Field(min_length=1, max_length=2000)

class ActivityCommentResponse(BaseModel):
    id: UUID
    activity_id: UUID
    author_id: UUID
    author_name: str
    author_username: str
    author_avatar: Optional[str] = None
    comment_text: str
    created_at: datetime
    can_delete: bool = False  # the comment's author or the post's author

    class Config:
        from_attributes = True

class ActivityCommentPage(BaseModel):
    items: List[ActivityCommentResponse]
    next_page: Optional[str] = None

class ActivityLikeResponse(BaseModel):
    activity_id: UUID
    likes_count: int
    is_liked: bool

# ==================== FEED / NETWORK ACTIVITIES ====================
class FeedPostCreate(BaseModel):
    title: ShortText = None
    content: str = Field(min_length=1, max_length=5000)
    activity_type: Literal["post", "milestone", "collab_call", "project_update"] = "post"
    project_id: Optional[UUID] = None
    media_url: Link = None

class FeedPostUpdate(BaseModel):
    title: ShortText = None
    content: Optional[str] = Field(default=None, min_length=1, max_length=5000)

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
    likes_count: int = 0
    is_liked: bool = False
    comments_count: int = 0

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
    comments: List[ActivityCommentResponse] = []  # the latest two, oldest first; the rest via /comments
    can_edit: bool = False  # author of a user post (system events cannot be edited)

    class Config:
        from_attributes = True

class FeedPage(BaseModel):
    items: List[FeedActivityResponse]
    next_page: Optional[str] = None

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
    accepted_at: Optional[datetime] = None
    partner: ConnectionUserSummary
    mutual_count: int = 0

    class Config:
        from_attributes = True

class ConnectionPage(BaseModel):
    items: List[ConnectionResponse]
    next_page: Optional[str] = None

class ConnectionStatusResponse(BaseModel):
    user_id: UUID
    status: Optional[str] = None  # None, "pending_sent", "pending_received", "accepted"
    connection_id: Optional[UUID] = None

# ==================== MESSAGES ====================
class MessageCreate(BaseModel):
    receiver_id: UUID
    content: str = Field(min_length=1, max_length=5000)

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


class ChatRoomCreate(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    member_ids: List[UUID] = Field(min_length=1, max_length=20)


class ChatRoomSummary(BaseModel):
    id: UUID
    title: str
    project_id: Optional[UUID] = None
    member_count: int
    unread_count: int = 0
    last_message: Optional[str] = None
    last_at: Optional[datetime] = None


class RoomMessageCreate(BaseModel):
    content: str = Field(min_length=1, max_length=5000)


class RoomMessageResponse(BaseModel):
    id: UUID
    room_id: UUID
    sender_id: UUID
    sender_username: str
    sender_name: str
    content: str
    created_at: datetime
    is_outgoing: bool = False

# ==================== COLLABORATIONS ====================
class CollabPermissions(BaseModel):
    """Optional extras the owner grants a member; viewing the project is always included."""
    can_edit_details: bool = False
    can_manage_files: bool = False
    can_view_secrets: bool = False

class CollaborationRequestCreate(BaseModel):
    message: Optional[str] = Field(default=None, max_length=2000)
    role: str = Field(default="Collaborator", min_length=1, max_length=100)

class InvitationCreate(CollaborationRequestCreate):
    username: str = Field(min_length=1, max_length=30)
    permissions: CollabPermissions = CollabPermissions()

class InvitationResponse(BaseModel):
    id: UUID
    project_id: UUID
    project_title: str
    inviter: ConnectionUserSummary
    invitee: ConnectionUserSummary
    role: str
    message: Optional[str] = None
    permissions: CollabPermissions
    status: str
    created_at: datetime
    expires_at: datetime

class InviteeResult(ConnectionUserSummary):
    status: Literal["member", "invited", "requested", "none"]

class MyCollabStatus(BaseModel):
    status: Literal["owner", "member", "requested", "invited", "none"]
    request_id: Optional[UUID] = None
    invitation_id: Optional[UUID] = None
    permissions: CollabPermissions = CollabPermissions()

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
    is_owner: bool = False
    permissions: CollabPermissions = CollabPermissions()

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
