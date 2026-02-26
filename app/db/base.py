from app.db.session import Base

# Import models so that Alembic can detect them when generating migrations
from app.models.user import User
from app.models.project import Project
from app.models.techstack import TechStack