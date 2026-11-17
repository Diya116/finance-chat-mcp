"""
End-to-end test that pretends to be Claude. Start the backend (8001) and MCP server (8000) first, then:

    python test_oauth_flow.py
"""

import asyncio
import base64
import hashlib
import re
import secrets
import sys
from urllib.parse import parse_qs, urlsplit

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

BACKEND = "http://127.0.0.1:8001"
MCP = "http://127.0.0.1:8000/mcp"
REDIRECT = "http://127.0.0.1:9999/callback"


def check(condition: bool, label: str):
    print(("PASS  " if condition else "FAIL  ") + label)
    if not condition:
        sys.exit(1)


def csrf_from(html: str) -> str:
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


def oauth_login(email: str, client_id: str) -> dict:
    """Signup + authorize + token exchange for one user. Returns the token response."""
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    params = {"response_type": "code", "client_id": client_id, "redirect_uri": REDIRECT,
              "code_challenge": challenge, "code_challenge_method": "S256", "state": "xyz"}

    browser = httpx.Client(base_url=BACKEND, follow_redirects=False)  # keeps cookies like a browser

    # not logged in -> redirected to /login
    r = browser.get("/oauth/authorize", params=params)
    check(r.status_code == 303 and r.headers["location"].startswith("/login"), "authorize redirects to login")

    # sign up (also logs in)
    r = browser.get("/signup")
    r = browser.post("/signup", data={"email": email, "password": "correct-horse", "csrf": csrf_from(r.text)})
    check(r.status_code == 303, f"signup {email}")

    # consent page
    r = browser.get("/oauth/authorize", params=params)
    check(r.status_code == 200 and "Allow access" in r.text, "consent page shown")
    form = dict(re.findall(r'name="(\w+)" value="([^"]*)"', r.text))
    form["decision"] = "approve"
    r = browser.post("/oauth/authorize", data=form)
    check(r.status_code == 303, "approve redirects back to the client")
    query = parse_qs(urlsplit(r.headers["location"]).query)
    check(query["state"] == ["xyz"] and "code" in query, "redirect has code + state")
    code = query["code"][0]

    # wrong PKCE verifier must fail
    bad = httpx.post(f"{BACKEND}/oauth/token", data={"grant_type": "authorization_code", "client_id": client_id,
                     "code": code, "redirect_uri": REDIRECT, "code_verifier": "x" * 50})
    check(bad.status_code == 400, "wrong PKCE verifier rejected")
    # ...and that attempt burned the code, so redo the approve step for the happy path
    r = browser.post("/oauth/authorize", data=form)
    code = parse_qs(urlsplit(r.headers["location"]).query)["code"][0]

    token_data = {"grant_type": "authorization_code", "client_id": client_id, "code": code,
                  "redirect_uri": REDIRECT, "code_verifier": verifier}
    r = httpx.post(f"{BACKEND}/oauth/token", data=token_data)
    check(r.status_code == 200 and "access_token" in r.json(), "code exchanged for tokens")
    reuse = httpx.post(f"{BACKEND}/oauth/token", data=token_data)
    check(reuse.status_code == 400, "authorization code cannot be reused")
    return r.json()


async def use_mcp(access_token: str, calls: list[tuple[str, dict]]) -> list[str]:
    headers = {"Authorization": f"Bearer {access_token}"}
    async with streamablehttp_client(MCP, headers=headers) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            return [(await session.call_tool(name, args)).content[0].text for name, args in calls]


def main():
    # discovery
    meta = httpx.get(f"{BACKEND}/.well-known/oauth-authorization-server").json()
    check(meta["code_challenge_methods_supported"] == ["S256"], "authorization server metadata")
    r = httpx.get("http://127.0.0.1:8000/.well-known/oauth-protected-resource/mcp")
    check(r.status_code == 200 and BACKEND in str(r.json()["authorization_servers"]),
          "MCP protected-resource metadata points to backend")

    # MCP rejects calls without a token
    r = httpx.post(MCP, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                   headers={"Accept": "application/json, text/event-stream"})
    check(r.status_code == 401, "MCP returns 401 without token")

    # dynamic client registration
    r = httpx.post(f"{BACKEND}/oauth/register", json={"client_name": "Test Claude", "redirect_uris": [REDIRECT]})
    check(r.status_code == 201, "client registered")
    client_id = r.json()["client_id"]
    check(httpx.post(f"{BACKEND}/oauth/register", json={"redirect_uris": ["http://evil.com/cb"]}).status_code == 400,
          "non-local http redirect_uri rejected")

    # two different users
    alice = oauth_login(f"alice{secrets.token_hex(3)}@example.com", client_id)
    bob = oauth_login(f"bob{secrets.token_hex(3)}@example.com", client_id)

    out = asyncio.run(use_mcp(alice["access_token"], [
        ("add_expense", {"amount": 450, "category": "Groceries", "note": "veggies"}),
        ("add_expense", {"amount": 120.5, "category": "food"}),
        ("set_budget", {"category": "groceries", "monthly_limit": 400}),
        ("monthly_summary", {}),
        ("budget_status", {}),
    ]))
    print("   alice summary:", out[3].replace("\n", "").replace("  ", ""))
    print("   alice budgets:", out[4].replace("\n", "").replace("  ", ""))
    check('"over_budget": true' in out[4], "budget over-limit detected")

    bob_summary = asyncio.run(use_mcp(bob["access_token"], [("monthly_summary", {})]))[0]
    check('"total": 0.0' in bob_summary, "bob cannot see alice's expenses")

    # bad token
    try:
        asyncio.run(use_mcp("not-a-token", [("monthly_summary", {})]))
        check(False, "bad token rejected")
    except BaseException:
        check(True, "bad token rejected by MCP server")

    # refresh rotation
    r = httpx.post(f"{BACKEND}/oauth/token", data={"grant_type": "refresh_token", "client_id": client_id,
                                                  "refresh_token": alice["refresh_token"]})
    check(r.status_code == 200 and r.json()["access_token"] != alice["access_token"], "refresh token works")
    again = httpx.post(f"{BACKEND}/oauth/token", data={"grant_type": "refresh_token", "client_id": client_id,
                                                      "refresh_token": alice["refresh_token"]})
    check(again.status_code == 400, "old refresh token cannot be reused (rotation)")
    print("\nALL CHECKS PASSED")


main()
