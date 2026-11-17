"""
OAuth 2.1 authorization server (what makes "Add connector -> log in -> Allow" work).

The flow, step by step:
  1. Client (e.g. Claude) discovers us:       GET  /.well-known/oauth-authorization-server
  2. Client registers itself (once):          POST /oauth/register       -> client_id
  3. Client sends the user's browser here:    GET  /oauth/authorize      -> login + consent page
  4. User clicks Allow:                       POST /oauth/authorize      -> redirect back with ?code=...
  5. Client swaps the code for tokens:        POST /oauth/token          -> access + refresh token
  6. Client calls the MCP server with         Authorization: Bearer <access token>
  7. Access token expires (1h):               POST /oauth/token (grant_type=refresh_token)

Security pieces: PKCE (S256) is mandatory, redirect_uri must match exactly what the client
registered, codes are single-use and expire in 5 minutes, refresh tokens rotate, and codes/tokens
are stored hashed.
"""

import secrets
from urllib.parse import quote, urlencode, urlsplit, urlunsplit, parse_qsl

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import config
from .database import get_db
from .deps import check_csrf, get_csrf, session_user
from .models import AuthCode, OAuthClient, OAuthToken, User
from .security import new_token, now, pkce_challenge, sha256_hex
from .web_routes import templates

router = APIRouter(tags=["oauth"])

NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}
LOCAL_HOSTS = {"localhost", "127.0.0.1", "[::1]", "::1"}


def oauth_error(error: str, description: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"error": error, "error_description": description}, status, headers=NO_STORE)


# ------------------------------------------------------------------ 1. discovery

@router.get("/.well-known/oauth-authorization-server")
def metadata():
    return {
        "issuer": config.BASE_URL,
        "authorization_endpoint": f"{config.BASE_URL}/oauth/authorize",
        "token_endpoint": f"{config.BASE_URL}/oauth/token",
        "registration_endpoint": f"{config.BASE_URL}/oauth/register",
        "revocation_endpoint": f"{config.BASE_URL}/oauth/revoke",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none"],  # public clients + PKCE
        "scopes_supported": [config.SCOPE],
    }


# ------------------------------------------------------------------ 2. dynamic client registration

def valid_redirect_uri(uri: str) -> bool:
    parts = urlsplit(uri)
    if not parts.scheme or parts.fragment:
        return False
    if parts.scheme == "http":  # plain http only for local development clients
        return parts.hostname in LOCAL_HOSTS
    return True  # https, or custom app schemes like cursor://


@router.post("/oauth/register", status_code=201)
async def register_client(request: Request, db: Session = Depends(get_db)):
    try:
        body = await request.json()
        redirect_uris = body["redirect_uris"]
        assert isinstance(redirect_uris, list) and redirect_uris
        assert all(isinstance(u, str) and valid_redirect_uri(u) for u in redirect_uris)
    except Exception:
        return oauth_error("invalid_redirect_uri", "redirect_uris must be a non-empty list of valid URIs")

    client = OAuthClient(client_id=secrets.token_urlsafe(16),
                         client_name=str(body.get("client_name") or "Unnamed app")[:200],
                         redirect_uris=redirect_uris)
    db.add(client)
    db.commit()
    return {
        "client_id": client.client_id,
        "client_name": client.client_name,
        "redirect_uris": client.redirect_uris,
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
        "scope": config.SCOPE,
    }


# ------------------------------------------------------------------ 3 + 4. authorize (login + consent)

class AuthorizeError(Exception):
    """Problem with the authorize request itself; shown to the user as an error page."""


def validate_authorize_request(db: Session, client_id: str, redirect_uri: str, response_type: str,
                               code_challenge: str, code_challenge_method: str) -> OAuthClient:
    client = db.get(OAuthClient, client_id)
    if client is None:
        raise AuthorizeError("Unknown application.")
    if redirect_uri not in client.redirect_uris:  # exact match, no wildcards
        raise AuthorizeError("redirect_uri does not match the application's registration.")
    if response_type != "code":
        raise AuthorizeError("Only response_type=code is supported.")
    if not code_challenge or code_challenge_method != "S256":
        raise AuthorizeError("PKCE with code_challenge_method=S256 is required.")
    return client


def redirect_with(uri: str, **params) -> str:
    """Add query parameters to the client's redirect URI."""
    parts = urlsplit(uri)
    query = parse_qsl(parts.query) + [(k, v) for k, v in params.items() if v is not None]
    return urlunsplit(parts._replace(query=urlencode(query)))


@router.get("/oauth/authorize")
def authorize_page(request: Request, client_id: str = "", redirect_uri: str = "", response_type: str = "",
                   code_challenge: str = "", code_challenge_method: str = "", state: str = "",
                   resource: str = "", db: Session = Depends(get_db),
                   user: User | None = Depends(session_user)):
    try:
        client = validate_authorize_request(db, client_id, redirect_uri, response_type,
                                            code_challenge, code_challenge_method)
    except AuthorizeError as e:
        return templates.TemplateResponse(request, "error.html", {"message": str(e)}, status_code=400)

    if user is None:  # not logged in: go to login, then come back to this exact URL
        back = request.url.path + "?" + request.url.query
        return RedirectResponse(f"/login?next={quote(back, safe='')}", status_code=303)

    return templates.TemplateResponse(request, "consent.html", {
        "csrf": get_csrf(request), "user": user, "client": client, "scope": config.SCOPE,
        "fields": {"client_id": client_id, "redirect_uri": redirect_uri, "response_type": response_type,
                   "code_challenge": code_challenge, "code_challenge_method": code_challenge_method,
                   "state": state, "resource": resource},
    })


