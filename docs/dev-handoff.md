# Developer Handoff — Discord Shared Bot Platform

**Read this first if you are picking up development.** This document describes
the code architecture, the runtime protocol between processes, and the
conventions to follow when changing anything.

## What this is

A self-hosted platform where multiple people each own one "project" (a single
Python file + KV store + secrets) and together shape one Discord bot. The
platform itself never restarts when projects change; each deployed project
runs in its own worker subprocess. Web console + REST API + discord gateway
live in one FastAPI/asyncio process.

Live facts as of this handoff: repo is public at
https://github.com/Richardn2002/discord-shared-bot. Deployed under a path
prefix (nginx `proxy_pass` strips it; app code is prefix-agnostic by using
relative URLs). Lang EN/中文.

## Repository map

```
app/
  config.py        # Config: config.json + DISCORD_TOKEN/ACCESS_TOKEN env overrides
  envfile.py       # .env parse/dump (shared by backend + workers)
  projects.py      # Project CRUD, metadata, draft/handler lifecycle, console log,
                   #   secrets_* helpers, HANDLER_TEMPLATE
  kvstore.py       # JSON-file KV store (atomic tmp+rename, thread-locked)
  template.py      # the HANDLER_TEMPLATE text prepopulated into new drafts
  bot.py           # discord.Client subclass (intents, event → payload_dict),
                   #   DiscordExecutor (turns action dicts into Discord API calls)
  worker_runner.py # subprocess that loads project code and executes handlers
  manager.py       # Worker pool: spawn/kill, dispatch+aggregation, tolerance,
                   #   run_test, query channel
  packages.py      # pip wrappers + transitive protection closure
  main.py          # FastAPI app: auth middleware, REST API, static templating
  static/
    index.html     # console shell (topbar + #view), placeholders injected server-side
    app.css        # all styling incl. responsive fixes (tables → .tablewrap scroll)
    app.js         # the SPA: hash router, views, makeDualEditor, actionCard
    site.js        # language switch, auth badge, bot-status badge (loaded before app.js)
    i18n.js        # window.I18N (landing + cheatsheet EN/中文） and window.CHEATSHEET
    landing.html   # public landing, same topbar, {{DOMAIN}}/{{SITE_TITLE}} tokens
    agent-guide.md # machine-readable contract for coding agents
files/             # user content; git init'd by the owner; .gitignore hides
                   #   console.log / kv_test.json / .env
config.json        # (gitignored) secrets + listen/port/domain/site_title/fonts/log_level
run.sh             # uvicorn entry, derives flags from config.json
```

## Runtime architecture (the important part)

