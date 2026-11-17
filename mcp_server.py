"""
Finance MCP server = the "chat UI" for the backend. It holds NO data and NO business logic.

How auth works here:
  - The backend is the OAuth *authorization server* (login page, tokens).
  - This server is the OAuth *resource server*: for every request it checks the
    "Authorization: Bearer <token>" header, then forwards the same token to the backend.
  - The backend decides which user the token belongs to, so each user only sees their own data.
  - FastMCP automatically publishes /.well-known/oauth-protected-resource, which tells clients
    (Claude, ...) "log in at the backend", and answers 401 when there is no valid token.

Run (from the finance_tracker folder):  python mcp_server.py
Env vars: see .env.example
"""

import os

import httpx
from dotenv import load_dotenv
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import FastMCP
from pydantic import AnyHttpUrl

load_dotenv()  # read finance_tracker/.env into environment variables

# Where this server reaches the backend (can be a private/internal address)...
BACKEND_URL = os.environ.get("BACKEND_URL", "http://127.0.0.1:8001").rstrip("/")
# ...and the backend's PUBLIC address (the OAuth issuer that clients are told to log in at).
PUBLIC_BACKEND_URL = os.environ.get("PUBLIC_BACKEND_URL", BACKEND_URL)
# Public address of THIS server's MCP endpoint.
PUBLIC_MCP_URL = os.environ.get("PUBLIC_MCP_URL", "http://127.0.0.1:8000/mcp")

SCOPE = "finance"


class BackendTokenVerifier:
    """Asks the backend 'is this token valid?' (GET /api/me). Returns None if not."""

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.get(f"{BACKEND_URL}/api/me",
                                            headers={"Authorization": f"Bearer {token}"})
        except httpx.HTTPError:
            return None
        if response.status_code != 200:
            return None
        info = response.json()
        return AccessToken(token=token, client_id=info["client_id"], scopes=info["scopes"],
                           expires_at=info["expires_at"], subject=str(info["user_id"]))


mcp = FastMCP(
    "finance-tracker",
    token_verifier=BackendTokenVerifier(),
    auth=AuthSettings(
        issuer_url=AnyHttpUrl(PUBLIC_BACKEND_URL),
        resource_server_url=AnyHttpUrl(PUBLIC_MCP_URL),
        required_scopes=[SCOPE],
        # Our backend issues tokens only for this app, so we don't bind tokens to a resource URL.
        validate_token_resource=False,
    ),
    host=os.environ.get("HOST", "127.0.0.1"),
    port=int(os.environ.get("PORT", "8000")),
)


async def call_backend(method: str, path: str, **kwargs):
    """Every tool uses this: forward the caller's token to the backend, return JSON or an error dict."""
    access = get_access_token()  # set by FastMCP's auth middleware for this request
    if access is None:
        return {"error": "Not authenticated"}
    try:
        async with httpx.AsyncClient(base_url=BACKEND_URL, timeout=10) as client:
            response = await client.request(method, path, headers={"Authorization": f"Bearer {access.token}"},
                                            **kwargs)
    except httpx.HTTPError as e:
        return {"error": f"Backend unreachable: {e}"}
    if response.status_code >= 400:
        detail = response.json().get("detail", response.text)
        return {"error": detail if isinstance(detail, str) else "Invalid input", "details": detail}
    return response.json()


@mcp.tool()
async def add_expense(amount: float, category: str, note: str = "", date: str = "") -> dict:
    """Record an expense. Example: "I spent 450 on groceries".

    Args:
        amount: Amount spent (positive number).
        category: Short category word such as food, groceries, travel, rent, bills.
        note: Optional description, e.g. "dinner with friends".
        date: Optional date as YYYY-MM-DD. Leave empty for today.
    """
    body = {"amount": amount, "category": category, "note": note, "spent_on": date or None}
    return await call_backend("POST", "/api/expenses", json=body)


@mcp.tool()
async def list_expenses(month: str = "", limit: int = 20) -> list | dict:
    """List expenses, newest first. Each has an id (needed for delete_expense).

    Args:
        month: Month as YYYY-MM. Leave empty for the current month.
        limit: Maximum number of expenses to return (1-100).
    """
    return await call_backend("GET", "/api/expenses", params={"month": month or None, "limit": limit})


@mcp.tool()
async def delete_expense(expense_id: int) -> dict:
    """Delete one expense by id (use list_expenses to find the id). Use this to fix mistakes.

    Args:
        expense_id: The id of the expense to delete.
    """
    return await call_backend("DELETE", f"/api/expenses/{expense_id}")


@mcp.tool()
async def monthly_summary(month: str = "") -> dict:
    """Total spending for a month plus a breakdown by category, largest first.

    Args:
        month: Month as YYYY-MM. Leave empty for the current month.
    """
    return await call_backend("GET", "/api/summary", params={"month": month or None})


@mcp.tool()
async def set_budget(category: str, monthly_limit: float) -> dict:
    """Set or change the monthly budget for a category. Example: "Budget 5000 for food".

    Args:
        category: Category name, e.g. food.
        monthly_limit: Maximum to spend per month in that category.
    """
    return await call_backend("PUT", "/api/budgets", json={"category": category, "monthly_limit": monthly_limit})


@mcp.tool()
async def budget_status(month: str = "") -> list | dict:
    """For each budgeted category: limit, spent, remaining and whether it is over budget.

    Args:
        month: Month as YYYY-MM. Leave empty for the current month.
    """
    return await call_backend("GET", "/api/budgets/status", params={"month": month or None})


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
