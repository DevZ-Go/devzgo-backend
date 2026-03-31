"""
Main application file for the DevZGo backend. This sets up the FastAPI app, includes route modules, 
and ensures database tables are created on startup.
The root endpoint provides a simple health check to confirm the backend is running.
"""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import OperationalError
from fastapi.staticfiles import StaticFiles
from pathlib import Path

# Importing CORSMiddleware to handle Cross-Origin Resource Sharing, allowing the frontend to communicate with the backend
from fastapi.middleware.cors import CORSMiddleware

# Importing the database base and engine to ensure that the database tables are created when the application starts
from app.db.base import Base
from app.db.session import engine
from app.routes.auth import router as auth_router

from app.routes.projects import router as projects_router

app = FastAPI()


@app.exception_handler(OperationalError)
async def database_unreachable_handler(_request: Request, _exc: OperationalError):
    """Return 503 when PostgreSQL (or DATABASE_URL) is down instead of a 500 stack trace."""
    return JSONResponse(
        status_code=503,
        content={
            "detail": "Cannot connect to the database. Start PostgreSQL on port 5432, then run alembic upgrade head. Or set DATABASE_URL in devzgo-backend/.env to your Postgres instance.",
        },
    )


# Configure CORS to allow requests from the frontend (adjust origins as needed)
origins = [
    "http://localhost:3000",  # React development server
    "http://127.0.0.1:3000",  # React development server
    "http://localhost:5173",  # FastAPI development server
    "http://127.0.0.1:5173",  # FastAPI development server
]


app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,  # Allow all origins for development; specify in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Create the database tables based on the models defined in the Base metadata
# Base.metadata.create_all(bind=engine)

app.include_router(auth_router)
app.include_router(projects_router)

# ---------------- Static uploads ----------------
# Files will be stored on disk and served at `/uploads/...`
UPLOAD_DIR = Path(__file__).resolve().parents[1] / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=str(UPLOAD_DIR)), name="uploads")

@app.get("/")
def root():
    return {"message": "DevZGo Backend is running!"}
