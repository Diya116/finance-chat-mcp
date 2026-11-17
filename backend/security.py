"""Small security helpers: password hashing, random tokens, PKCE."""

import base64
import hashlib
import hmac
import secrets
import time


def now() -> int:
    """Current unix time in seconds."""
    return int(time.time())


# ---- passwords (scrypt from the standard library, so no extra dependency) ----

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt_hex, digest_hex = stored.split("$")
        digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex), n=2**14, r=8, p=1)
    except ValueError:
        return False
    return hmac.compare_digest(digest.hex(), digest_hex)


# A throwaway hash so login takes the same time whether or not the email exists.
DUMMY_HASH = hash_password("not-a-real-password")


# ---- tokens ----

def new_token() -> str:
    return secrets.token_urlsafe(32)


def sha256_hex(value: str) -> str:
    """We store only this hash in the DB. If the DB leaks, the tokens are still safe."""
    return hashlib.sha256(value.encode()).hexdigest()


# ---- PKCE (S256) ----

def pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
