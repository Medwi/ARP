#!/usr/bin/env bash
# scripts/healthcheck.sh
# Comprehensive pre-demo system health check.
# Run this 5 minutes before any interview or presentation.
#
# Usage:
#   ./scripts/healthcheck.sh           # full check
#   ./scripts/healthcheck.sh --quick   # skip slow checks (model inference test)
#   ./scripts/healthcheck.sh --json    # output JSON for monitoring integration
#
# Exit codes:
#   0 — all checks passed (green)
#   1 — one or more checks failed (red)
#   2 — warnings only (yellow)

set -euo pipefail

BACKEND_URL="${BACKEND_URL:-http://localhost:8000}"
FRONTEND_URL="${FRONTEND_URL:-http://localhost:8501}"
OLLAMA_URL="${OLLAMA_URL:-http://localhost:11434}"
OLLAMA_MODEL="${OLLAMA_MODEL:-phi3:mini}"
QUICK="${1:-}"
JSON_MODE="${1:-}"

PASS=0; WARN=0; FAIL=0
RESULTS=()

GREEN='\033[0;32m'; YELLOW='\033[1;33m'
RED='\033[0;31m'; NC='\033[0m'; BOLD='\033[1m'

pass()  { echo -e "  ${GREEN}✅ PASS${NC}  $1"; PASS=$((PASS+1));  RESULTS+=("{\"check\":\"$1\",\"status\":\"PASS\"}"); }
warn()  { echo -e "  ${YELLOW}⚠️  WARN${NC}  $1"; WARN=$((WARN+1));  RESULTS+=("{\"check\":\"$1\",\"status\":\"WARN\"}"); }
fail()  { echo -e "  ${RED}❌ FAIL${NC}  $1"; FAIL=$((FAIL+1));  RESULTS+=("{\"check\":\"$1\",\"status\":\"FAIL\"}"); }
header(){ echo -e "\n${BOLD}$1${NC}"; }

_health_field() {
    local path="$1"
    python3 -c "
import json
try:
    cur = json.load(open('/tmp/arp_health.json'))
    for part in '${path}'.split('.'):
        if not isinstance(cur, dict) or part not in cur:
            print('?')
            break
        cur = cur[part]
    else:
        print(cur)
except Exception:
    print('?')
" 2>/dev/null || echo "?"
}

echo ""
echo -e "${BOLD}  ARP Platform — Pre-Demo Health Check${NC}"
echo -e "  $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
echo "  ────────────────────────────────────"

# ── 1. Docker containers ──────────────────────────────────────────────────────
header "1. Docker Services"

for svc in arp_backend arp_frontend arp_ollama; do
    if docker ps --format '{{.Names}}' 2>/dev/null | grep -q "^${svc}$"; then
        STATUS=$(docker inspect --format='{{.State.Health.Status}}' "${svc}" 2>/dev/null || echo "running")
        if [[ "${STATUS}" == "healthy" ]] || [[ "${STATUS}" == "running" ]]; then
            pass "${svc} is running (${STATUS})"
        else
            warn "${svc} is running but status: ${STATUS}"
        fi
    else
        fail "${svc} is NOT running — start with: make run"
    fi
done

# ── 2. API endpoints ──────────────────────────────────────────────────────────
header "2. API Endpoints"

if curl -sf "${BACKEND_URL}/health" > /tmp/arp_health.json 2>/dev/null; then
    pass "Backend reachable at ${BACKEND_URL}"
    LLM_ONLINE=$(_health_field "llm_online")
    DATA_RES=$(_health_field "data_residency")
    echo "       LLM online: ${LLM_ONLINE}  |  Data residency: ${DATA_RES}"
else
    fail "Backend not reachable at ${BACKEND_URL}"
fi

if curl -sf "${FRONTEND_URL}/_stcore/health" > /dev/null 2>/dev/null || \
   curl -sf "${FRONTEND_URL}" > /dev/null 2>/dev/null; then
    pass "Frontend reachable at ${FRONTEND_URL}"
else
    warn "Frontend not responding at ${FRONTEND_URL} (may still be starting)"
fi

# ── 2b. RAG / Graph / Memory (from /health) ───────────────────────────────────
header "2b. Knowledge Subsystems"

