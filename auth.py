"""Auth helpers — password hashing and session management."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import bcrypt

from database import (
    create_session,
    delete_session,
    get_session,
    get_user_by_id,
)


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode(), hashed.encode())
    except Exception:
        return False


def make_session(user_id: str) -> str:
    """Create a 7-day session; returns the new session_id cookie value."""
    expires_at = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
    return create_session(user_id, expires_at)


def resolve_session(session_id: str | None) -> dict | None:
    """Return the user dict if the session is valid, else None."""
    if not session_id:
        return None
    row = get_session(session_id)
    if not row:
        return None
    if datetime.fromisoformat(row["expires_at"]) < datetime.now(timezone.utc):
        delete_session(session_id)
        return None
    return get_user_by_id(row["user_id"])


def invalidate_session(session_id: str) -> None:
    delete_session(session_id)
