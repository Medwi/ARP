"""
ARP Platform – rbac.py
Role-based access control: permission matrix, token validation, enforcement.
Every data access passes through check_permission() before touching the DB.
"""

from __future__ import annotations
import hashlib
import re
import sqlite3
from dataclasses import dataclass
from typing import Optional

from backend.config import allow_legacy_plain_tokens

# ── Permission matrix ─────────────────────────────────────────────────────────
#
#  resource          analyst   risk    manager   intern   admin
#  ───────────────────────────────────────────────────────────
#  portfolio         ✓         ✓       ✗         ✗        ✓
#  market_prices     ✓         ✓       ✗         ✗        ✓
#  trades            ✗         ✓       ✗         ✗        ✓
#  risk_alerts       ✗         ✓       ✗         ✗        ✓
#  summary           ✓         ✓       ✓*        ✗        ✓
#  (* manager summary: fund KPIs, asset-class mix, top-3 concentration weights only)
#  audit_logs        ✗         ✗*      ✗         ✗        ✓
#  (* audit UI gated to risk in API; admin has full audit_logs resource)

ALL_RESOURCES: frozenset[str] = frozenset({
    "portfolio", "market_prices", "trades", "risk_alerts", "summary", "audit_logs",
})

PERMISSIONS: dict[str, set[str]] = {
    "analyst": {"portfolio", "market_prices", "summary"},
    "risk":    {"portfolio", "market_prices", "trades", "risk_alerts", "summary"},
    "manager": {"summary"},
    "intern":  set(),
    "admin":   set(ALL_RESOURCES),
}

ROLE_DESCRIPTIONS: dict[str, str] = {
    "analyst": "Can view portfolio holdings and market data.",
    "risk":    "Can view portfolio, trades, and risk alerts.",
    "manager": (
        "COO / executive summary: AUM, P&L, asset-class mix, top-3 weights, risk alert "
        "counts only — no book, blotter, audit, or AI. Intelligence workspace (briefing, "
        "reporting, compliance) requires risk or admin."
    ),
    "intern":  "No data access — onboarding/training role; all data endpoints deny.",
    "admin":   "Full platform access: all data, agents, audit logs, and metrics.",
}

PRIVILEGED_ROLES: frozenset[str] = frozenset({"risk", "admin"})

_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class User:
    email: str
    role:  str

@dataclass
class PermissionResult:
    allowed:     bool
    user:        Optional[User]
    resource:    str
    deny_reason: Optional[str] = None

# ── Core functions ────────────────────────────────────────────────────────────

def hash_token(plain: str) -> str:
    """SHA-256 digest of a bearer token for DB storage."""
    return hashlib.sha256(plain.strip().encode()).hexdigest()


def looks_like_stored_digest(value: str) -> bool:
    """True when value is a 64-char lowercase hex SHA-256 digest."""
    return bool(value and _SHA256_HEX.match(value.lower()))


def resolve_token(db_path: str, token: str) -> Optional[User]:
    """
    Look up a bearer token and return the associated User, or None.
    Production stores SHA-256 digests only; legacy plain tokens require
    ARP_ALLOW_LEGACY_PLAIN_TOKENS=1.
    """
    token = token.strip()
    if not token:
        return None
    hashed = hash_token(token)
    try:
        from backend.db import connect
        con = connect(db_path)
        row = con.execute(
            "SELECT email, role FROM users WHERE token = ?", (hashed,)
        ).fetchone()
        if not row and allow_legacy_plain_tokens():
            row = con.execute(
                "SELECT email, role FROM users WHERE token = ?", (token,)
            ).fetchone()
        con.close()
        if row:
            return User(email=row[0], role=row[1])
    except sqlite3.Error:
        pass
    return None


def is_admin(user: User) -> bool:
    return user.role == "admin"


def can_view_audit(user: User) -> bool:
    """Audit log API and verification endpoints."""
    return user.role in PRIVILEGED_ROLES


def can_view_metrics(user: User) -> bool:
    return user.role in ("analyst", "risk", "admin")


def check_permission(user: User, resource: str) -> PermissionResult:
    """
    Determine whether a user's role grants access to a resource.
    Deny messages are policy-specific: they name exactly which roles would
    grant access, and confirm the attempt is written to the audit trail.
    This is the single enforcement point — call it before every DB query.
    """
    if is_admin(user):
        return PermissionResult(allowed=True, user=user, resource=resource)

    allowed_resources = PERMISSIONS.get(user.role, set())
    if resource in allowed_resources:
        return PermissionResult(allowed=True, user=user, resource=resource)

    # Which roles do have access to this resource?
    granting_roles = sorted(
        role for role, perms in PERMISSIONS.items() if resource in perms
    )

    if user.role not in PERMISSIONS:
        reason = (
            f"[RBAC POLICY VIOLATION] Unknown role '{user.role}'. "
            f"This session has been flagged. Contact your system administrator."
        )
    elif not allowed_resources:
        reason = (
            f"[RBAC POLICY VIOLATION] Role '{user.role}' has no data access permissions "
            f"in this environment. This attempt has been written to the immutable audit trail. "
            f"Resource '{resource}' requires role: "
            f"{', '.join(granting_roles) or 'system-only'}."
        )
    else:
        permitted_str = ", ".join(sorted(allowed_resources))
        granting_str  = ", ".join(granting_roles) if granting_roles else "no standard role"
        reason = (
            f"[RBAC POLICY VIOLATION] Role '{user.role}' is not authorised to access "
            f"'{resource}' data. "
            f"Your permitted resources: {permitted_str}. "
            f"Access to '{resource}' requires role: {granting_str}. "
            f"This attempt has been written to the immutable audit trail."
        )
    return PermissionResult(allowed=False, user=user, resource=resource, deny_reason=reason)

def get_user_permissions(role: str) -> list[str]:
    """Return list of resource names accessible to a given role."""
    if role == "admin":
        return sorted(ALL_RESOURCES)
    return sorted(PERMISSIONS.get(role, set()))

def role_summary(role: str) -> str:
    return ROLE_DESCRIPTIONS.get(role, "Unknown role.")


# Back-compat alias for internal callers
_hash_token = hash_token
