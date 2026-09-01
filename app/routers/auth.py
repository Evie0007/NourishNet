from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from .. import auth, crud, models, schemas
from ..database import get_db

router = APIRouter(prefix="/auth", tags=["auth"])


def _to_auth_user(user: models.User) -> schemas.AuthUserOut:
    return schemas.AuthUserOut(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        role=user.role,
        pantry_id=user.pantry_id,
        pantry_name=user.pantry.org_name if user.pantry else None,
        pantry_verified=user.pantry.verified if user.pantry else None,
    )


@router.post("/login", response_model=schemas.TokenResponse)
def login(payload: schemas.LoginRequest, db: Session = Depends(get_db)):
    """
    One login for both staff and organizers (FR-1.4). The client reads
    `user.role` off the response to decide which dashboard to show — it
    never infers the role from the URL.
    """
    user = crud.get_user_by_email(db, payload.email)

    # FR-1.3: an unknown email and a wrong password must be
    # indistinguishable. Hashing against a dummy value on the unknown-email
    # path keeps the timing comparable, so the response can't be used to
    # enumerate which accounts exist.
    if user is None:
        auth.verify_password(payload.password, auth.hash_password("nonexistent-account"))
        raise _invalid_credentials()

    if not auth.verify_password(payload.password, user.password_hash):
        raise _invalid_credentials()

    # A deactivated account gets the same generic message (UC-01, flow 3c):
    # deactivation is not disclosed at the login boundary.
    if not user.is_active:
        raise _invalid_credentials()

    token, expires_at = auth.create_access_token(user)

    user.last_login_at = datetime.utcnow()
    db.commit()

    return schemas.TokenResponse(
        access_token=token,
        expires_at=expires_at,
        user=_to_auth_user(user),
    )


@router.get("/me", response_model=schemas.AuthUserOut)
def me(user: models.User = Depends(auth.get_current_user)):
    """FR-1.10. The client calls this on load to restore a session from a
    stored token, so a refresh doesn't bounce the user back to login."""
    return _to_auth_user(user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(user: models.User = Depends(auth.get_current_user)):
    """
    Client-side only for now: the browser drops the token. FR-1.6 wants
    server-side invalidation too, which needs a token denylist or short-lived
    tokens plus refresh — deliberately deferred, not overlooked.
    """
    return None


def _invalid_credentials() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Email or password is incorrect.",
    )
