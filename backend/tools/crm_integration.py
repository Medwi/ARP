"""
ARP Platform – tools/crm_integration.py
Salesforce + Administrator Integration Stub.

Maps to ARP's SOON (0–3 month) roadmap priority:
  "Salesforce + Administrator Integration: enriching CRM data with
   administrator records, automating follow-ups, and preparing KYC/ODD packs."

Architecture:
  In production this connects to Salesforce via the REST API using OAuth2.
  This stub implements the full data model and business logic locally,
  with a pluggable connector interface. Swapping the stub for the real
  Salesforce connector requires only changing _fetch_contacts() and
  _push_update() — all business logic stays identical.

Key capabilities:
  - Investor contact management (name, AUM, jurisdiction, contact cadence)
  - Follow-up automation (overdue contacts, next action queue)
  - KYC/ODD pack status tracking per investor
  - Subscription and redemption pipeline
  - Auto-generate investor update drafts (fed to LLM for personalisation)
"""

from __future__ import annotations
import sqlite3, os
from datetime import datetime, timezone, timedelta
from typing import Optional

from backend.rbac import User, check_permission
from backend.audit import log
from backend.db import connect

# Connection mode — swap to "salesforce" in production
CRM_MODE = os.getenv("CRM_MODE", "local")

INVESTOR_TIERS = {"INSTITUTIONAL", "HNW", "FAMILY_OFFICE", "SOVEREIGN"}
KYC_STATUSES   = {"PENDING", "IN_PROGRESS", "COMPLETE", "EXPIRED", "FLAGGED"}


def ensure_crm_tables(db_path: Optional[str] = None) -> None:
    """Create CRM tables if not present. Safe to call multiple times."""
    con = connect(db_path)
    con.executescript("""
        CREATE TABLE IF NOT EXISTS crm_contacts (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            name            TEXT    NOT NULL,
            entity          TEXT    NOT NULL,
            tier            TEXT    NOT NULL DEFAULT 'INSTITUTIONAL',
            email           TEXT,
            jurisdiction    TEXT,
            aum_committed   REAL,
            aum_currency    TEXT    DEFAULT 'USD',
            relationship_mgr TEXT,
            last_contact    TEXT,
            next_action     TEXT,
            next_action_due TEXT,
            kyc_status      TEXT    DEFAULT 'PENDING',
            kyc_expiry      TEXT,
            notes           TEXT,
            active          INTEGER DEFAULT 1,
            created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS crm_interactions (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            contact_id  INTEGER REFERENCES crm_contacts(id),
            type        TEXT    NOT NULL,
            summary     TEXT    NOT NULL,
            outcome     TEXT,
            logged_by   TEXT    NOT NULL,
            occurred_at TEXT    NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS crm_subscriptions (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            contact_id      INTEGER REFERENCES crm_contacts(id),
            type            TEXT    NOT NULL CHECK(type IN ('SUBSCRIPTION','REDEMPTION')),
            amount          REAL    NOT NULL,
            currency        TEXT    DEFAULT 'USD',
            status          TEXT    NOT NULL DEFAULT 'PENDING',
            requested_at    TEXT    NOT NULL DEFAULT (datetime('now')),
            settled_at      TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_crm_contact_active ON crm_contacts(active);
        CREATE INDEX IF NOT EXISTS idx_crm_kyc_status ON crm_contacts(kyc_status);
        CREATE INDEX IF NOT EXISTS idx_crm_next_due ON crm_contacts(next_action_due);
    """)
    con.commit()
    con.close()


# ── Tool: get_investor_pipeline ───────────────────────────────────────────────

