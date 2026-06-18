"""User repository for Firestore operations."""

from __future__ import annotations

from datetime import datetime, timezone

from config import firebase as firebase_config
from models.user import UserModel


def _to_user_model(uid: str, data: dict | None) -> UserModel | None:
    if not data:
        return None
    created_at = data.get("createdAt")
    if isinstance(created_at, str):
        try:
            created_at = datetime.fromisoformat(created_at)
        except ValueError:
            created_at = None
    return UserModel(
        id=uid,
        firstName=data.get("firstName", ""),
        surname=data.get("surname", ""),
        email=data.get("email", ""),
        role=data.get("role", "employee"),
        language=data.get("language", "en"),
        disabled=bool(data.get("disabled", False)),
        createdAt=created_at,
    )


def create_user(uid: str, first_name: str, surname: str, email: str) -> UserModel:
    payload = {
        "firstName": first_name,
        "surname": surname,
        "email": email,
        "role": "employee",
        "language": "en",
        "disabled": False,
        "createdAt": datetime.now(timezone.utc).isoformat(),
    }
    firebase_config.db.collection("users").document(uid).set(payload)
    return _to_user_model(uid, payload)


def get_user(uid: str) -> UserModel | None:
    doc = firebase_config.db.collection("users").document(uid).get()
    if not doc.exists:
        return None
    return _to_user_model(uid, doc.to_dict())


def update_user(uid: str, data: dict) -> None:
    firebase_config.db.collection("users").document(uid).set(data, merge=True)


def get_all_users(requester_role: str) -> list[UserModel]:
    if requester_role != "admin":
        raise PermissionError("Admin role required to list users.")
    docs = firebase_config.db.collection("users").stream()
    users: list[UserModel] = []
    for doc in docs:
        user = _to_user_model(doc.id, doc.to_dict())
        if user:
            users.append(user)
    return users
