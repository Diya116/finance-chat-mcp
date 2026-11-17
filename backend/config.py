"""Settings, read from environment variables (see .env.example)."""

import os

from dotenv import load_dotenv

# Reads finance_tracker/.env (if present) into environment variables.
# Variables already set in the real environment (e.g. on a hosting platform) win.
load_dotenv()

# Signs the login-session cookie. MUST be set to a long random value in production.
SECRET_KEY = os.environ.get("SECRET_KEY", "dev-only-change-me")

# SQLite for local dev. For production use Postgres, e.g.
#   postgresql+psycopg://user:pass@host/dbname
DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///finance.db")

# Public URL of THIS backend, as the browser/clients see it. It is the OAuth "issuer".
BASE_URL = os.environ.get("BASE_URL", "http://127.0.0.1:8001").rstrip("/")

# Public URL of the MCP server (only used to show instructions on the web pages).
MCP_URL = os.environ.get("MCP_URL", "http://127.0.0.1:8000/mcp")

# Token lifetimes (seconds)
AUTH_CODE_TTL = 5 * 60              # authorization code: 5 minutes, single use
ACCESS_TOKEN_TTL = 60 * 60          # access token: 1 hour
REFRESH_TOKEN_TTL = 30 * 24 * 3600  # refresh token: 30 days

SCOPE = "finance"  # the single scope this app uses
HTTPS_ONLY = BASE_URL.startswith("https://")  # secure cookies when deployed on https
