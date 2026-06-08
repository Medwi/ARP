"""
ARP Platform – middleware/rate_limiter.py
Per-token request throttling.

Backends (RATE_LIMIT_BACKEND):
  - memory  — single uvicorn worker only (default)
  - redis   — shared store for multi-worker / multi-replica deployments
"""

from __future__ import annotations

from typing import Optional

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from backend.middleware.rate_limit_store import get_rate_limit_store


class SlidingWindowRateLimiter(BaseHTTPMiddleware):
    """
    Sliding-window rate limiter keyed on bearer token.
    Falls back to IP address if no token is present.
    """

    def __init__(
        self,
        app,
        default_limit:   int  = 120,
        default_window:  int  = 60,
        endpoint_limits: Optional[dict] = None,
    ):
        super().__init__(app)
        self.default_limit   = default_limit
        self.default_window  = default_window
        if endpoint_limits is None:
            from backend.config import get_rate_limit_endpoint_overrides
            endpoint_limits = get_rate_limit_endpoint_overrides()
        self.endpoint_limits = endpoint_limits

    def _get_key(self, request: Request) -> str:
        auth = request.headers.get("authorization", "")
        if auth.startswith("Bearer "):
            return f"token:{auth[7:20]}"
        client_ip = request.client.host if request.client else "unknown"
        return f"ip:{client_ip}"

    async def dispatch(self, request: Request, call_next) -> Response:
        skip = {"/health", "/docs", "/openapi.json",
                "/redoc", "/docs/oauth2-redirect"}
        if request.url.path in skip:
            return await call_next(request)

        store, _ = get_rate_limit_store()
        key = self._get_key(request)

        if request.url.path in self.endpoint_limits:
            limit, window = self.endpoint_limits[request.url.path]
        else:
            limit, window = self.default_limit, self.default_window

        is_limited, retry_after = store.check_and_record(key, limit, window)

        if is_limited:
            return JSONResponse(
                status_code=429,
                content={
                    "detail": (
                        f"Rate limit exceeded: {limit} requests per {window}s. "
                        f"Retry after {retry_after}s."
                    ),
                    "retry_after":    retry_after,
                    "limit":          limit,
                    "window_seconds": window,
                },
                headers={"Retry-After": str(retry_after)},
            )

        return await call_next(request)
