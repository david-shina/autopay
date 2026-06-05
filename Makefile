.PHONY: help up down logs restart build rebuild psql test test-unit test-integration lint format typecheck migrate seed clean nuke db-init db-reset db-shell cov ngrok keygen webhooks

# ─── Defaults ───────────────────────────────────────────────────────
COMPOSE         := docker compose
COMPOSE_TEST    := docker compose -f docker-compose.test.yml --env-file .env.test
APP_SERVICE     := app
DB_SERVICE      := db

# ─── Help ───────────────────────────────────────────────────────────
help:  ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}'

# ─── Local dev ──────────────────────────────────────────────────────
up:  ## Start app + db in foreground
	$(COMPOSE) up

up-d:  ## Start app + db in background
	$(COMPOSE) up -d

down:  ## Stop app + db
	$(COMPOSE) down

logs:  ## Tail logs from all services
	$(COMPOSE) logs -f

logs-app:  ## Tail logs from app only
	$(COMPOSE) logs -f $(APP_SERVICE)

logs-db:  ## Tail logs from db only
	$(COMPOSE) logs -f $(DB_SERVICE)

psql:  ## Open psql shell inside db container
	$(COMPOSE) exec $(DB_SERVICE) psql -U $$POSTGRES_USER -d $$POSTGRES_DB

shell:  ## Open bash inside app container
	$(COMPOSE) exec $(APP_SERVICE) /bin/bash

restart:  ## Restart app only
	$(COMPOSE) restart $(APP_SERVICE)

# ─── Build ──────────────────────────────────────────────────────────
build:  ## Build images
	$(COMPOSE) build

rebuild:  ## Rebuild without cache
	$(COMPOSE) build --no-cache

# ─── Tests ──────────────────────────────────────────────────────────
test:  ## Run full test suite
	$(COMPOSE_TEST) run --rm app-test

test-unit:  ## Run unit tests only
	docker run --rm -v $(PWD):/code -w /code --env-file .env.test \
	  python:3.12-slim sh -c "pip install -e .[dev] && pytest tests/unit -v"

test-integration:  ## Run integration tests only
	$(COMPOSE_TEST) run --rm app-test pytest tests/integration -v

cov:  ## Run tests with coverage report
	$(COMPOSE_TEST) run --rm app-test pytest --cov=app --cov-report=term-missing --cov-report=html

cov-html: cov  ## Open the HTML coverage report
	@powershell -NoProfile -Command "Start-Process 'htmlcov\index.html'"

cov-clear:  ## Remove coverage artifacts
	rm -rf .coverage htmlcov .pytest_cache

# ─── Quality ────────────────────────────────────────────────────────
lint:  ## Run ruff
	docker run --rm -v $(PWD):/code -w /code python:3.12-slim sh -c "pip install ruff==0.8.4 && ruff check ."

format:  ## Auto-format code
	docker run --rm -v $(PWD):/code -w /code python:3.12-slim sh -c "pip install ruff==0.8.4 && ruff format ."

typecheck:  ## Run mypy
	docker run --rm -v $(PWD):/code -w /code python:3.12-slim sh -c "pip install -e .[dev] && mypy app"

# ─── Migrations ─────────────────────────────────────────────────────
migrate:  ## Apply all pending migrations
	$(COMPOSE) run --rm $(APP_SERVICE) alembic upgrade head

migrate-new:  ## Create new migration (usage: make migrate-new msg="add foo")
	$(COMPOSE) run --rm $(APP_SERVICE) alembic revision --autogenerate -m "$(msg)"

migrate-down:  ## Roll back one migration
	$(COMPOSE) run --rm $(APP_SERVICE) alembic downgrade -1

# ─── Seed / data ────────────────────────────────────────────────────
seed:  ## Load dev seed data
	$(COMPOSE) run --rm $(APP_SERVICE) python -m scripts.seed

# ─── Local DB helpers (run uvicorn outside Docker) ─────────────────
db-init:  ## Load schema.sql into the local Postgres
	@echo "[db-init] applying schema.sql to $$DATABASE_URL"
	@if [ -z "$$DATABASE_URL" ]; then \
	  echo "DATABASE_URL not set — using default from app/core/config.py"; \
	fi
	@powershell -NoProfile -Command "$$env:PGPASSWORD = 'David*2020*'; $$env:PATH = 'C:\Program Files\PostgreSQL\17\bin;' + $$env:PATH; psql -U postgres -h localhost -d autopay -f schema.sql"
	@echo "[db-init] done"

db-reset:  ## Drop + recreate the local autopay database (DESTROYS DATA)
	@powershell -NoProfile -Command "$$env:PGPASSWORD = 'David*2020*'; $$env:PATH = 'C:\Program Files\PostgreSQL\17\bin;' + $$env:PATH; psql -U postgres -h localhost -d postgres -c 'DROP DATABASE IF EXISTS autopay;' -c 'CREATE DATABASE autopay;' && psql -U postgres -h localhost -d autopay -f schema.sql"
	@echo "[db-reset] done"

db-shell:  ## Open psql against the local autopay database
	@powershell -NoProfile -Command "$$env:PGPASSWORD = 'David*2020*'; $$env:PATH = 'C:\Program Files\PostgreSQL\17\bin;' + $$env:PATH; psql -U postgres -h localhost -d autopay"

# ─── Cleanup ────────────────────────────────────────────────────────
clean:  ## Remove containers + images
	$(COMPOSE) down --rmi local

nuke:  ## Remove containers, images, AND volumes (DESTROYS DB DATA)
	$(COMPOSE) down --rmi local -v

# ─── Helpers ────────────────────────────────────────────────────────
keygen:  ## Generate Fernet + JWT secrets and print them (so you can paste into .env)
	@echo "FERNET_KEY (paste into .env):"
	@powershell -NoProfile -Command "& '.\.venv\Scripts\python.exe' -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'"
	@echo ""
	@echo "JWT_SECRET_KEY (paste into .env):"
	@powershell -NoProfile -Command "& '.\.venv\Scripts\python.exe' -c 'import secrets; print(secrets.token_urlsafe(64))'"

ngrok:  ## Expose the local app on a public ngrok URL (for webhook testing)
	@if (Get-Command ngrok -ErrorAction SilentlyContinue) { \
		echo "[ngrok] starting tunnel to http://localhost:8000"; \
		ngrok http 8000 --domain $$NGROK_DOMAIN; \
	} else { \
		echo "ngrok not found. Install: winget install ngrok"; \
	}

webhooks:  ## Print the webhook URL you need to paste into Paystack dashboard
	@echo "Paystack webhook URL (paste into https://dashboard.paystack.com/#/settings/developer):"
	@echo "  http://localhost:8000/webhooks/paystack  (dev)"
	@echo "  https://<your-railway-app>.up.railway.app/webhooks/paystack  (prod)"
