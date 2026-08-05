"""Local same-origin and CSRF protections for state-changing endpoints."""

from __future__ import annotations

import hmac
import secrets

from fastapi import HTTPException, Request, status

CSRF_COOKIE = "astro_wolkencheck_csrf"
CSRF_HEADER = "X-CSRF-Token"


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def require_csrf(request: Request) -> None:
    cookie = request.cookies.get(CSRF_COOKIE)
    header = request.headers.get(CSRF_HEADER)
    if not cookie or not header or not hmac.compare_digest(cookie, header):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="CSRF token invalid")

    origin = request.headers.get("origin")
    if origin and origin.rstrip("/") != str(request.base_url).rstrip("/"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Origin not allowed")
