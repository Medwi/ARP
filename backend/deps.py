"""FastAPI dependencies shared across route modules."""

from __future__ import annotations

from typing import Optional

from fastapi import Header, HTTPException

from backend.config import get_db_path
from backend.rbac import User, resolve_token


def _user_from_authorization(authorization: Optional[str]) -> Optional[User]:
    if not authorization or not authorization.startswith("Bearer "):
        return None
    return resolve_token(get_db_path(), authorization[7:])


def get_current_user(authorization: str = Header(...)) -> User:
    """Extract Bearer token and resolve to a User. Raises 401 if invalid."""
    user = _user_from_authorization(authorization)
    if not user:
        if not authorization.startswith("Bearer "):
            raise HTTPException(
                status_code=401,
                detail="Authorization header must be 'Bearer <token>'",
            )
        raise HTTPException(status_code=401, detail="Invalid or expired token.")
    return user


def get_optional_user(authorization: Optional[str] = Header(None)) -> Optional[User]:
    """Return User when Bearer token is valid; None when absent or invalid."""
    return _user_from_authorization(authorization)


def enforce_tool_result(result: dict) -> dict:
    """
    REST routes: RBAC denial → HTTP 403 (not 200 with allowed:false).
    Agent tools keep dict responses for orchestration and audit.
    """
    if not result.get("allowed", True):
        raise HTTPException(
            status_code=403,
            detail=result.get("error") or "Access denied",
        )
    return result