def get_investor_pipeline(
    user:    User,
    db_path: Optional[str] = None,
) -> dict:
    """
    Returns the full investor pipeline: contacts, overdue follow-ups,
    KYC status, pending subscriptions/redemptions.
    Required role: risk (trades access covers investor data).
    """
    perm = check_permission(user, "trades")
    log(user.email, user.role, "get_investor_pipeline", perm.allowed, db_path=db_path)
    if not perm.allowed:
        return {"allowed": False, "error": perm.deny_reason}

    ensure_crm_tables(db_path)
    con = connect(db_path)
    try:
        contacts = [dict(r) for r in con.execute(
            "SELECT * FROM crm_contacts WHERE active=1 ORDER BY next_action_due ASC"
        ).fetchall()]

        if not contacts:
            return {
                "allowed": True,
                "contacts": [],
                "overdue_count": 0,
                "kyc_alerts": [],
                "message": "CRM is empty. Use /crm/seed-samples or connect Salesforce.",
                "crm_mode": CRM_MODE,
            }

        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        # Overdue follow-ups
        overdue = [
            c for c in contacts
            if c.get("next_action_due") and c["next_action_due"] < now_str
        ]

        # KYC alerts (expired or expiring within 30 days)
        expiry_cutoff = (datetime.now(timezone.utc) + timedelta(days=30)).strftime("%Y-%m-%d")
        kyc_alerts = [
            {
                "contact":    c["name"],
                "entity":     c["entity"],
                "kyc_status": c["kyc_status"],
                "kyc_expiry": c.get("kyc_expiry"),
                "urgency":    "EXPIRED" if c.get("kyc_expiry", "9999") < now_str else "EXPIRING",
            }
            for c in contacts
            if c["kyc_status"] in ("EXPIRED", "FLAGGED") or
               (c.get("kyc_expiry") and c["kyc_expiry"] < expiry_cutoff)
        ]

        # Pending flows
        pending_flows = [dict(r) for r in con.execute(
            """SELECT s.*, c.name as contact_name, c.entity
               FROM crm_subscriptions s
               JOIN crm_contacts c ON s.contact_id = c.id
               WHERE s.status = 'PENDING'
               ORDER BY s.requested_at DESC"""
        ).fetchall()]

        # Total AUM committed
        total_aum = sum(c.get("aum_committed") or 0 for c in contacts)

        return {
            "allowed":        True,
            "contacts":       contacts,
            "contact_count":  len(contacts),
            "total_aum_committed": total_aum,
            "overdue_followups": overdue,
            "overdue_count":  len(overdue),
            "kyc_alerts":     kyc_alerts,
            "pending_flows":  pending_flows,
            "crm_mode":       CRM_MODE,
        }
    finally:
        con.close()


# ── Tool: log_investor_interaction ────────────────────────────────────────────

