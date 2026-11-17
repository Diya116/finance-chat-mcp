"""
App entry point.   Run from the finance_tracker folder:

    uvicorn backend.main:app --port 8001 --reload

Interactive API docs: http://127.0.0.1:8001/docs
"""

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.sessions import SessionMiddleware

from . import config
from .database import Base, engine
from .finance_routes import router as finance_router
from .oauth_routes import router as oauth_router
from .services import BadRequest
from .web_routes import router as web_router

# Creates tables if missing. For schema changes later, use Alembic migrations.
Base.metadata.create_all(engine)

app = FastAPI(title="Finance Tracker Backend")

# Signed cookie holding the logged-in user id (website only; the API uses bearer tokens).
app.add_middleware(SessionMiddleware, secret_key=config.SECRET_KEY, same_site="lax",
                   https_only=config.HTTPS_ONLY, max_age=14 * 24 * 3600)

# Lets browser-based MCP clients (e.g. the MCP Inspector) read discovery + token endpoints.
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.exception_handler(BadRequest)
async def bad_request_handler(_: Request, exc: BadRequest):
    return JSONResponse({"detail": str(exc)}, status_code=400)


app.include_router(oauth_router)
app.include_router(finance_router)
app.include_router(web_router)
