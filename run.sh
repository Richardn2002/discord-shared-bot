#!/usr/bin/env bash
# Start the platform (web UI + bot). Configure listen/port/token in config.json.
cd "$(dirname "$0")"
exec ./venv/bin/python -m uvicorn app.main:app \
  --host "$(./venv/bin/python -c 'from app.config import CONFIG; print(CONFIG.listen)')" \
  --port "$(./venv/bin/python -c 'from app.config import CONFIG; print(CONFIG.port)')" \
  --log-level "$(./venv/bin/python -c 'from app.config import CONFIG; print(CONFIG.log_level.lower())')"
