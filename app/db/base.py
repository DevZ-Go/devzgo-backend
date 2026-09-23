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
from app.models.collaboration import CollaborationRequest, ProjectCollaborator
from app.models.notification import Notification