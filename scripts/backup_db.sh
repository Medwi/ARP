#!/usr/bin/env bash
# scripts/backup_db.sh
# Creates a timestamped, verified backup of the ARP platform SQLite database.
#
# Usage:
#   ./scripts/backup_db.sh                        # backup to ./backups/
#   ./scripts/backup_db.sh /path/to/backup/dir    # backup to custom path
#   ./scripts/backup_db.sh --restore <file>       # restore from a backup
#   ./scripts/backup_db.sh --list                 # list available backups
#
# The backup uses SQLite's .backup command (hot backup — safe on live DB).
# Each backup is verified with integrity_check before being retained.
# Backups older than 30 days are automatically pruned.
#
# In production: schedule via cron at 02:00 AM daily.
# Example cron entry:
#   0 2 * * * /app/scripts/backup_db.sh /mnt/backups >> /var/log/arp_backup.log 2>&1

set -euo pipefail

DB_PATH="${DB_PATH:-/data/arp.db}"
BACKUP_DIR="${1:-./backups}"
RETENTION_DAYS=30
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
BACKUP_FILE="${BACKUP_DIR}/arp_db_${TIMESTAMP}.db"
MANIFEST_FILE="${BACKUP_DIR}/backup_manifest.txt"

# ── Restore mode ──────────────────────────────────────────────────────────────
if [[ "${1:-}" == "--restore" ]]; then
    RESTORE_FILE="${2:-}"
    if [[ -z "${RESTORE_FILE}" ]] || [[ ! -f "${RESTORE_FILE}" ]]; then
        echo "  ❌  Usage: $0 --restore <backup_file>"
        exit 1
    fi
    echo ""
    echo "  ⚠️   RESTORE MODE"
    echo "  Source: ${RESTORE_FILE}"
    echo "  Target: ${DB_PATH}"
    read -p "  This will OVERWRITE the current database. Type 'yes' to confirm: " confirm
    if [[ "${confirm}" != "yes" ]]; then
        echo "  Aborted."
        exit 0
    fi
    cp "${DB_PATH}" "${DB_PATH}.pre_restore_$(date +%Y%m%d_%H%M%S)"
    sqlite3 "${RESTORE_FILE}" ".backup '${DB_PATH}'"
    echo "  ✅  Restore complete. Previous DB saved as .pre_restore_* in same directory."
    exit 0
fi

# ── List mode ─────────────────────────────────────────────────────────────────
if [[ "${1:-}" == "--list" ]]; then
    echo ""
    echo "  Available backups:"
    if ls "${BACKUP_DIR}"/arp_db_*.db 2>/dev/null | head -20; then
        echo ""
        [[ -f "${MANIFEST_FILE}" ]] && cat "${MANIFEST_FILE}" | tail -10
    else
        echo "  (no backups found in ${BACKUP_DIR})"
    fi
    exit 0
fi

# ── Backup mode ───────────────────────────────────────────────────────────────
echo ""
echo "  ARP Platform — Database Backup"
echo "  ────────────────────────────────"
echo "  Source: ${DB_PATH}"
echo "  Target: ${BACKUP_FILE}"

# Check source exists
if [[ ! -f "${DB_PATH}" ]]; then
    echo "  ❌  Database not found at ${DB_PATH}"
    echo "      Ensure the platform has been seeded: ./EXEC or docker compose up"
    exit 1
fi

# Create backup directory
mkdir -p "${BACKUP_DIR}"

# ── Hot backup (sqlite3 CLI or Python fallback) ─────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if command -v sqlite3 >/dev/null 2>&1; then
    echo "  📦  Creating hot backup (sqlite3)..."
    sqlite3 "${DB_PATH}" ".backup '${BACKUP_FILE}'"

    echo "  🔍  Verifying backup integrity..."
    INTEGRITY=$(sqlite3 "${BACKUP_FILE}" "PRAGMA integrity_check;" 2>&1)
    if [[ "${INTEGRITY}" != "ok" ]]; then
        echo "  ❌  Integrity check FAILED: ${INTEGRITY}"
        rm -f "${BACKUP_FILE}"
        exit 1
    fi

    USERS=$(sqlite3 "${BACKUP_FILE}" "SELECT COUNT(*) FROM users;" 2>/dev/null || echo "?")
    TRADES=$(sqlite3 "${BACKUP_FILE}" "SELECT COUNT(*) FROM trades;" 2>/dev/null || echo "?")
    AUDIT=$(sqlite3 "${BACKUP_FILE}" "SELECT COUNT(*) FROM audit_logs;" 2>/dev/null || echo "?")
    SIZE=$(du -sh "${BACKUP_FILE}" | cut -f1)

    echo "  ✅  Backup verified — size=${SIZE} users=${USERS} trades=${TRADES} audit=${AUDIT}"
    echo "${TIMESTAMP} | ${BACKUP_FILE} | size=${SIZE} | users=${USERS} trades=${TRADES} audit=${AUDIT} | VERIFIED" \
        >> "${MANIFEST_FILE}"
    CHECKSUM=$(sha256sum "${BACKUP_FILE}" | cut -d' ' -f1)
    echo "${CHECKSUM}  ${BACKUP_FILE}" > "${BACKUP_FILE}.sha256"
    echo "      SHA-256: ${CHECKSUM:0:16}…"

    PRUNED=0
    while IFS= read -r -d '' old_backup; do
        rm -f "${old_backup}" "${old_backup}.sha256"
        PRUNED=$((PRUNED + 1))
    done < <(find "${BACKUP_DIR}" -name "arp_db_*.db" \
        -mtime "+${RETENTION_DAYS}" -print0 2>/dev/null)
    [[ ${PRUNED} -gt 0 ]] && echo "  🗑️   Pruned ${PRUNED} backup(s) older than ${RETENTION_DAYS} days"
    echo "  ✅  Backup complete: ${BACKUP_FILE}"
else
    echo "  📦  sqlite3 CLI not found — using Python backup API..."
    DB_PATH="${DB_PATH}" python3 "${SCRIPT_DIR}/backup_db.py" --dir "${BACKUP_DIR}"
fi
echo ""
