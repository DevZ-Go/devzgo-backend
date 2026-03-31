"""
Main application file for the DevZGo backend. This sets up the FastAPI app, includes route modules, 
and ensures database tables are created on startup.
The root endpoint provides a simple health check to confirm the backend is running.
"""

from fastapi import FastAPI

# Importing CORSMiddleware to handle Cross-Origin Resource Sharing, allowing the frontend to communicate with the backend
from fastapi.middleware.cors import CORSMiddleware

# Importing the database base and engine to ensure that the database tables are created when the application starts
from app.db.base import Base
from app.db.session import engine
from app.routes.auth import router as auth_router

from app.routes.projects import router as projects_router

app = FastAPI()

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

@app.get("/")
def root():
    return {"message": "DevZGo Backend is running!"}
