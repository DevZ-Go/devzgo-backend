from enum import Enum

class ProjectCategory(str, Enum):
    AI_ML = "AI/ML"
    WEB_DEVELOPMENT = "Web Development"
    Mobile = "Mobile"
    GAMING = "Gaming"
    EDUCATION = "Education"
    PRODUCTIVITY = "Productivity"
    OTHER = "Other"

class ProjectVisibility(str, Enum):
    PUBLIC = "Public"
    PRIVATE = "Private"