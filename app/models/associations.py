from sqlalchemy import Table, Column, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from app.db.session import Base

# Association table for many-to-many relationship between Project and TechStack
project_tech = Table(
    'project_tech',
    Base.metadata,
    Column('project_id', UUID(as_uuid=True), ForeignKey('projects.id'), primary_key=True),
    Column('tech_id', ForeignKey('techstacks.id'), primary_key=True)
)