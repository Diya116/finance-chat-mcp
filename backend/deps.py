"""FastAPI dependencies: "who is calling?" for the API (bearer token) and web pages (session)."""

import hmac
import secrets

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from .database import get_db
from .models import OAuthToken, User
from .security import now, sha256_hex


# ---------- API: Authorization: Bearer <access token> ----------

def current_token(authorization: str | None = Header(None), db: Session = Depends(get_db)) -> OAuthToken:
    """Find the access token row. 401 if missing, unknown, expired or revoked."""
    scheme, _, value = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not value:
        raise HTTPException(401, "Missing bearer token", headers={"WWW-Authenticate": "Bearer"})
    token = db.scalar(select(OAuthToken).where(
        OAuthToken.token_hash == sha256_hex(value.strip()),
        OAuthToken.kind == "access",
        OAuthToken.revoked.is_(False),
        OAuthToken.expires_at > now(),
    ))
    if token is None:
        raise HTTPException(401, "Invalid or expired token", headers={"WWW-Authenticate": "Bearer"})
    return token


def current_user(token: OAuthToken = Depends(current_token), db: Session = Depends(get_db)) -> User:
    return db.get(User, token.user_id)


# ---------- web pages: cookie session ----------

def session_user(request: Request, db: Session = Depends(get_db)) -> User | None:
    user_id = request.session.get("user_id")
    return db.get(User, user_id) if user_id else None


def get_csrf(request: Request) -> str:
    """One random token per session; every HTML form includes it."""
    if "csrf" not in request.session:
        request.session["csrf"] = secrets.token_urlsafe(16)
    return request.session["csrf"]


def check_csrf(request: Request, submitted: str | None) -> None:
    expected = request.session.get("csrf")
    if not expected or not submitted or not hmac.compare_digest(expected, submitted):
        raise HTTPException(403, "Invalid CSRF token. Go back, refresh the page and try again.")
