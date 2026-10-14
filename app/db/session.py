import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

# Local default; set DATABASE_URL (e.g. Neon, with ?sslmode=require) to use another database.
DATABASE_URL = (
    os.getenv("DATABASE_URL") or "postgresql://devzgo:devzgo@localhost:5432/devzgo_db"
).replace("postgres://", "postgresql://", 1)

# Serverless Postgres (Neon) closes idle connections when it scales to zero.
engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_recycle=300)

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine
)

Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
