import hmac
import hashlib
import os
from typing import Annotated

from fastapi import Header, HTTPException


def valid_api_token(token: str) -> bool:
    return len(token) >= 32 and all(33 <= ord(character) <= 126 for character in token)


def require_api_key(authorization: Annotated[str | None, Header()] = None) -> str:
    expected = os.getenv("AGENT_API_TOKEN", "")
    if not valid_api_token(expected):
        raise HTTPException(status_code=503, detail="Backend API key is not configured")

    scheme, _, supplied = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not valid_api_token(supplied) or not hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="A valid bearer token is required")
    return hashlib.sha256(expected.encode()).hexdigest()