@router.post("/oauth/authorize")
def authorize_decision(request: Request, client_id: str = Form(""), redirect_uri: str = Form(""),
                       response_type: str = Form(""), code_challenge: str = Form(""),
                       code_challenge_method: str = Form(""), state: str = Form(""),
                       decision: str = Form(""), csrf: str = Form(""),
                       db: Session = Depends(get_db), user: User | None = Depends(session_user)):
    check_csrf(request, csrf)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    try:  # never trust hidden form fields: validate everything again
        validate_authorize_request(db, client_id, redirect_uri, response_type,
                                   code_challenge, code_challenge_method)
    except AuthorizeError as e:
        return templates.TemplateResponse(request, "error.html", {"message": str(e)}, status_code=400)

    if decision != "approve":
        return RedirectResponse(redirect_with(redirect_uri, error="access_denied", state=state or None),
                                status_code=303)

    code = new_token()
    db.add(AuthCode(code_hash=sha256_hex(code), client_id=client_id, user_id=user.id,
                    redirect_uri=redirect_uri, code_challenge=code_challenge, scope=config.SCOPE,
                    expires_at=now() + config.AUTH_CODE_TTL))
    db.commit()
    return RedirectResponse(redirect_with(redirect_uri, code=code, state=state or None), status_code=303)


# ------------------------------------------------------------------ 5 + 7. token endpoint

def issue_tokens(db: Session, user_id: int, client_id: str, scope: str) -> JSONResponse:
    access, refresh = new_token(), new_token()
    db.add_all([
        OAuthToken(token_hash=sha256_hex(access), kind="access", user_id=user_id, client_id=client_id,
                   scope=scope, expires_at=now() + config.ACCESS_TOKEN_TTL),
        OAuthToken(token_hash=sha256_hex(refresh), kind="refresh", user_id=user_id, client_id=client_id,
                   scope=scope, expires_at=now() + config.REFRESH_TOKEN_TTL),
    ])
    db.commit()
    return JSONResponse({"access_token": access, "token_type": "Bearer",
                         "expires_in": config.ACCESS_TOKEN_TTL, "refresh_token": refresh,
                         "scope": scope}, headers=NO_STORE)


@router.post("/oauth/token")
def token_endpoint(grant_type: str = Form(...), client_id: str = Form(...),
                   code: str | None = Form(None), redirect_uri: str | None = Form(None),
                   code_verifier: str | None = Form(None), refresh_token: str | None = Form(None),
                   db: Session = Depends(get_db)):
    if db.get(OAuthClient, client_id) is None:
        return oauth_error("invalid_client", "Unknown client_id", 401)

    if grant_type == "authorization_code":
        if not (code and redirect_uri and code_verifier):
            return oauth_error("invalid_request", "code, redirect_uri and code_verifier are required")
        auth_code = db.get(AuthCode, sha256_hex(code))
        if (auth_code is None or auth_code.used or auth_code.expires_at < now()
                or auth_code.client_id != client_id or auth_code.redirect_uri != redirect_uri):
            return oauth_error("invalid_grant", "Invalid, used or expired authorization code")
        auth_code.used = True  # single use: burn it even if PKCE fails below
        db.commit()
        if not secrets.compare_digest(pkce_challenge(code_verifier), auth_code.code_challenge):
            return oauth_error("invalid_grant", "PKCE verification failed")
        return issue_tokens(db, auth_code.user_id, client_id, auth_code.scope)

    if grant_type == "refresh_token":
        if not refresh_token:
            return oauth_error("invalid_request", "refresh_token is required")
        old = db.scalar(select(OAuthToken).where(
            OAuthToken.token_hash == sha256_hex(refresh_token), OAuthToken.kind == "refresh",
            OAuthToken.client_id == client_id, OAuthToken.revoked.is_(False),
            OAuthToken.expires_at > now()))
        if old is None:
            return oauth_error("invalid_grant", "Invalid or expired refresh token")
        old.revoked = True  # rotation: a refresh token can be used only once
        db.commit()
        return issue_tokens(db, old.user_id, client_id, old.scope)

    return oauth_error("unsupported_grant_type", f"Unsupported grant_type: {grant_type}")


# ------------------------------------------------------------------ revoke (RFC 7009)

@router.post("/oauth/revoke")
def revoke(token: str = Form(...), db: Session = Depends(get_db)):
    row = db.scalar(select(OAuthToken).where(OAuthToken.token_hash == sha256_hex(token)))
    if row:
        row.revoked = True
        db.commit()
    return JSONResponse({}, headers=NO_STORE)  # always 200, never reveal if the token existed
