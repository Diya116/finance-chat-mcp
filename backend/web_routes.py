"""The small website: landing, signup, login, dashboard, connected apps.

Login has to live on a web page (OAuth sends the user's browser here). Everything else
the user does through chat.
"""

from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import config, services
from .database import get_db
from .deps import check_csrf, get_csrf, session_user
from .models import OAuthClient, OAuthToken, User
from .security import DUMMY_HASH, hash_password, now, verify_password

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")  # autoescapes HTML
router = APIRouter(include_in_schema=False)


def safe_next(target: str | None) -> str:
    """Only allow redirects to paths on this site (prevents open-redirect attacks)."""
    if target and target.startswith("/") and not target.startswith("//") and "\\" not in target:
        return target
    return "/dashboard"


def page(request: Request, name: str, status_code: int = 200, **context):
    context.setdefault("csrf", get_csrf(request))
    context.setdefault("mcp_url", config.MCP_URL)
    return templates.TemplateResponse(request, name, context, status_code=status_code)


def log_in(request: Request, user: User) -> None:
    request.session.clear()  # fresh session on login (prevents session fixation)
    request.session["user_id"] = user.id


@router.get("/")
def landing(request: Request, user: User | None = Depends(session_user)):
    return page(request, "landing.html", user=user)


# ---------------- signup / login / logout ----------------

@router.get("/signup")
def signup_form(request: Request, user: User | None = Depends(session_user)):
    if user:
        return RedirectResponse("/dashboard", status_code=303)
    return page(request, "signup.html", next=request.query_params.get("next", ""))


@router.post("/signup")
def signup(request: Request, email: str = Form(""), password: str = Form(""), next: str = Form(""),
           csrf: str = Form(""), db: Session = Depends(get_db)):
    check_csrf(request, csrf)
    email = email.strip().lower()
    error = None
    if "@" not in email or len(email) > 255:
        error = "Please enter a valid email."
    elif len(password) < 8:
        error = "Password must be at least 8 characters."
    elif db.scalar(select(User).where(User.email == email)):
        error = "An account with this email already exists."
    if error:
        return page(request, "signup.html", status_code=400, error=error, email=email, next=next)

    user = User(email=email, password_hash=hash_password(password))
    db.add(user)
    try:
        db.commit()
    except IntegrityError:  # two signups at the same moment
        db.rollback()
        return page(request, "signup.html", status_code=400, error="That email is taken.", email=email, next=next)
    log_in(request, user)
    return RedirectResponse(safe_next(next), status_code=303)


@router.get("/login")
def login_form(request: Request, user: User | None = Depends(session_user)):
    next_url = request.query_params.get("next", "")
    if user:
        return RedirectResponse(safe_next(next_url), status_code=303)
    return page(request, "login.html", next=next_url)


@router.post("/login")
def login(request: Request, email: str = Form(""), password: str = Form(""), next: str = Form(""),
          csrf: str = Form(""), db: Session = Depends(get_db)):
    check_csrf(request, csrf)
    user = db.scalar(select(User).where(User.email == email.strip().lower()))
    # Always run a hash check so response time doesn't reveal whether the email exists.
    password_ok = verify_password(password, user.password_hash if user else DUMMY_HASH)
    if not user or not password_ok:
        return page(request, "login.html", status_code=400, error="Invalid email or password.",
                    email=email, next=next)
    log_in(request, user)
    return RedirectResponse(safe_next(next), status_code=303)


@router.post("/logout")
def logout(request: Request, csrf: str = Form("")):
    check_csrf(request, csrf)
    request.session.clear()
    return RedirectResponse("/", status_code=303)


# ---------------- dashboard ----------------

@router.get("/dashboard")
def dashboard(request: Request, db: Session = Depends(get_db), user: User | None = Depends(session_user)):
    if user is None:
        return RedirectResponse("/login?next=/dashboard", status_code=303)

    # Apps (Claude, ...) that currently hold a valid refresh token for this user
    connected = db.scalars(
        select(OAuthClient).where(OAuthClient.client_id.in_(
            select(OAuthToken.client_id).where(
                OAuthToken.user_id == user.id, OAuthToken.kind == "refresh",
                OAuthToken.revoked.is_(False), OAuthToken.expires_at > now())))
    ).all()

    return page(request, "dashboard.html", user=user,
                summary=services.summary(db, user.id),
                budgets=services.budget_status(db, user.id),
                expenses=services.list_expenses(db, user.id, limit=10),
                connected=connected)


@router.post("/apps/{client_id}/revoke")
def revoke_app(client_id: str, request: Request, csrf: str = Form(""), db: Session = Depends(get_db),
               user: User | None = Depends(session_user)):
    check_csrf(request, csrf)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    db.execute(update(OAuthToken)
               .where(OAuthToken.user_id == user.id, OAuthToken.client_id == client_id)
               .values(revoked=True))
    db.commit()
    return RedirectResponse("/dashboard", status_code=303)
