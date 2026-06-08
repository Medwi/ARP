#!/usr/bin/env python3
"""
SQLite hot backup via the backup API — works without the sqlite3 CLI.

Used by scripts/backup_db.sh when sqlite3 is not installed, and directly in Docker.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path


def hot_backup(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(str(source))
    try:
        dst = sqlite3.connect(str(target))
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()


def integrity_ok(path: Path) -> bool:
    con = sqlite3.connect(str(path))
    try:
        row = con.execute("PRAGMA integrity_check").fetchone()
        return row is not None and row[0] == "ok"
    finally:
        con.close()


def count_table(path: Path, table: str) -> str:
    con = sqlite3.connect(str(path))
    try:
        return str(con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    except sqlite3.Error:
        return "?"
    finally:
        con.close()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def run_backup(db_path: Path, backup_dir: Path, retention_days: int = 30) -> int:
    if not db_path.is_file():
        print(f"  ❌  Database not found at {db_path}", file=sys.stderr)
        return 1

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    backup_file = backup_dir / f"arp_db_{ts}.db"
    manifest = backup_dir / "backup_manifest.txt"

    print(f"  📦  Creating hot backup → {backup_file}")
    hot_backup(db_path, backup_file)

    if not integrity_ok(backup_file):
        backup_file.unlink(missing_ok=True)
        print("  ❌  Integrity check failed — backup removed", file=sys.stderr)
        return 1

    users = count_table(backup_file, "users")
    trades = count_table(backup_file, "trades")
    audit = count_table(backup_file, "audit_logs")
    size = backup_file.stat().st_size
    digest = sha256_file(backup_file)

    (backup_file.with_suffix(".db.sha256")).write_text(
        f"{digest}  {backup_file.name}\n", encoding="utf-8"
    )
    with manifest.open("a", encoding="utf-8") as mf:
        mf.write(
            f"{ts} | {backup_file} | bytes={size} | "
            f"users={users} trades={trades} audit={audit} | VERIFIED\n"
        )

    print(f"  ✅  Backup verified — users={users} trades={trades} audit={audit}")
    print(f"      SHA-256: {digest[:16]}…")

    cutoff = datetime.now(timezone.utc).timestamp() - retention_days * 86400
    pruned = 0
    for old in backup_dir.glob("arp_db_*.db"):
        if old.stat().st_mtime < cutoff:
            old.unlink(missing_ok=True)
            old.with_suffix(".db.sha256").unlink(missing_ok=True)
            pruned += 1
    if pruned:
        print(f"  🗑️   Pruned {pruned} backup(s) older than {retention_days} days")

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="ARP SQLite hot backup (Python)")
    parser.add_argument("--db", default=os.environ.get("DB_PATH", "/data/arp.db"))
    parser.add_argument("--dir", default="./backups")
    parser.add_argument("--retention-days", type=int, default=30)
    args = parser.parse_args()
    return run_backup(Path(args.db), Path(args.dir), args.retention_days)


if __name__ == "__main__":
    raise SystemExit(main())
