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
  archive/         # local SQLite message archive + Nextcloud blob storage;
                   #   service.py runs startup/periodic sync and live gateway events;
                   #   backfill.py also supports manual full reconcile (force scan)
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
archive/           # (gitignored) archive.db, cache/, backups/; blobs live on Nextcloud
run.sh             # uvicorn entry, derives flags from config.json
```

## Runtime architecture (the important part)

**One process per thing:**
- **Core** (uvicorn + bot.py's discord client) — one asyncio loop owns
  everything: gateway events, REST API, worker supervision, aggregation, and
  the archive service. With `projects_enabled: false`, gateway events go to
  the archive only; project dispatch is skipped.
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

## Archive system (backend + web explorer + project queries)

The platform also maintains a local historical archive of guild messages. The
archive backend, live gateway integration, Nextcloud blob storage, backups, the
read-only web explorer and project-facing archive queries are implemented. See
the worker-query section below for the handler API.

Current archive coverage at the time of this handoff: ~58k messages, ~8k
attachments, ~14.1 GB of blobs on Nextcloud. Two channels the bot cannot view
(`device-portal`, `moderator-only`) are skipped. DMs are not archived.

### Files and data placement

```
app/archive/
  store.py          # SQLite schema + thread-safe read/write API (no network)
  discord_source.py # read-only Discord REST client (no gateway)
  nextcloud.py      # WebDAV client, content-addressed uploads, chunked upload,
                    #   download/read/delete
  ingest.py         # shared message/attachment ingestion, dedupe, blob upload,
                    #   edit/delete cleanup
  backfill.py       # discovery + incremental scan + optional full reconcile
  service.py        # live integration owned by the FastAPI lifespan
  explorer.py       # shared read-only result shaping + query facade for web/workers
tools/
  archive_backfill.py   # incremental or forced catch-up CLI
  archive_reconcile.py  # manual full reconcile CLI
  archive_status.py     # stats / data-quality CLI
  archive_backup.py     # consistent SQLite snapshot -> zstd -> Nextcloud
archive/              # gitignored local runtime data
  archive.db          # SQLite + FTS5 trigram index
  cache/              # optional local blob cache
  backups/            # local compressed snapshots (also uploaded)
