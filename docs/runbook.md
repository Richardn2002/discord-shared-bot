# Operations Runbook

For whoever runs this in production (you). Complements the developer handoff.

## Layout on the server

```
~/discord-shared-bot/        # repo (public upstream: github.com/Richardn2002/discord-shared-bot)
  config.json                # NOT in git — token, access_token, listen/port/domain, site_title/fonts/log_level, projects_enabled, archive
  venv/                      # shared environment; platform deps live here
  files/                     # git repo (owner-initialized) with all projects; .env files ignored
  run.sh                     # ./run.sh to start (binds CONFIG.listen:CONFIG.port)
  archive/                   # local SQLite index/search DB + backups; attachments on Nextcloud
```

nginx (cloudflare full TLS, origin certs), location `/bot/` →
`proxy_pass http://127.0.0.1:PORT/;` with prefix strip. `domain` in
config.json includes the `/bot` prefix and must match.

## Everyday commands

| need | how |
|---|---|
| start / stop the platform | `./run.sh` (or your systemd unit wrapping it) / `pkill -f 'uvicorn app.main'` |
| platform status | `curl -H "X-Token: $ACCESS" localhost:PORT/api/status` |
| per-project worker state | `curl -H "X-Token: $ACCESS" localhost:PORT/api/projects/<slug>` → `runtime.state` |
| restart one project's worker | `POST /api/projects/<slug>/worker {"action":"restart"}` (clears a stopped flag) |
| a project misbehaving | worker Stop in dashboard → events skip it; or delete the project entirely |
| add a python package | Packages page (or `POST /api/packages/install {"spec":...}`) |
| bot offline | check config.json `token`, intents in Discord portal, nginx/cloudflare |
| archive-only test instance | set `projects_enabled: false` or env `PROJECTS_ENABLED=0`; bot archives but does not dispatch project events |
| force archive catch-up | `./venv/bin/python tools/archive_backfill.py` |
| full archive reconcile (catch old edits/missed deletes) | `./venv/bin/python tools/archive_reconcile.py --only-channel <id>` or `--guild <id>` or `--all` |
| reconcile without deleting missing messages | add `--no-prune` |
| archive stats/quality | `./venv/bin/python tools/archive_status.py --monthly --top 10` |
| archive backup now | `./venv/bin/python tools/archive_backup.py` |

## State machine for project workers

`sleeping` → (first event | deploy | start) → `running` → (stop) →
`sleeping` (disabled until started) · (handler crash/timeout) → killed +
restarted automatically · (module import fails) → `broken` until
deploy/restart. "Sleeping" is normal and means no process exists yet — the
next event spawns it on demand. Idle CPU of a running worker is ~0; RAM is
~10–25 MB per *awake* project (more if handlers import heavy libs — that's
the scaling term to watch, not project count).

## Incidents and what to look at

- **"deployed" but nothing answers**: project runtime state + project
  Console tab (init errors and tracebacks land there).
- **one project floods chat**: its Console tab shows what it emitted; the
  tolerance/timeout settings bound worst-case latency.
- **platform-wide slowness**: check `docker stats`/ps for venv python
  workers; heavy imports per worker are the usual suspect.
- **kv corrupted**: `kv.json.corrupt` backup appears next to kv.json;
  restore by hand.

## Upgrading the platform

1. `git pull` (repo is public upstream).
2. New pip deps: `./venv/bin/pip install -r requirements.txt`.
3. Restart (`run.sh`). Workers respawn lazily; a project with import-time
   errors will show `broken` — its Console tab has the traceback.
4. Re-check `config.example.json` for new keys; config.json merges defaults,
   missing keys keep working.

## Security notes (trusted-friends deployment)

- Single shared access token gates every `/api/*`; the landing page and
  static assets are public. Rotate the token in config.json when someone
  leaves.
- `.env` files hold API keys; `files/.gitignore` ignores them (never put a
  trailing comment on a gitignore rule — it breaks the pattern).
- Project code runs unsandboxed as the platform's OS user. Everyone is
  trusted; still, keep the box patched and the token private.
