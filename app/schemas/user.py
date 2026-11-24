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
    first_name: str = Field(min_length=1, max_length=60)
    last_name: str = Field(min_length=1, max_length=60)
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


class VerifyEmailRequest(BaseModel):
    pending_id: UUID
    code: str = Field(min_length=6, max_length=6, pattern=r"^\d{6}$")


class ResendVerificationRequest(BaseModel):
    pending_id: UUID


class EmailChangeRequestBody(BaseModel):
    new_email: EmailStr


class EmailChangeConfirmBody(BaseModel):
    request_id: UUID
    code: str = Field(min_length=6, max_length=6, pattern=r"^\d{6}$")

class ChangePasswordRequest(BaseModel):
    current_password: Optional[str] = Field(default=None, max_length=128)  # omitted when setting a first password
    new_password: Password

class GitHubCompleteRequest(BaseModel):
    onboarding_token: str = Field(min_length=10, max_length=2000)
    username: Username
    role: Optional[str] = Field(default=None, max_length=40)
    company: Optional[str] = Field(default=None, max_length=120)
    # Required when GitHub did not provide a verified primary email.
    email: Optional[EmailStr] = None

class UsernameAvailability(BaseModel):
    username: str
    available: bool
    reason: Optional[str] = None  # invalid, reserved, taken

class UsernameChangeRequest(BaseModel):
    username: Username


class AccountConfirmRequest(BaseModel):
    """Confirm a destructive account action with the password (when set) or by matching the username."""
    password: Optional[str] = Field(default=None, max_length=128)
    username: Optional[str] = Field(default=None, max_length=64)


class ScheduleDeleteRequest(AccountConfirmRequest):
    """Must type the account username to schedule permanent deletion."""
    username: Username


class UserResponse(BaseModel):
    id: UUID
    username: str
    email: EmailStr
    created_at: datetime
    has_password: bool = True
    username_needs_choice: bool = False
    name_needs_choice: bool = False
    full_name: Optional[str] = None
    account_type: str = "developer"
    email_verified: bool = True
    deactivated_at: Optional[datetime] = None
    delete_after: Optional[datetime] = None
    suspended_at: Optional[datetime] = None
    is_platform_admin: bool = False
    admin_role: Optional[str] = None
    avatar_url: Optional[str] = None

    class Config:
        from_attributes = True
