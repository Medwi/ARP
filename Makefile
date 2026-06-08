.PHONY: up down up-prod test test-all test-expansion smoke security seed-local logs-tokens \
        bootstrap-tokens healthcheck healthcheck-quick backup-db warm-model rotate-tokens \
        tls-certs

up:
	docker compose up --build

up-prod:
	docker compose -f docker-compose.yml -f docker-compose.prod.yml up --build

tls-certs:
	./deploy/tls/generate-certs.sh

down:
	docker compose down

test:
	pytest tests/ -m fundamental

test-all:
	pytest tests/ -m ""

test-expansion:
	pytest tests/ -m expansion -v

smoke:
	python scripts/smoke.py

security:
	bandit -r backend/ seed/ -ll -x tests/ --format txt
	./scripts/safety_check.sh

seed-local:
	DB_PATH=$${DB_PATH:-/tmp/arp.db} python seed/seed.py

logs-tokens:
	@echo "Deprecated: tokens are no longer printed to seed logs."
	@$(MAKE) bootstrap-tokens

bootstrap-tokens:
	@if [ -f data/.bootstrap_tokens ]; then cat data/.bootstrap_tokens; \
	elif docker exec arp_backend test -f /data/.bootstrap_tokens 2>/dev/null; then \
		docker exec arp_backend cat /data/.bootstrap_tokens; \
	else echo "No bootstrap file — run seed (make seed-local or docker compose up)"; fi

healthcheck:
	./scripts/healthcheck.sh

healthcheck-quick:
	./scripts/healthcheck.sh --quick

backup-db:
	./scripts/backup_db.sh

warm-model:
	./scripts/warm_model.sh

rotate-tokens:
	./scripts/rotate_tokens.sh
