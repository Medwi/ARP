# Persistence & data scope

## Assessment default (this repo)

| Setting | Value | Meaning |
|---------|--------|---------|
| `ARP_DATA_SCOPE` | `demo_snapshot` | Portfolio, P&L, and risk figures come from a **seeded demonstration book** |
| `ARP_PERSISTENCE` | `sqlite_demo` | Single-file SQLite at `DB_PATH` — suitable for local dev and technical assessment |

This is **not** a production fund database. NAV, regulatory capital, and filing calendars may be **synthetic** unless wired to external systems.

### SQLite limitations (assessment only)

| Concern | This demo | Production target (`ARP_PERSISTENCE=postgresql`) |
|---------|-----------|--------------------------------------------------|
| **High availability** | Single file on one host — no failover or replica set | Managed PostgreSQL (e.g. RDS/Aurora) with multi-AZ |
| **Concurrent writers** | One SQLite file; multiple API workers risk lock contention | PostgreSQL row-level locking; connection pooling |
| **Backup / RPO** | Manual `make backup-db` / `scripts/backup_db.py` — no automated PITR | Continuous backup, PITR, defined RPO/RTO in runbook |
| **Scale** | Suitable for local assessment and single-operator demos | Horizontal API replicas + shared PostgreSQL |

The **tool layer, RBAC, and audit APIs are unchanged** across both modes — only the persistence backend and ingest path differ. See `backend/config.py` (`persistence_summary`) and `/health` for runtime disclosure.

## Production-shaped target

| Component | Assessment | Production |
|-----------|------------|------------|
| Book data | SQLite seed | PostgreSQL fed by Broadridge / admin ingest |
| Pipeline sync | Simulated heartbeat (`pipeline_runs`) | AWS Glue → PostgreSQL (`me-central-1`) |
| Manager accounts | Synthetic DFSA tests | Accounting system + compliance workflow |
| LLM | Local Ollama | Same pattern (on-prem or VPC-private) |

The **tool layer, RBAC, and audit APIs are unchanged** across both modes — only `ARP_DATA_SCOPE` and persistence backend differ.

## Environment flags

```bash
# Demo (default)
ARP_DATA_SCOPE=demo_snapshot
ARP_PERSISTENCE=sqlite_demo

# Production-shaped (when live ingest is wired)
ARP_DATA_SCOPE=live
ARP_PERSISTENCE=postgresql
```

## API transparency

Successful portfolio and reporting responses include:

- `data_scope` — `demo_snapshot` or `live`
- `data_scope_note` — human-readable disclaimer
- `synthetic` — `true` when figures are illustrative

Pipeline `/pipeline/status` separates **pipeline health** (sync heartbeat) from **book freshness** so a successful simulate-sync never implies live NAV.

## One-liner for reviewers

> Assessment persistence is SQLite on a demo book (`demo_snapshot`). PostgreSQL is the named production target for HA, concurrent writers, and defined backup RPO — the application layer is already structured for that swap.
