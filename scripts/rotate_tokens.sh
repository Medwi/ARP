#!/usr/bin/env bash
# scripts/rotate_tokens.sh
# Rotate bearer tokens for one or all users without reseeding the database.

set -euo pipefail

DB_PATH="${DB_PATH:-/data/arp.db}"
ACTION="${1:-rotate_all}"
TARGET="${2:-}"

GREEN='\033[0;32m'; RED='\033[0;31m'; YELLOW='\033[1;33m'
BOLD='\033[1m'; NC='\033[0m'

echo ""
echo -e "${BOLD}  ARP Platform — Token Management${NC}"
echo -e "  $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
echo "  ─────────────────────────────────"

if ! docker exec arp_backend sqlite3 "${DB_PATH}" "SELECT 1;" > /dev/null 2>&1; then
    echo -e "  ${RED}❌  Cannot reach database at ${DB_PATH}${NC}"
    echo "      Ensure arp_backend container is running: make run"
    exit 1
fi

if [[ "${ACTION}" == "--list" ]]; then
    echo "  Current users and token hashes (first 16 chars):"
    docker exec arp_backend sqlite3 "${DB_PATH}" \
        "SELECT printf('  %-20s %-12s #%.16s', email, role, token) FROM users ORDER BY role;" \
        2>/dev/null || echo "  Error reading users"
    echo ""
    exit 0
fi

if [[ "${ACTION}" == "--revoke" ]]; then
    if [[ -z "${TARGET}" ]]; then
        echo -e "  ${RED}Usage: $0 --revoke <email>${NC}"
        exit 1
    fi
    echo -e "  ${YELLOW}⚠️   Revoking access for: ${TARGET}${NC}"
    docker exec arp_backend sqlite3 "${DB_PATH}" \
        "UPDATE users SET token = 'REVOKED_$(date +%s)' WHERE email = '${TARGET}';"
    echo -e "  ${GREEN}✅  Token revoked. ${TARGET} can no longer authenticate.${NC}"
    echo "      To restore access, run: $0 ${TARGET}"
    echo ""
    exit 0
fi

rotate_user() {
    local email="$1"
    NEW_TOKEN=$(python3 -c "import secrets; print(secrets.token_hex(24))")
    NEW_HASH=$(python3 -c "
import hashlib
print(hashlib.sha256('${NEW_TOKEN}'.encode()).hexdigest())
")
    ROWS_AFFECTED=$(docker exec arp_backend sqlite3 "${DB_PATH}" \
        "UPDATE users SET token = '${NEW_HASH}' WHERE email = '${email}'; \
         SELECT changes();" 2>/dev/null | tail -1)

    if [[ "${ROWS_AFFECTED:-0}" -gt 0 ]]; then
        printf "  ${GREEN}✅${NC}  %-22s %s\n" "${email}" "${NEW_TOKEN}"
    else
        printf "  ${RED}❌${NC}  %-22s NOT FOUND\n" "${email}"
    fi
}

if [[ -n "${ACTION}" ]] && [[ "${ACTION}" != "rotate_all" ]] && [[ "${ACTION}" != "--list" ]]; then
    echo "  Rotating token for: ${ACTION}"
    echo ""
    echo "  ⚠️  NEW TOKENS — printed once only, never stored:"
    echo "  ─────────────────────────────────────────────────"
    rotate_user "${ACTION}"
else
    echo "  Rotating tokens for all users..."
    echo ""
    echo "  ⚠️  NEW TOKENS — printed once only, never stored:"
    echo "  ─────────────────────────────────────────────────"
    EMAILS=$(docker exec arp_backend sqlite3 "${DB_PATH}" \
        "SELECT email FROM users ORDER BY role;" 2>/dev/null)
    while IFS= read -r email; do
        [[ -n "${email}" ]] && rotate_user "${email}"
    done <<< "${EMAILS}"
fi

echo ""
echo "  ─────────────────────────────────────────────────"
echo -e "  ${YELLOW}Copy these tokens now — they cannot be retrieved later.${NC}"
echo -e "  ${YELLOW}Distribute securely to each user (never via email).${NC}"
echo ""
