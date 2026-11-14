import hmac
import hashlib
import os
from typing import Annotated

from fastapi import Header, HTTPException


def require_api_key(authorization: Annotated[str | None, Header()] = None) -> str:
    expected = os.getenv("AGENT_API_TOKEN", "")
    if len(expected) < 32:
        raise HTTPException(status_code=503, detail="Backend API key is not configured")

    scheme, _, supplied = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="A valid bearer token is required")
    return hashlib.sha256(expected.encode()).hexdigest()