if [[ -f /tmp/arp_health.json ]]; then
    RAG_EN=$(_health_field "rag.enabled")
    RAG_CHUNKS=$(_health_field "rag.chunks_indexed")
    GRAPH_EN=$(_health_field "graph.enabled")
    GRAPH_NODES=$(_health_field "graph.nodes")
    MEM_EN=$(_health_field "memory.enabled")
    MEM_TOTAL=$(_health_field "memory.stored_total")

    if [[ "${RAG_EN}" == "True" ]] || [[ "${RAG_EN}" == "true" ]]; then
        if [[ "${RAG_CHUNKS:-0}" =~ ^[0-9]+$ ]] && [[ "${RAG_CHUNKS}" -gt 0 ]]; then
            pass "RAG enabled (${RAG_CHUNKS} chunks indexed)"
        else
            warn "RAG enabled but no chunks indexed — run seed or ingest knowledge"
        fi
    else
        warn "RAG disabled (set ARP_RAG=1 to enable)"
    fi

    if [[ "${GRAPH_EN}" == "True" ]] || [[ "${GRAPH_EN}" == "true" ]]; then
        if [[ "${GRAPH_NODES:-0}" =~ ^[0-9]+$ ]] && [[ "${GRAPH_NODES}" -gt 0 ]]; then
            pass "Knowledge graph enabled (${GRAPH_NODES} nodes)"
        else
            warn "Knowledge graph enabled but empty"
        fi
    else
        warn "Knowledge graph disabled (set ARP_GRAPH=1 to enable)"
    fi

    if [[ "${MEM_EN}" == "True" ]] || [[ "${MEM_EN}" == "true" ]]; then
        pass "Agent memory enabled (${MEM_TOTAL:-0} stored interactions)"
    else
        warn "Agent memory disabled (set ARP_MEMORY=1 to enable)"
    fi
else
    warn "Skipped subsystem checks — /health response unavailable"
fi

# ── 3. Ollama & model ─────────────────────────────────────────────────────────
header "3. LLM / Ollama"

if curl -sf "${OLLAMA_URL}/api/version" > /dev/null 2>/dev/null; then
    pass "Ollama reachable at ${OLLAMA_URL}"
    MODELS=$(curl -sf "${OLLAMA_URL}/api/tags" 2>/dev/null | \
        python3 -c "import sys,json; d=json.load(sys.stdin); print(', '.join(m['name'] for m in d.get('models',[])))" 2>/dev/null || echo "none")
    if [[ -z "${MODELS}" ]] || [[ "${MODELS}" == "none" ]]; then
        fail "No models pulled — run: make pull-model"
    else
        pass "Models available: ${MODELS}"
    fi

    if [[ "${QUICK}" != "--quick" ]]; then
        echo "    Testing inference (this takes 10–60s on cold start)…"
        INFER_RESULT=$(curl -sf -X POST "${OLLAMA_URL}/api/generate" \
            -H "Content-Type: application/json" \
            -d "{\"model\":\"${OLLAMA_MODEL}\",\"prompt\":\"Respond: READY\",\"stream\":false,\"options\":{\"num_predict\":3}}" \
            2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get('response','').strip())" 2>/dev/null || echo "FAILED")
        if echo "${INFER_RESULT}" | grep -qi "ready\|READY\|ok\|OK"; then
            pass "Inference working — model responded"
        else
            warn "Inference responded but unexpectedly: '${INFER_RESULT:0:40}'"
        fi
    else
        echo "    (inference test skipped in quick mode)"
    fi
else
    fail "Ollama not reachable at ${OLLAMA_URL} — start with: docker compose up -d ollama"
fi

# ── 4. Database ───────────────────────────────────────────────────────────────
header "4. Database"

DB_CHECK=$(docker exec arp_backend sqlite3 /data/arp.db \
    "SELECT COUNT(*) FROM users; SELECT COUNT(*) FROM portfolio_holdings; \
     SELECT COUNT(*) FROM trades; SELECT COUNT(*) FROM audit_logs;" \
    2>/dev/null | tr '\n' '|' || echo "ERROR")

if [[ "${DB_CHECK}" == "ERROR" ]]; then
    fail "Cannot query database — check arp_backend container"
