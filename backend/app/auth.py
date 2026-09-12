"""
Authentication: password hashing, JWT issue/verify, and the FastAPI
dependencies that every protected route hangs off.

Implements FR-1 (authentication) and the enforcement half of FR-2
(authorization). Kept separate from crud.py because none of this touches
business logic — it only answers "who is calling, and may they?"
"""
import os
import secrets
import warnings
from datetime import datetime, timedelta
from typing import Optional

import bcrypt
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from . import models
from .database import get_db

JWT_ALGORITHM = "HS256"

# FR-1.5: staff sessions are shorter than organization sessions, because a
# staff token lives on a shared store device and an org token lives on a
# coordinator's own phone.
STAFF_TOKEN_HOURS = int(os.getenv("STAFF_TOKEN_HOURS", "8"))
ORG_TOKEN_HOURS = int(os.getenv("ORG_TOKEN_HOURS", "24"))

_JWT_SECRET = os.getenv("JWT_SECRET")
if not _JWT_SECRET:
    # Deliberately random rather than a hardcoded default: a checked-in
    # fallback secret is worse than no secret at all, because it looks
    # like it works. This one invalidates every token on restart, which
    # is annoying enough in dev to make you set the variable, and it
    # cannot silently become your production key.
    _JWT_SECRET = secrets.token_urlsafe(48)
    warnings.warn(
        "JWT_SECRET is not set. Generated an ephemeral one — all sessions "
        "will be invalidated when this process restarts, and separate "
        "workers will reject each other's tokens. Set JWT_SECRET in .env.",
        RuntimeWarning,
    )

bearer_scheme = HTTPBearer(auto_error=False)


# ---------- Passwords ----------

# bcrypt hashes at most 72 bytes and raises on anything longer, so both
# sides truncate identically. Anything past 72 bytes adds no entropy that
# bcrypt would have used anyway.
def _encode(password: str) -> bytes:
    return password.encode("utf-8")[:72]


def hash_password(password: str) -> str:
    """NFR-4.1.1: bcrypt at cost 12."""
    return bcrypt.hashpw(_encode(password), bcrypt.gensalt(rounds=12)).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(_encode(password), password_hash.encode("utf-8"))
    except ValueError:
        # Malformed hash in the DB — treat as a failed login, never a 500.
        return False


# ---------- Tokens ----------

def create_access_token(user: models.User) -> tuple[str, datetime]:
    """Returns (token, expiry). Role and pantry ride in the claims so that
    routine authorization checks don't need a database round trip."""
    hours = ORG_TOKEN_HOURS if user.role == models.UserRole.ORG_COORDINATOR else STAFF_TOKEN_HOURS
    expires_at = datetime.utcnow() + timedelta(hours=hours)
    payload = {
        "sub": user.id,
        "email": user.email,
        "role": user.role.value,
        "pantry_id": user.pantry_id,
        "exp": expires_at,
        "iat": datetime.utcnow(),
    }
    return jwt.encode(payload, _JWT_SECRET, algorithm=JWT_ALGORITHM), expires_at


def _decode(token: str) -> dict:
    return jwt.decode(token, _JWT_SECRET, algorithms=[JWT_ALGORITHM])


# ---------- Dependencies ----------

_CREDENTIALS_ERROR = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Not authenticated. Sign in again.",
    headers={"WWW-Authenticate": "Bearer"},
)


def get_current_user(
    creds: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> models.User:
    if creds is None:
        raise _CREDENTIALS_ERROR
    try:
        payload = _decode(creds.credentials)
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Your session expired. Sign in again.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except jwt.PyJWTError:
        raise _CREDENTIALS_ERROR

    user = db.get(models.User, payload.get("sub"))
    # Re-read the user on every request rather than trusting the claims
    # alone: FR-1.9 requires that deactivating an account takes effect
    # immediately, not whenever the token happens to expire.
    if user is None or not user.is_active:
        raise _CREDENTIALS_ERROR
    return user


def require_role(*roles: models.UserRole):
    """Route guard. Usage: `Depends(require_role(UserRole.STAFF, UserRole.MANAGER))`."""

    def dependency(user: models.User = Depends(get_current_user)) -> models.User:
        if user.role not in roles:
            # FR-2.6: denials are worth logging once there is a logger.
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Your account does not have access to this action.",
            )
        return user

    return dependency


# Convenience aliases for the two guards used most often.
STORE_ROLES = (models.UserRole.STAFF, models.UserRole.MANAGER)
ALL_STORE_ROLES = (models.UserRole.STAFF, models.UserRole.MANAGER, models.UserRole.ADMIN)


def require_staff(user: models.User = Depends(require_role(*STORE_ROLES))) -> models.User:
    return user


def require_organizer(
    user: models.User = Depends(require_role(models.UserRole.ORG_COORDINATOR)),
) -> models.User:
    """An organizer without a pantry is a data error, not a valid session —
    every reservation path derives its org from this field (FR-2.3)."""
    if not user.pantry_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This account is not linked to an organization. Contact NourishNet support.",
        )
    return user
