# ARP Investment Intelligence Platform
# Single image used for both backend (FastAPI) and frontend (Streamlit) services
# The SERVICE env var controls which process starts

FROM python:3.11-slim

# Security: run as non-root
RUN groupadd -r arp && useradd -r -g arp arp

WORKDIR /app

# System dependencies (sqlite3 for ops scripts; curl for healthchecks)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    sqlite3 \
    && rm -rf /var/lib/apt/lists/*

# Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application code (no secrets, no .env)
COPY backend/   ./backend/
COPY frontend/  ./frontend/
COPY seed/      ./seed/
COPY db/        ./db/
COPY knowledge/ ./knowledge/
COPY scripts/   ./scripts/

# Data directory (mounted as volume in docker-compose)
RUN mkdir -p /data && chown arp:arp /data

USER arp

EXPOSE 8000 8501

# Entrypoint selects process based on SERVICE env var
CMD if [ "$SERVICE" = "frontend" ]; then \
        streamlit run frontend/app.py \
            --server.port=8501 \
            --server.address=0.0.0.0 \
            --server.headless=true \
            --browser.gatherUsageStats=false; \
    elif [ "$SERVICE" = "seed" ]; then \
        python seed/seed.py; \
    else \
        uvicorn backend.main:app \
            --host 0.0.0.0 \
            --port 8000 \
            --workers 1; \
    fi