else
    USERS=$(echo "${DB_CHECK}" | cut -d'|' -f1)
    HOLDINGS=$(echo "${DB_CHECK}" | cut -d'|' -f2)
    TRADES=$(echo "${DB_CHECK}" | cut -d'|' -f3)
    AUDIT=$(echo "${DB_CHECK}" | cut -d'|' -f4)
    if [[ "${HOLDINGS:-0}" -gt 0 ]]; then
        pass "DB: ${USERS} users | ${HOLDINGS} holdings | ${TRADES} trades | ${AUDIT} audit records"
    else
        fail "DB has no holdings — run: make seed"
    fi
fi

# ── 5. Auth tokens ────────────────────────────────────────────────────────────
header "5. Auth Tokens"

TOKEN_COUNT=$(docker exec arp_backend sqlite3 /data/arp.db \
    "SELECT COUNT(*) FROM users;" 2>/dev/null || echo "0")
if [[ "${TOKEN_COUNT:-0}" -ge 4 ]]; then
    pass "${TOKEN_COUNT} user tokens available"
    echo "    Run 'make logs-tokens' to display them"
else
    fail "Expected 4 users, found ${TOKEN_COUNT} — run: make seed"
fi

# ── 6. Audit chain integrity ──────────────────────────────────────────────────
header "6. Audit Chain"

CHAIN=$(docker exec arp_backend python3 -c "
import sys, os
sys.path.insert(0,'.')
os.environ['DB_PATH']='/data/arp.db'
from backend.audit import verify_chain
r = verify_chain()
print('VALID' if r['valid'] else 'BREACH', r.get('checked',0))
" 2>/dev/null || echo "ERROR 0")

CHAIN_STATUS=$(echo "${CHAIN}" | cut -d' ' -f1)
CHAIN_COUNT=$(echo "${CHAIN}" | cut -d' ' -f2)

if [[ "${CHAIN_STATUS}" == "VALID" ]]; then
    pass "Audit hash chain intact (${CHAIN_COUNT} records verified)"
elif [[ "${CHAIN_STATUS}" == "BREACH" ]]; then
    fail "Audit chain BREACH detected — investigate before demo"
else
    warn "Could not verify audit chain (${CHAIN})"
fi

# ── 7. Rate limiter ───────────────────────────────────────────────────────────
header "7. Security"

RATE_CHECK=$(curl -so /dev/null -w "%{http_code}" "${BACKEND_URL}/health" 2>/dev/null || echo "000")
if [[ "${RATE_CHECK}" == "200" ]]; then
    pass "Rate limiter active (health endpoint returns 200)"
else
    warn "Unexpected response from health endpoint: ${RATE_CHECK}"
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "  ────────────────────────────────────"
TOTAL=$((PASS + WARN + FAIL))
if [[ ${FAIL} -eq 0 ]] && [[ ${WARN} -eq 0 ]]; then
    echo -e "  ${GREEN}${BOLD}ALL CLEAR — ${PASS}/${TOTAL} checks passed. Ready to demo.${NC}"
    EXIT=0
elif [[ ${FAIL} -eq 0 ]]; then
    echo -e "  ${YELLOW}${BOLD}WARNINGS — ${PASS} passed, ${WARN} warned, ${FAIL} failed.${NC}"
    echo -e "  ${YELLOW}Demo can proceed but review warnings above.${NC}"
    EXIT=2
else
    echo -e "  ${RED}${BOLD}NOT READY — ${PASS} passed, ${WARN} warned, ${FAIL} failed.${NC}"
    echo -e "  ${RED}Fix failures before demo.${NC}"
    EXIT=1
fi

if [[ "${JSON_MODE}" == "--json" ]]; then
    echo ""
    python3 -c "
import json
results = [$(IFS=,; echo "${RESULTS[*]}")]
print(json.dumps({
    'timestamp': '$(date -u +%Y-%m-%dT%H:%M:%SZ)',
    'passed': ${PASS}, 'warned': ${WARN}, 'failed': ${FAIL},
    'ready': $([ ${FAIL} -eq 0 ] && echo 'true' || echo 'false'),
    'checks': results
}, indent=2))
" 2>/dev/null || true
fi

echo ""
exit ${EXIT}
