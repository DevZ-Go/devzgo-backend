from app.db.session import Base

# Import models so that Alembic and SQLAlchemy can detect them when generating migrations / creating tables
from app.models.user import User
from app.models.profile import UserProfile
from app.models.project import Project
from app.models.techstack import TechStack
from app.models.associations import project_tech
from app.models.file import File
from app.models.network import NetworkActivity, ActivityLike, ActivityComment
from app.models.connection import Connection
from app.models.message import Message
from app.models.chat import ChatRoom, ChatMember, RoomMessage
from app.models.collaboration import CollaborationRequest, ProjectCollaborator, ProjectInvitation
from app.models.github_history import ProjectCommit, ProjectContributor, ProjectPush
from app.models.notification import Notification
from app.models.project_like import ProjectLike
from app.models.project_comment import ProjectComment
from app.models.password_reset import PasswordResetToken
from app.models.monthly_wrap import MonthlyWrap
from app.models.recruiting import RecruitmentRequest, Shortlist, ShortlistEntry
from app.models.challenge import ChallengeInvitation, ChallengeReview, ChallengeSubmission, TechnicalChallenge
from app.models.workspace import GitHubConnection, WorkspaceAuditEvent, WorkspaceDraft, WorkspaceSyncJob