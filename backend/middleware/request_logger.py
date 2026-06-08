"""
ARP Platform – middleware/request_logger.py
Structured JSON request/response logger.

Logs every HTTP request as a structured JSON line to stdout.
Designed for production log aggregation (CloudWatch, Datadog, ELK).

Fields per log line:
  timestamp, method, path, status_code, duration_ms,
  user_email (from Bearer token), client_ip, user_agent,
  request_id (UUID per request for correlation)

Why this matters for ARP:
  - DFSA operational risk requirements mandate system activity logging
  - Structured JSON enables automated alerting on error spikes
  - Per-request duration metrics feed latency SLA monitoring
  - Correlation IDs tie frontend actions to backend traces
  - Required for production SIEM integration (Splunk, Sentinel)

Excluded from logging (privacy + noise):
  - /health, /docs, /openapi.json, /redoc (infrastructure endpoints)
  - Authorization header values (tokens never logged)
  - Request/response body (PII risk — content stays in audit_logs)
"""

from __future__ import annotations
import json, time, uuid, os
from datetime import datetime, timezone

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from backend.config import get_db_path

# Paths excluded from request logging (too noisy / no business value)
SKIP_PATHS = frozenset({
    "/health", "/docs", "/openapi.json",
    "/redoc", "/docs/oauth2-redirect",
})


def _extract_user(request: Request) -> str:
    """Extract user email from Bearer token without hitting the DB."""
    auth = request.headers.get("authorization", "")
    if not auth.startswith("Bearer "):
        return "unauthenticated"
    # Resolve token → email via DB lookup (lightweight)
    try:
        import sqlite3
        token = auth[7:].strip()
        import hashlib
        hashed = hashlib.sha256(token.encode()).hexdigest()
        con = sqlite3.connect(get_db_path())
        row = con.execute(
            "SELECT email FROM users WHERE token IN (?, ?)", (hashed, token)
        ).fetchone()
        con.close()
        return row[0] if row else "unknown_token"
    except Exception:
        return "unknown_token"


class RequestLogger(BaseHTTPMiddleware):
    """
    Structured JSON request logger. Writes one log line per request to stdout.
    Silent on failure — logging must never break the application.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        if request.url.path in SKIP_PATHS:
            return await call_next(request)

        request_id  = str(uuid.uuid4())[:8]
        started_at  = time.perf_counter()
        ts          = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        client_ip   = request.client.host if request.client else "unknown"
        user_agent  = request.headers.get("user-agent", "")[:120]
        user_email  = _extract_user(request)

        try:
            response = await call_next(request)
            status_code = response.status_code
        except Exception as exc:
            status_code = 500
            self._write_log({
                "ts":           ts,
                "request_id":   request_id,
                "method":       request.method,
                "path":         str(request.url.path),
                "status":       status_code,
                "duration_ms":  round((time.perf_counter() - started_at) * 1000),
                "user":         user_email,
                "ip":           client_ip,
                "ua":           user_agent,
                "error":        str(exc),
                "level":        "ERROR",
            })
            raise

        duration_ms = round((time.perf_counter() - started_at) * 1000)
        level = (
            "ERROR"   if status_code >= 500 else
            "WARNING" if status_code >= 400 else
            "INFO"
        )

        self._write_log({
            "ts":          ts,
            "request_id":  request_id,
            "method":      request.method,
            "path":        str(request.url.path),
            "status":      status_code,
            "duration_ms": duration_ms,
            "user":        user_email,
            "ip":          client_ip,
            "ua":          user_agent,
            "level":       level,
        })

        # Inject correlation ID into response headers
        response.headers["X-Request-ID"] = request_id
        return response

    @staticmethod
    def _write_log(data: dict) -> None:
        """Write a single JSON log line to stdout. Never raises."""
        try:
            print(json.dumps(data), flush=True)
        except Exception:
            pass


class SlowRequestDetector(BaseHTTPMiddleware):
    """
    Separate middleware that flags slow requests (>5s) with a WARNING log.
    Helps identify Ollama cold-start latency and DB query bottlenecks.
    """

    SLOW_THRESHOLD_MS = int(os.getenv("SLOW_REQUEST_THRESHOLD_MS", "5000"))

    async def dispatch(self, request: Request, call_next) -> Response:
        if request.url.path in SKIP_PATHS:
            return await call_next(request)

        start    = time.perf_counter()
        response = await call_next(request)
        elapsed  = round((time.perf_counter() - start) * 1000)

        if elapsed > self.SLOW_THRESHOLD_MS:
            try:
                print(json.dumps({
                    "ts":          datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
                    "level":       "SLOW_REQUEST",
                    "path":        str(request.url.path),
                    "duration_ms": elapsed,
                    "threshold_ms": self.SLOW_THRESHOLD_MS,
                    "action":      "Check Ollama model warm-up status.",
                }), flush=True)
            except Exception:
                pass

        return response
