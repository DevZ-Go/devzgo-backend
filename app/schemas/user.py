from datetime import datetime
from typing import Annotated, Optional
from uuid import UUID

from pydantic import AfterValidator, BaseModel, BeforeValidator, EmailStr, Field

from app.core.policy import normalize_username, password_problem, username_problem


def _username(value: str) -> str:
    if problem := username_problem(value):
        raise ValueError("Username is reserved" if problem == "reserved" else
                         "Username must be 3-30 characters: letters, numbers, _ . - (not starting or ending with . or -)")
    return value


def _password(value: str) -> str:
    if problem := password_problem(value):
        raise ValueError(problem)
    return value


Username = Annotated[str, BeforeValidator(lambda v: normalize_username(v) if isinstance(v, str) else v), AfterValidator(_username)]
Password = Annotated[str, AfterValidator(_password)]


class UserCreate(BaseModel):
    username: Username
    email: EmailStr
    password: Password
    role: Optional[str] = Field(default=None, max_length=40)
    company: Optional[str] = Field(default=None, max_length=120)

class UserLogin(BaseModel):
    email: EmailStr
    password: str

class ForgotPasswordRequest(BaseModel):
    # The account's email address or its username.
    email: str = Field(min_length=1, max_length=255)

class ResetPasswordRequest(BaseModel):
    token: str = Field(min_length=10, max_length=200)
    new_password: Password

class ChangePasswordRequest(BaseModel):
    current_password: Optional[str] = Field(default=None, max_length=128)  # omitted when setting a first password
    new_password: Password

class GitHubCompleteRequest(BaseModel):
    onboarding_token: str = Field(min_length=10, max_length=2000)
    username: Username
    role: Optional[str] = Field(default=None, max_length=40)
    company: Optional[str] = Field(default=None, max_length=120)

class UsernameAvailability(BaseModel):
    username: str
    available: bool
    reason: Optional[str] = None  # invalid, reserved, taken

class UsernameChangeRequest(BaseModel):
    username: Username

class UserResponse(BaseModel):
    id: UUID
    username: str
    email: EmailStr
    created_at: datetime
    has_password: bool = True
    username_needs_choice: bool = False
    account_type: str = "developer"

    class Config:
        from_attributes = True