```

Blob bytes are content-addressed on Nextcloud under
`<base_path>/attachments/<sha[:2]>/<sha[2:4]>/<sha256>/<original-filename>`.
The local DB stores metadata, message text, search text, attachment rows and
blob state (`pending|local|remote|missing|skipped`). The VPS should keep only
the SQLite DB, backups and a bounded cache; attachment bytes live remotely.
The web/API layer must retrieve blob bytes through the core, not expose the
Nextcloud app password or raw WebDAV paths to browsers/projects.

### Config

Top-level `projects_enabled` controls archive-only mode. When false, the bot
connects and archives but never dispatches events to project workers. This is
the safe way to run a second dev instance with a real token.

Relevant `archive` keys (see `config.example.json`): `enabled`, `dir`,
`database`, `cache_dir`, `cache_max_bytes`, `max_concurrent_downloads`,
`max_concurrent_jobs`, `sync_on_startup`, `startup_restore`,
`sync_interval_minutes`,
`download_attachments`, `download_direct_links`, `nextcloud` (`enabled`, `url`,
`username`, `app_password`, `base_path`, `verify_tls`, ...), and backup settings.
The running platform reads Nextcloud credentials from `config.json` only; the
repo-root `DISCORD_TOKEN` file is for standalone tools and is not read by the
bot. `.secrets/` is dev-only fallback for tools and does not need to be copied to
the VPS if `config.json` contains everything.

### Existing low-level API (`ArchiveStore`)

`ArchiveStore` is synchronous and thread-safe. It is owned by the core process.
Use it from FastAPI handlers, `ArchiveService`, or CLI tools. Do **not** open
SQLite directly from worker subprocesses; mediate project queries through the
core so there is one writer and consistent locking.

Useful read/query methods already present:

- `search(query, *, guild_id, channel_id, author_id, before, after, limit=50,
  cursor=None, include_bots=True) -> {"items": [...], "next_cursor": ...}`
  - FTS5 trigram for >=3-char terms; short terms fall back to substring scan.
  - Multi-term queries are ANDed. Cursor is `"created_ts:id"`.
- `stats() -> dict` (messages/channels/threads/users/attachments/blobs/bytes/
  oldest/newest).
- `get_attachment(attachment_id)`, `get_blob(sha256)`,
  `blob_path(sha256, filename)`, `blobs_for_sha(sha256)`.
- `backfill_rows()`, `get_backfill(channel_id)`.
- Write-side helpers (`upsert_message`, `delete_message`, etc.) are for ingestion;
  the web/project query layer should be read-only.

### Implemented lifecycle behavior

- Startup first checks Nextcloud `backups/` for a newer snapshot. Newer remote
  manifests are downloaded, decompressed, validated (`quick_check`, schema
  version), and atomically swapped in before `ArchiveStore` opens. The previous
  local DB is kept as `archive.db.pre-restore`. Controlled by
  `archive.startup_restore` (default true).
- Startup then runs an incremental catch-up scan, and live gateway
  create/edit/delete events are applied in order. Periodic incremental sync
  defaults to 60 min.
- `projects_enabled: false` suppresses project dispatch only; archiving continues.
- Edits compare `edited_ts`, so stale backfill cannot overwrite a newer edit.
- Deleted message IDs get tombstones so a later backfill cannot resurrect them.
- Attachment edits/deletes release blob refs and delete the remote Nextcloud
  object when the last reference is gone.
- Incremental sync only follows messages after each channel's cursor. It cannot
  see edits to ancient messages that happened while the bot was offline.
- `tools/archive_reconcile.py` does a full per-channel re-scan. It catches ancient
  edits and, with pruning enabled (default), removes messages Discord no longer
  returns. Inaccessible channels are skipped without pruning. This is manual
  because a full pass costs far more API calls.
- Whole deleted channels/threads are not discovered, so they are not reconciled.

### Archive web explorer (implemented)

The console has an `Archive` tab with a Discord-like channel/thread browser on
the left and a query/heatmap pane on the right. It is backed by the same
read-only `ArchiveStore` methods that project-facing queries also use.

Store read helpers added for the explorer (all synchronous, thread-safe):

- `query_messages(filter_ast, *, guild_id, channel_id, author_id, before, after,
  limit, cursor, order, include_bots) -> {items, next_cursor, total, ...}`
  - Nested `AND`/`OR`/`NOT` filter tree with whitelisted fields/operators.
  - Supports text, date, numeric, bool, reply, attachment and derived fields.
  - Attachment fields compile to `EXISTS (...)` so one message is not duplicated.
- `get_message(id, channel_id=None)`, `get_messages(ids)`,
  `message_context(message_id, before=20, after=20)`
- `attachments_for_messages(ids)` for batch message serialization.
- `guilds()`, `channels(guild_id)`, `users(guild_id, q, limit)`
- `heatmap(guild_id, channel_id, after, before, bucket_seconds,
  tz_offset_minutes)` returning per-bucket messages/authors/heat.

HTTP read endpoints (auth middleware applies; all read-only):

- `GET /api/archive/stats`
- `GET /api/archive/guilds`
- `GET /api/archive/channels?guild_id=...`
- `GET /api/archive/users?guild_id=...&q=...&limit=...`
- `GET /api/archive/messages?...`
- `POST /api/archive/query` with a JSON filter tree
- `GET /api/archive/messages/{message_id}?channel_id=...`
- `GET /api/archive/context?message_id=...&channel_id=...&before=20&after=20`
- `GET /api/archive/heatmap?guild_id=...&channel_id=...&after=...&before=...&bucket=hour|day&tz_offset=...`
- `GET /api/archive/attachments/{attachment_id}` streaming local/Nextcloud bytes

Message JSON serializes Discord snowflake IDs as strings (JavaScript precision).
The query engine lives in `store.py`; public shaping lives in
`app/archive/explorer.py` and is shared by `main.py` and the worker bridge.

### Project-facing archive queries (implemented)

Projects run in isolated subprocesses and cannot read SQLite/Nextcloud directly.
The worker `query` protocol now exposes read-only archive calls; core answers in
`Manager._answer_archive_query` using the same `app/archive/explorer.py` facade
and `ArchiveStore` methods as the web API.

Injected handler functions:

- `archive_search(query=None, guild_id=None, channel_id=None, author_id=None,
  after=None, before=None, limit=50, cursor=None, order="desc",
  include_bots=True)`
- `archive_query(filter=None, ...same filters...)`
- `archive_context(message_id, channel_id=None, before=20, after=20)`
- `archive_message(message_id, channel_id=None)`
- `archive_channels(guild_id=None)`, `archive_guilds()`
- `archive_stats(guild_id=None)`
- `archive_heatmap(guild_id=None, channel_id=None, after=None, before=None,
  bucket="hour", tz_offset=0, include_bots=True)`
- `archive_attachment_meta(attachment_id)` — metadata only, no bytes.

Project-facing results use integer Discord IDs; HTTP results use strings.
Archive queries intentionally query real archive data in test mode and are
recorded under `queries`. No mutating archive API is exposed to projects.

### What is NOT implemented yet

1. **Attachment bytes for projects** — projects can inspect archive attachment
   metadata but cannot read file bytes. Add a bounded `archive_read_attachment`
   only if needed; decide between base64 over the JSON line protocol or a
   core-materialized temp file. Never let workers touch Nextcloud credentials.
2. **Attachment Range requests** — the web stream endpoint currently sends whole
   blobs; add HTTP Range support if large-video seeking is needed.
3. **Deterministic archive tests** — test mode intentionally hits the real
   archive. Add an explicit empty/temp-DB option if deterministic tests become
   necessary.

### Recommended implementation order

1. Optional follow-ups: HTTP Range for attachment streaming, project attachment
   bytes, and per-project archive scoping in the worker bridge.
2. Add remaining regression tests: worker archive-query roundtrip (exists as a
   manual smoke test now), deleted messages absent, attachment missing-blob case.

### Archive-specific gotchas

- Do not run the standalone CLI backfill/reconcile as a second writer unless
  intended; WAL/busy-timeout makes it safe but it can contend with the live
  service. Reconcile is manual for that reason.
- Nextcloud blob retrieval should go through the core/WebDAV client. Browsers
  should never receive the app password or raw WebDAV URL.
- Search uses FTS5 trigram; 2-character Chinese queries fall back to LIKE. Keep
  that behavior unless you add a better tokenizer/index.
- Privacy: deleted messages are removed from the DB and blobs when refcount drops.
  Backups may retain them until rotated out.
- Reconcile pruning relies on a complete successful scan. Do not prune after an
  inaccessible/partial scan. The current guard uses `first_seen`/`updated_ts`
  around scan start to avoid deleting concurrent live inserts.
- The archive is a local index plus remote blobs; copying only `config.json` and
  `archive/archive.db` to the VPS is enough to connect to the same Nextcloud data
  because blob paths are content-addressed and stored in the DB.

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
- Project-facing archive queries are read-only and implemented through the
  worker bridge; project attachment bytes and HTTP Range are still TODO.
- Archive incremental sync cannot recover ancient edits or deletes that happened
  while the bot was offline; use the manual reconcile CLI.
- Whole deleted channels/threads are not discoverable, so reconcile does not
  prune them.

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
