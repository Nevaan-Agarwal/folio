"""User model."""

from datetime import datetime
from dataclasses import dataclass


@dataclass
class UserModel:
    id: str
    firstName: str
    surname: str
    email: str
    passwordHash: str = ""
    role: str = ""
    language: str = "en"
    disabled: bool = False
    onboardingCompleted: bool = False
    createdAt: datetime = None
