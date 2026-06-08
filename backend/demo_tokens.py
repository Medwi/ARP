"""
Fixed demo bearer tokens — same values as the README tester table.

Seed always applies these credentials so assessors can log in without
reading bootstrap files or container logs.
"""

from __future__ import annotations

DEMO_USER_SPECS: tuple[tuple[str, str], ...] = (
    ("admin@local", "admin"),
    ("analyst@local", "analyst"),
    ("risk@local", "risk"),
    ("manager@local", "manager"),
    ("intern@local", "intern"),
)

DEMO_USER_TOKENS: dict[str, str] = {
    "admin@local": "c8e9c750de3d240b4d2e659a48b00f0c66ca534b4e544229",
    "analyst@local": "3edf214346ede6b6593e6c66f54c008bc5cc275793c6a650",
    "risk@local": "69535fd68dcf0a91c043d5cc9e776cd1ce6a4437c26870b4",
    "manager@local": "e80cde4bb031633a8bb086c33900869ae66b763e4df4d239",
    "intern@local": "969bc0eac4285f06a8c6cd63cdd30b218aaab5447dbd6aac",
}
