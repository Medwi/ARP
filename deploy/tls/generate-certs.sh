#!/usr/bin/env bash
# Generate self-signed TLS certs for the docker-compose.prod.yml nginx proxy.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CERT_DIR="${DIR}/certs"
mkdir -p "${CERT_DIR}"

openssl req -x509 -nodes -newkey rsa:2048 -days 365 \
    -keyout "${CERT_DIR}/arp.key" \
    -out "${CERT_DIR}/arp.crt" \
    -subj "/CN=arp.local/O=ARP Global Capital/C=AE"

chmod 600 "${CERT_DIR}/arp.key"
echo "✅  TLS certs written to ${CERT_DIR}/"
echo "    Dashboard: https://localhost:8443/"
echo "    API:       https://localhost:8443/api/health"
