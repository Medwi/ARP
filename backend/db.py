"""
ARP Platform – db.py
Shared SQLite connection helper.

Every module that reads portfolio, trade, audit, or memory data should use
connect() so row_factory and DB_PATH resolution stay consistent.
"""

from __future__ import annotations

import sqlite3
from typing import Optional

from backend.config import get_db_path


def connect(db_path: Optional[str] = None) -> sqlite3.Connection:
    """Open a SQLite connection with Row factory. Uses get_db_path() when db_path is omitted."""
    con = sqlite3.connect(db_path or get_db_path())
    con.row_factory = sqlite3.Row
    return con