**One process per thing:**
- **Core** (uvicorn + bot.py's discord client) — one asyncio loop owns
  everything: gateway events, REST API, worker supervision, aggregation.
- **Worker per deployed project** — `python -m app.worker_runner
  --project-dir D --file handler.py|draft.py --kv kv.json --slug S`,
  JSON-lines over stdin/stdout.

**Worker protocol** (all lines are JSON, UTF-8):
- Worker → core: `{"type":"ready"}` / `{"type":"init_error","traceback":...}`
  / `{"type":"log","line":...}` / `{"type":"result","id","actions","logs",
  "queries","error","duration"}` / `{"type":"query","qid","query","args"}`
- Core → worker: `{"type":"run","id","event","data","timeout",["test",
  "fake_message"]}` / `{"type":"query_result","qid","value"|"error"}`
  / `{"type":"shutdown"}`
- stderr is streamed into the project console as `[stderr]` lines.

**Handler framework (injected into the worker's module globals — never
imported by project code):** `send`, `reply`, `add_reaction`, `send_embed`,
`send_file`, `log`/`print` (captured), `kv_*`, `secret_get`, `get_message`.
See `worker_runner.py` for exact semantics. `reply` is literally `send` +
a `reply_to_message_id` marker, so aggregation treats them identically.

**Actions recorded by handlers** are dicts; the *core* (never the worker)
turns them into Discord API calls:
- `send` with `content` → aggregated text
- `send` with `embed` / `file` → sent individually (not aggregated)
- `react` → reaction
- No `reply` action kind exists anymore (merged into send marker).

**Event dispatch (manager.dispatch):** on an event, every *deployed and not
stopped* project runs the handler. Plain `send` texts are buffered into
`[nickname]:` sections (project creation order, 2000-char chunking via
`_chunk_blocks`) and flushed together; flush happens when all finish OR when
the smallest project's `tolerance` expires; stragglers ship late sections.
Then non-aggregatable actions execute. Executor executes: aggregator calls
`executor.send_text(channel_id, text, reply_ref)`; individual actions go via
`executor.execute(action)`. In tests the `RecordingExecutor` in main.py
records everything (see `POST /api/dev/simulate`).

**Queries (get_message):** worker emits `query` and blocks reading stdin;
core's reader task (always running) answers `query_result`. Handler timeout
still bounds the whole thing. In `test` mode the worker never queries the
core: it serves `fake_message` from the run request instead and records the
lookup in `queries`.

**Failure paths:** module import failure → `init_error`, worker stays
"broken" until deploy/restart. Handler exception → traceback captured,
`on_failure(event_name, event_data, error)` invoked in-process if defined.
Handler timeout → worker killed, restarted, `on_failure` re-run on the fresh
process with `_failure_of`/`_error` set; its actions still deliver via
`deliver_adhoc` (labelled).

## Conventions (please keep)

- **Plain text everywhere user-facing**: draft.py/handler.py/kv.json/.env/
  console.log under `files/projects/<slug>/`; nothing binary.
- **Relative URLs** in all frontend code (`fetch("api/...")`,
  `href="static/..."`, Monaco loader `static/monaco/vs`) so the app works
  under a path prefix. Never reintroduce absolute paths.
- **i18n**: UI copy via `data-i18n` keys in `site.js`-driven
  `applyI18n()` / `window.I18N`; cheatsheet via `window.CHEATSHEET`.
- **Responsive**: any new table goes inside `<div class="tablewrap">`; check
  at 375px before merging.
- **Docs for users**: update `template.py` header comment, `i18n.js`
  cheatsheet rows (both languages), `agent-guide.md`, and `landing.html`
  when you change handler-facing APIs or payloads.
- **Test tab keeps two rules**: every action renders fully (no hidden info —
  unknown action kinds fall back to JSON), and editors are lossless
  `makeDualEditor` instances.
- **.env & secrets**: never commit; `files/.gitignore` has the rule (and
  gitignore lines must NOT have trailing comments — that bit us once).

## Known limitations / deliberate trade-offs

- No cron/timers, no slash commands (v1 decision).
- Message-delete content is `None` when uncached (discord.py limitation).
- Workers never idle-sleep once awake (owner decision; opt-in
  `worker_idle_seconds` would be a future feature — preserves lazy-start).
- Embed actions are not aggregated (Discord limitation, by design).
- Package env is shared (no per-project isolation); protected closure marks
  platform deps + transitive deps uninstallable.
- Single shared access token; no per-user auth (trusted-friends deployment).

## How to verify your changes

```bash
./venv/bin/python -c "import ast,pathlib;[ast.parse(f.read_text()) for f in pathlib.Path('app').glob('*.py')]"
node --check app/static/app.js
# boot: (nohup ./run.sh &) then exercise REST with X-Token header from config.json
# browser: dev-browser --headless --timeout 120 run <script>.js (see past
# session scripts in /tmp/ui*.js pattern on this machine)
```
E2E checks worth running after touching dispatch/workers: create a temp
project, deploy, `POST /api/dev/simulate`, assert aggregated message +
queries list; test tab renders action cards; secrets roundtrip;
stop/start worker semantics; `GET /api/projects` shows expected runtime
state.
