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
    role: str = "employee"
    language: str = "en"
    disabled: bool = False
    createdAt: datetime = None
