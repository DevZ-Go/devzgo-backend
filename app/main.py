"""
Main application file for the DevZGo backend. This sets up the FastAPI app, includes route modules, 
and ensures database tables are created on startup.
The root endpoint provides a simple health check to confirm the backend is running.
"""

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

# Importing CORSMiddleware to handle Cross-Origin Resource Sharing, allowing the frontend to communicate with the backend
from fastapi.middleware.cors import CORSMiddleware

# Importing the database base and engine to ensure that the database tables are created when the application starts
from app.db.base import Base
from app.db.session import engine
from app.routes.auth import router as auth_router

from app.routes.projects import router as projects_router

from fastapi.staticfiles import StaticFiles

from app.routes.media import router as media_router

app = FastAPI(
    title="DevZGo API",
    description=(
        "**Authentication in Swagger:** click **Authorize** (lock), choose **HTTPBearer**, and paste "
        "only the JWT value returned as `access_token` from **POST /auth/login** (not the word Bearer). "
        "Then try protected routes such as **PUT** and **DELETE** on `/projects/{project_id}`.\n\n"
        "Under `/projects/{project_id}` you should see **get**, **put**, and **delete** — expand that path if the UI is collapsed."
    ),
    version="1.0.0",
)

# CORS must run early so browser preflight (OPTIONS) on JSON POSTs — e.g. /auth/register —
# gets Access-Control-* headers. Login uses form-urlencoded and often skips preflight, which
# is why register can fail while login appears to work.
origins = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    # Any localhost / 127.0.0.1 port (Vite port changes, teammates, etc.)
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?$",
    allow_credentials=True,
    # Explicit methods so DELETE is never omitted by proxies or middleware
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
    allow_headers=["*"],
)

app.mount("/storage", StaticFiles(directory="storage"), name="storage")
app.include_router(media_router)

# Create the database tables based on the models defined in the Base metadata
# Base.metadata.create_all(bind=engine)

app.include_router(auth_router)
app.include_router(projects_router)

static_dir = Path(__file__).resolve().parent / "static"
static_dir.mkdir(parents=True, exist_ok=True)
(static_dir / "covers").mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

@app.get("/")
def root():
    return {"message": "DevZGo Backend is running!"}
