import hmac
import hashlib
import os
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Request, Response
from pydantic import BaseModel, Field

from app.accounts import SESSION_SECONDS, LoginRateLimited, get_accounts

router = APIRouter(prefix="/api/auth")
SESSION_COOKIE = "guuey_session"


def require_cookie_request(request: Request) -> None:
    # Cross-origin sites cannot set this header without an approved CORS preflight.
    if request.headers.get("x-guuey-request") != "1":
        raise HTTPException(403, "A same-origin app request is required")


def valid_api_token(token: str) -> bool:
    return len(token) >= 32 and all(33 <= ord(character) <= 126 for character in token)


def require_api_key(authorization: Annotated[str | None, Header()] = None, request: Request = None) -> str:
    if request is not None and authorization is None:
        token = request.cookies.get(SESSION_COOKIE)
        if token:
            owner = get_accounts().session_owner(token)
            if owner is None:
                raise HTTPException(401, "Your session expired. Sign in again.")
            expected_owner = request.headers.get("x-guuey-user")
            if expected_owner is not None and expected_owner != owner:
                raise HTTPException(401, "The signed-in account changed. Sign in again.")
            if request.method not in {"GET", "HEAD", "OPTIONS"}:
                require_cookie_request(request)
            return owner
    expected = os.getenv("AGENT_API_TOKEN", "")
    if not valid_api_token(expected):
        if get_accounts().has_users():
            raise HTTPException(401, "Sign in to continue")
        raise HTTPException(status_code=503, detail="Backend API key is not configured")

    scheme, _, supplied = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not valid_api_token(supplied) or not hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="A valid bearer token is required")
    return hashlib.sha256(expected.encode()).hexdigest()


class PasswordLogin(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=1024)


class MagicLogin(BaseModel):
    token: str = Field(pattern=r"^[A-Za-z0-9_-]{43}$")


def login_response(request: Request, response: Response, **credentials):
    require_cookie_request(request)
    store = get_accounts()
    try:
        store.limit_login(request.client.host if request.client else "unknown", credentials.get("username"))
    except LoginRateLimited:
        raise HTTPException(429, "Too many sign-in attempts. Try again in five minutes.",
                            headers={"Retry-After": "300"}) from None
    result = store.login(**credentials, previous_session=request.cookies.get(SESSION_COOKIE))
    if result is None:
        raise HTTPException(401, "Invalid sign-in details")
    user, token = result
    response.set_cookie(SESSION_COOKIE, token, max_age=SESSION_SECONDS, httponly=True, samesite="strict",
                        secure=request.url.scheme == "https" or os.getenv("AUTH_COOKIE_SECURE", "").lower() == "true",
                        path="/api")
    response.headers["Cache-Control"] = "no-store"
    return {"authenticated": True, "user": user}


@router.post("/login")
def password_login(body: PasswordLogin, request: Request, response: Response):
    return login_response(request, response, username=body.username, password=body.password)


@router.post("/magic-link")
def magic_login(body: MagicLogin, request: Request, response: Response):
    return login_response(request, response, magic_token=body.token)


@router.post("/logout", status_code=204)
def logout(request: Request):
    require_cookie_request(request)
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        get_accounts().logout(token)
    response = Response(status_code=204, headers={"Cache-Control": "no-store"})
    response.delete_cookie(SESSION_COOKIE, path="/api")
    return response
