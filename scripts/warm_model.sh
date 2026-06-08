#!/usr/bin/env bash
# scripts/warm_model.sh
# Pre-warm the Ollama model before a demo or interview.

set -euo pipefail

MODEL="${1:-${OLLAMA_MODEL:-phi3:mini}}"
OLLAMA_HOST="${OLLAMA_HOST:-http://localhost:11434}"
CHECK_ONLY="${1:-}"

echo ""
echo "  ARP Platform — Model Warm-Up"
echo "  ─────────────────────────────"

if ! curl -sf "${OLLAMA_HOST}/api/version" > /dev/null 2>&1; then
    echo "  ❌  Ollama not reachable at ${OLLAMA_HOST}"
    echo "      Start with: docker compose up -d ollama"
    exit 1
fi
echo "  ✅  Ollama reachable at ${OLLAMA_HOST}"

if [[ "${CHECK_ONLY}" == "--check" ]]; then
    MODELS=$(curl -sf "${OLLAMA_HOST}/api/tags" | python3 -c "
import sys, json
data = json.load(sys.stdin)
for m in data.get('models', []):
    print(' ', m['name'])
" 2>/dev/null || echo "  (none)")
    echo "  Available models:"
    echo "${MODELS}"
    exit 0
fi

PULLED=$(curl -sf "${OLLAMA_HOST}/api/tags" | python3 -c "
import sys, json
data = json.load(sys.stdin)
names = [m['name'] for m in data.get('models', [])]
print('yes' if any('${MODEL}'.split(':')[0] in n for n in names) else 'no')
" 2>/dev/null || echo "no")

if [[ "${PULLED}" == "no" ]]; then
    echo "  ⬇️   Model '${MODEL}' not found. Pulling now..."
    echo "       This may take several minutes on first run."
    docker exec arp_ollama ollama pull "${MODEL}"
    echo "  ✅  Model pulled successfully"
fi

echo "  🔥  Warming model '${MODEL}'..."
START=$(date +%s)

RESPONSE=$(curl -sf -X POST "${OLLAMA_HOST}/api/generate" \
    -H "Content-Type: application/json" \
    -d "{
        \"model\": \"${MODEL}\",
        \"prompt\": \"Respond with exactly: WARM\",
        \"stream\": false,
        \"options\": {\"num_predict\": 5, \"temperature\": 0}
    }" 2>/dev/null | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    print(data.get('response', '').strip())
except Exception:
    print('ERROR')
")

END=$(date +%s)
ELAPSED=$((END - START))

if [[ -z "${RESPONSE}" ]] || [[ "${RESPONSE}" == "ERROR" ]]; then
    echo "  ⚠️   Warm-up response unexpected: '${RESPONSE}'"
    echo "       Model may still be loading. Try again in 30 seconds."
    exit 1
fi

echo "  ✅  Model warm — responded in ${ELAPSED}s"
echo "  🚀  Ready for demo. First query will be fast."
echo ""

FALLBACK="${OLLAMA_FALLBACK_MODEL:-tinyllama}"
if [[ "${FALLBACK}" != "${MODEL}" ]]; then
    FALLBACK_PULLED=$(curl -sf "${OLLAMA_HOST}/api/tags" | python3 -c "
import sys, json
data = json.load(sys.stdin)
names = [m['name'] for m in data.get('models', [])]
print('yes' if any('${FALLBACK}'.split(':')[0] in n for n in names) else 'no')
" 2>/dev/null || echo "no")

    if [[ "${FALLBACK_PULLED}" == "yes" ]]; then
        curl -sf -X POST "${OLLAMA_HOST}/api/generate" \
            -H "Content-Type: application/json" \
            -d "{\"model\": \"${FALLBACK}\", \"prompt\": \"WARM\", \"stream\": false, \"options\": {\"num_predict\": 3}}" \
            > /dev/null 2>&1 || true
        echo "  ✅  Fallback model '${FALLBACK}' also warmed."
        echo ""
    fi
fi
