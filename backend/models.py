"""Database tables (SQLAlchemy 2.0 style).

Two groups:
  - App data:  User, Expense, Budget
  - OAuth:     OAuthClient (apps like Claude), AuthCode, OAuthToken

Notes:
  - Money is stored as integer paise/cents (amount_paise) to avoid float rounding bugs.
  - Expiry times are integer unix timestamps (simple to compare, no timezone traps).
  - Codes and tokens are stored only as SHA-256 hashes, never in plain text.
"""

from datetime import date, datetime, timezone

from sqlalchemy import JSON, Boolean, Date, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Expense(Base):
    __tablename__ = "expenses"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    amount_paise: Mapped[int] = mapped_column(Integer)
    category: Mapped[str] = mapped_column(String(50), index=True)
    note: Mapped[str] = mapped_column(String(255), default="")
    spent_on: Mapped[date] = mapped_column(Date, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Budget(Base):
    __tablename__ = "budgets"
    __table_args__ = (UniqueConstraint("user_id", "category"),)  # one budget per category per user

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    category: Mapped[str] = mapped_column(String(50))
    monthly_limit_paise: Mapped[int] = mapped_column(Integer)


# ---------------- OAuth ----------------

class OAuthClient(Base):
    """An app that registered itself (Dynamic Client Registration), e.g. Claude."""
    __tablename__ = "oauth_clients"

    client_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    client_name: Mapped[str] = mapped_column(String(200))
    redirect_uris: Mapped[list] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AuthCode(Base):
    """Short-lived, single-use code handed to the client after the user clicks Allow."""
    __tablename__ = "oauth_auth_codes"

    code_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    client_id: Mapped[str] = mapped_column(ForeignKey("oauth_clients.client_id"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    redirect_uri: Mapped[str] = mapped_column(String(2000))
    code_challenge: Mapped[str] = mapped_column(String(128))  # PKCE
    scope: Mapped[str] = mapped_column(String(200))
    expires_at: Mapped[int] = mapped_column(Integer)
    used: Mapped[bool] = mapped_column(Boolean, default=False)


class OAuthToken(Base):
    """Access and refresh tokens (opaque random strings, stored hashed)."""
    __tablename__ = "oauth_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    kind: Mapped[str] = mapped_column(String(10))  # "access" or "refresh"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    client_id: Mapped[str] = mapped_column(ForeignKey("oauth_clients.client_id"), index=True)
    scope: Mapped[str] = mapped_column(String(200))
    expires_at: Mapped[int] = mapped_column(Integer)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
