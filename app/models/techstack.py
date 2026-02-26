from sqlalchemy import Column, Integer, String
from sqlalchemy.orm import relationship
from app.db.session import Base

class TechStack(Base):
    __tablename__ = 'techstacks'

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, index=True, nullable=False)

    projects = relationship(
        "Project",
        secondary="project_tech",
        back_populates="tech_stacks"
        )