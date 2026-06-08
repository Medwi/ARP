#!/usr/bin/env python3
"""
End-to-end fundamentals smoke check (no Docker required).

  python scripts/smoke.py
  DB_PATH=/tmp/arp-smoke.db python scripts/smoke.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from fastapi.testclient import TestClient  # noqa: E402

import backend.main as main  # noqa: E402
import seed.seed as seed_mod  # noqa: E402
from backend.config import get_bootstrap_tokens_path  # noqa: E402


def _load_bootstrap_tokens(db_path: str) -> dict[str, str]:
    """Read plain tokens from the gitignored bootstrap file written at seed."""
    path = get_bootstrap_tokens_path()
    if str(path.parent) != str(Path(db_path).parent):
        path = Path(db_path).parent / ".bootstrap_tokens"

    tokens: dict[str, str] = {}
    if not path.is_file():
        return tokens

    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) >= 3:
            tokens[parts[0]] = parts[2]
    return tokens


def run() -> int:
    db = os.environ.get("DB_PATH") or os.path.join(
        tempfile.gettempdir(), "arp-smoke.db"
    )
    if os.path.exists(db):
        os.remove(db)

    os.environ["DB_PATH"] = db
    seed_mod.DB_PATH = db

    print(f"Seeding {db} …")
    seed_mod.main()

    tokens = _load_bootstrap_tokens(db)
    risk_token = tokens.get("risk@local")
    intern_token = tokens.get("intern@local")
    if not risk_token or not intern_token:
        print("  FAIL — bootstrap tokens missing; re-run seed", file=sys.stderr)
        return 1

    client = TestClient(main.app)
    checks = []

    def check(name: str, ok: bool, detail: str = ""):
        status = "OK" if ok else "FAIL"
        print(f"  [{status}] {name}" + (f" — {detail}" if detail else ""))
        checks.append(ok)

    r = client.get("/health")
    check("GET /health", r.status_code == 200 and r.json().get("status") == "ok")

    h = {"Authorization": f"Bearer {risk_token}"}
    r = client.get("/portfolio/summary", headers=h)
    check(
        "GET /portfolio/summary (risk)",
        r.status_code == 200 and r.json().get("allowed"),
        f"AUM={r.json().get('total_aum')}" if r.status_code == 200 else r.text,
    )

    r = client.get("/trades/recent", headers=h)
    check(
        "GET /trades/recent (risk)",
        r.status_code == 200 and r.json().get("allowed"),
    )

    r = client.post(
        "/ask",
        headers=h,
        json={"question": "Show recent trades", "agent": "risk"},
    )
    check(
        "POST /ask (risk agent)",
        r.status_code == 200 and r.json().get("allowed"),
        r.json().get("tool_called", ""),
    )

    r = client.get("/health", headers=h)
    body = r.json() if r.status_code == 200 else {}
    check(
        "GET /health (authenticated detail)",
        r.status_code == 200 and body.get("rate_limit", {}).get("enabled") is True,
        body.get("rate_limit", {}).get("backend", ""),
    )

    h_intern = {"Authorization": f"Bearer {intern_token}"}
    r = client.get("/portfolio/summary", headers=h_intern)
    check(
        "RBAC deny intern portfolio (HTTP 403)",
        r.status_code == 403,
    )

    r = client.get("/audit/logs", headers=h_intern)
    check("Audit 403 for non-risk", r.status_code == 403)

    passed = sum(checks)
    total = len(checks)
    print(f"\n{passed}/{total} checks passed.")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(run())