def log_investor_interaction(
    user:       User,
    contact_id: int,
    type_:      str,         # CALL | EMAIL | MEETING | REPORT_SENT
    summary:    str,
    outcome:    Optional[str] = None,
    next_action: Optional[str] = None,
    next_action_due: Optional[str] = None,
    db_path:    Optional[str] = None,
) -> dict:
    """
    Log an investor interaction and update next action.
    Required role: risk.
    """
    perm = check_permission(user, "trades")
    log(user.email, user.role, "log_investor_interaction", perm.allowed,
        f"contact_id={contact_id} type={type_}", perm.deny_reason, db_path)
    if not perm.allowed:
        return {"allowed": False, "error": perm.deny_reason}

    ensure_crm_tables(db_path)
    con = connect(db_path)
    try:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        con.execute(
            """INSERT INTO crm_interactions
               (contact_id, type, summary, outcome, logged_by, occurred_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (contact_id, type_.upper(), summary, outcome, user.email, now),
        )
        # Update last_contact and optionally next action
        update_fields = "last_contact = ?"
        params: list = [now[:10]]
        if next_action:
            update_fields += ", next_action = ?"
            params.append(next_action)
        if next_action_due:
            update_fields += ", next_action_due = ?"
            params.append(next_action_due)
        params.append(contact_id)
        con.execute(f"UPDATE crm_contacts SET {update_fields} WHERE id = ?", params)
        con.commit()
        return {
            "allowed": True,
            "logged":  True,
            "contact_id": contact_id,
            "type":    type_.upper(),
            "message": f"Interaction logged. Next action: {next_action or 'not set'}",
        }
    finally:
        con.close()


# ── Tool: generate_investor_update_draft ─────────────────────────────────────

def generate_investor_update_draft(
    user:       User,
    contact_id: int,
    period:     str = "monthly",
    db_path:    Optional[str] = None,
) -> dict:
    """
    Auto-generate a personalised investor update draft.
    Pulls contact details + portfolio performance for LLM personalisation.
    Required role: risk.
    """
    perm = check_permission(user, "trades")
    log(user.email, user.role, "generate_investor_update_draft", perm.allowed,
        f"contact_id={contact_id}", perm.deny_reason, db_path)
    if not perm.allowed:
        return {"allowed": False, "error": perm.deny_reason}

    ensure_crm_tables(db_path)
    con = connect(db_path)
    try:
        contact = con.execute(
            "SELECT * FROM crm_contacts WHERE id = ?", (contact_id,)
        ).fetchone()
        if not contact:
            return {"allowed": True, "error": f"Contact ID {contact_id} not found"}

        contact = dict(contact)

        # Get portfolio summary for context
        portfolio_ctx = {}
        try:
            holdings = con.execute(
                "SELECT ticker, asset_class, weight_pct, "
                "ROUND((current_price-avg_cost)/avg_cost*100,2) as pnl_pct "
                "FROM portfolio_holdings ORDER BY weight_pct DESC LIMIT 5"
            ).fetchall()
            total_pnl = con.execute(
                "SELECT ROUND(SUM((current_price-avg_cost)*quantity),0) FROM portfolio_holdings"
            ).fetchone()[0]
            portfolio_ctx = {
                "top_holdings": [dict(h) for h in holdings],
                "total_pnl": total_pnl,
            }
        except Exception:
            pass

        period_label = {"monthly": "Monthly", "quarterly": "Quarterly"}.get(period, "Periodic")
        date_str     = datetime.now(timezone.utc).strftime("%B %Y")

        draft = f"""Dear {contact['name']},

I hope this message finds you well.

Please find below your {period_label} update from ARP Global Capital for {date_str}.

PORTFOLIO PERFORMANCE
[AI: Insert period P&L, attribution, and vs-benchmark commentary here]
Total P&L (portfolio): ${portfolio_ctx.get('total_pnl', 0):,.0f}

TOP POSITIONS
{chr(10).join(f"  {h['ticker']}: {h['weight_pct']:.1f}% weight, {h['pnl_pct']:+.2f}% P&L" for h in portfolio_ctx.get('top_holdings', []))}

MARKET COMMENTARY
[AI: Insert macro outlook and key themes for the period here]

LOOKING AHEAD
[AI: Insert forward-looking commentary and any portfolio changes planned]

Please do not hesitate to reach out if you have any questions. We look forward
to speaking with you at our next scheduled review.

Warm regards,

[Portfolio Manager]
ARP Global Capital
DIFC, Dubai

---
Note: This draft was auto-generated. Requires compliance review before sending.
Approval required: CIO + Compliance per ARP sign-off matrix."""

        return {
            "allowed":    True,
            "contact":    contact["name"],
            "entity":     contact["entity"],
            "period":     period_label,
            "draft":      draft,
            "compliance_note": (
                "All investor communications require CIO + Compliance approval "
                "per DFSA GEN Rule 5.3 and ARP sign-off matrix before sending."
            ),
        }
    finally:
        con.close()


def seed_sample_crm(db_path: Optional[str] = None) -> int:
    """Populate CRM with realistic sample investors for demo purposes."""
    ensure_crm_tables(db_path)
    con = connect(db_path)
    if con.execute("SELECT COUNT(*) FROM crm_contacts").fetchone()[0] > 0:
        con.close()
        return 0

    now = datetime.now(timezone.utc)
    contacts = [
        ("Sheikh Al-Rashidi Family Office", "Al-Rashidi Holdings DIFC",
         "FAMILY_OFFICE", "contact@alrashidi.ae", "UAE",
         25_000_000, "Yusuf Alireza",
         (now - timedelta(days=14)).strftime("%Y-%m-%d"),
         "Schedule Q2 performance review",
         (now + timedelta(days=7)).strftime("%Y-%m-%d"),
         "COMPLETE", (now + timedelta(days=365)).strftime("%Y-%m-%d")),
        ("Gulf Sovereign Investment Authority", "GSIA Abu Dhabi",
         "SOVEREIGN", "pm@gsia.ae", "UAE",
         100_000_000, "Innes Harding",
         (now - timedelta(days=45)).strftime("%Y-%m-%d"),
         "Send monthly investor letter",
         (now - timedelta(days=5)).strftime("%Y-%m-%d"),  # overdue
         "COMPLETE", (now + timedelta(days=180)).strftime("%Y-%m-%d")),
        ("Meridian Capital Partners", "Meridian CP Cayman",
         "INSTITUTIONAL", "ops@meridian.ky", "Cayman Islands",
         15_000_000, "Yusuf Alireza",
         (now - timedelta(days=7)).strftime("%Y-%m-%d"),
         "Follow up on redemption request",
         (now + timedelta(days=2)).strftime("%Y-%m-%d"),
         "IN_PROGRESS", None),
        ("Al-Noor Family Trust", "Al-Noor Trust DIFC",
         "HNW", "family@alnoor.ae", "UAE",
         8_000_000, "Innes Harding",
         (now - timedelta(days=90)).strftime("%Y-%m-%d"),  # overdue
         "KYC renewal — documents requested",
         (now - timedelta(days=30)).strftime("%Y-%m-%d"),  # overdue
         "EXPIRED", (now - timedelta(days=30)).strftime("%Y-%m-%d")),
    ]

    con.executemany(
        """INSERT INTO crm_contacts
           (name, entity, tier, email, jurisdiction, aum_committed,
            relationship_mgr, last_contact, next_action, next_action_due,
            kyc_status, kyc_expiry)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        contacts,
    )

    # Add a pending subscription
    con.execute(
        """INSERT INTO crm_subscriptions (contact_id, type, amount, currency, status)
           VALUES (3, 'REDEMPTION', 2000000, 'USD', 'PENDING')"""
    )

    con.commit()
    count = len(contacts)
    con.close()
    return count
