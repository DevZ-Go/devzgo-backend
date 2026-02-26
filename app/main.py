"""
Main application file for the DevZGo backend. This sets up the FastAPI app, includes route modules, 
and ensures database tables are created on startup.
The root endpoint provides a simple health check to confirm the backend is running.
"""

from fastapi import FastAPI

# Importing the database base and engine to ensure that the database tables are created when the application starts
from app.db.base import Base
from app.db.session import engine
from app.routes.auth import router as auth_router

from app.routes.projects import router as projects_router

app = FastAPI()

# Create the database tables based on the models defined in the Base metadata
# Base.metadata.create_all(bind=engine)

app.include_router(auth_router)
app.include_router(projects_router)

@app.get("/")
def root():
    return {"message": "DevZGo Backend is running!"}
