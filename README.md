# Discord Shared Bot Platform

A self-hosted platform where friends write Discord bot features in the browser,
one Python file per "project", with per-project KV stores. The main bot process
never restarts; each deployed project runs in its own worker process.

## Setup

```bash
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
cp config.example.json config.json   # then edit it
./run.sh
```

`config.json`:

| key         | meaning                                                   |
|-------------|-----------------------------------------------------------|
| token       | Discord bot token (or set env `DISCORD_TOKEN`)            |
| access_token| shared password for the web UI (env `ACCESS_TOKEN`). Share it with friends; `null` = no auth (dev only!) |
| listen      | web listen address (`127.0.0.1` behind a reverse proxy)   |
| port        | web listen port, e.g. `8000`                              |
| domain      | public base URL **including any path prefix**, e.g. `https://example.com/bot` (used on the landing page and for the cookie's Secure flag) |
| files_dir   | where projects live (`files`) — fine dir to `git init`    |
| site_title  | text in the top bar / page titles / landing headline      |
| font_ui     | CSS font stack for the interface text                     |
| font_code   | CSS font stack for code (editor, consoles)                |
| log_level   | `DEBUG` / `INFO` / `WARNING` / `ERROR`                    |
| projects_enabled | `false` runs archive-only mode: bot connects and archives, but never dispatches events to project workers (env `PROJECTS_ENABLED=0` also works) |
| archive     | local SQLite archive + Nextcloud blob storage; see `config.example.json` |

The running platform reads its Discord token from `config.json` (or the
`DISCORD_TOKEN` environment variable). The repo-root `DISCORD_TOKEN` file is
reserved for standalone tooling and is not read by the bot.

### Deploying under a path prefix

All frontend URLs are relative, so the app works at `/` or under a sub-path
like `/bot/` with no extra settings — just proxy with prefix stripping
(`location /bot/ { proxy_pass http://127.0.0.1:8000/; }`) and set `domain`
to the full public URL including the prefix.

### Access control

Set `access_token` to a long random string and hand it to your friends. Every
`/api/*` call then requires it; humans log in once via the console's lock screen
(30-day cookie), scripts/agents send header `X-Token: <token>` (or `?token=`).
If you expose the port to the internet, put the app behind an HTTPS reverse
proxy (Caddy/nginx) — the cookie is required over plain HTTP until then.

### Pages

* `/` — public landing page for your friends (what/why/how + the agent one-liner)
* `/app` — the console (Projects / Packages), protected by the access token
* `/static/agent-guide.md` — the machine-readable contract for coding agents;
  with it an agent can develop, **test**, and debug projects entirely over HTTP

### Creating the Discord application

1. https://discord.com/developers/applications → New Application → Bot → copy token.
2. Under **Bot → Privileged Gateway Intents** enable **Message Content Intent**
   (required for `on_message*` content). No other privileged intent is used.
3. Invite with scopes `bot` and permissions at least:
   Send Messages, Read Message History, Add Reactions, Embed Links, Attach Files.

Without a token the web UI still works fully (create/edit/test/deploy projects,
simulate events) — events just don't come from Discord and actions are dropped.

## Concepts

* **Project**: one Python file (`handler.py` = live, `draft.py` = edited),
  one KV store (`kv.json`), a test KV copy (`kv_test.json`), and a console
  (`console.log`). All plain text under `files/projects/<slug>/` — ideal for
  `git init` + commits.
* **Deploy**: copies draft → handler, restarts that project's worker. The bot
  and other projects never blink.
* **Handlers**: `on_message`, `on_message_edit`, `on_message_delete`,
  `on_reaction_add`, `on_reaction_remove`, plus `on_failure`. Bot-authored
  messages are never dispatched (no feedback loops).
* **Framework functions**: `send`, `reply`, `add_reaction`, `send_embed`,
  `send_file`, `log`/`print`, `kv_get`, `kv_set`, `kv_delete`, `kv_keys`,
  `kv_all`, plus `get_message(message_id, channel_id=None)` (fetch any message
  the bot can see; `None` on failure) and `secret_get(key, default=None)`
  (read the project's gitignored `.env` secret store — for API keys).
  See the header comment of any new project file.
* **Aggregation**: when an event fires, all deployed projects run it; their
  plain `send()` outputs are merged into one message (in project-creation
  order) as `[nickname]:` sections. Replies/reactions/embeds/files go out
  individually.
* **Metadata**: `timeout` (max handler seconds; the worker is killed after
  this → deadlocks become `[console]` errors + an `on_failure` call) and
  `tolerance` (aggregated messages are sent no later than this; stragglers
  go out as follow-up messages).
* **Test**: the Test tab py-compiles your draft, copies real KV → test KV,
  fires a fake event at `draft.py`, and shows actions/logs/errors. Nothing
  touches Discord.
* **on_failure(event_name, event_data, error)**: called when a handler raises
  or times out. If it also fails, the failure is logged to the console and
  that's it.
* **Secrets**: each project may keep a `.env` file of API keys, edited under
  the KV tab and read in code via `secret_get(key)`. It's listed in
  `files/.gitignore` so shared git history stays secret-free.
* **Packages**: the Packages page wraps `pip install/uninstall` into the shared
  venv. New workers (deploy/restart) pick them up.

## Archive mode

The platform can run as a pure archive instance. Set `projects_enabled` to
`false` (or `PROJECTS_ENABLED=0`) when testing with a real bot token on a
second machine; the bot still receives gateway events and maintains the
archive, but no project handlers run and no project messages are sent.

Archive data lives under `archive/` (gitignored): SQLite metadata/search index
locally, attachment bytes on Nextcloud via WebDAV. On startup the archive
service runs an incremental catch-up scan, then applies live create/edit/delete
events. Periodic catch-up defaults to every 60 minutes
(`archive.sync_interval_minutes`). Use `tools/archive_backfill.py`,
`tools/archive_status.py`, and `tools/archive_backup.py` for manual work.

The console's **Archive** tab browses channels/threads and messages, supports a
nested query builder over all message fields (AND/OR/NOT), and shows an hourly /
daily activity heatmap with clickable hot moments. Attachments stream through the
core, so Nextcloud credentials never reach the browser. Read-only endpoints live
under `/api/archive/*`.

Projects can query the same archive from handlers through injected functions:
`archive_search(...)`, `archive_query(filter, ...)`, `archive_context(...)`,
`archive_message(...)`, `archive_channels(...)`, `archive_guilds()`,
`archive_stats(...)`, `archive_heatmap(...)` and `archive_attachment_meta(...)`.
See the project cheatsheet or `static/agent-guide.md` for fields/operators.

Incremental sync only follows messages after each channel's stored cursor. It
cannot see edits to older messages that happened while the bot was offline. For
that, run a manual full reconciliation:

```bash
# one channel or thread
./venv/bin/python tools/archive_reconcile.py --only-channel <id>

# one guild, or everything
./venv/bin/python tools/archive_reconcile.py --guild <guild_id>
./venv/bin/python tools/archive_reconcile.py --all

# detect missing messages without deleting them
./venv/bin/python tools/archive_reconcile.py --only-channel <id> --no-prune
```

A reconcile pass re-scans from the beginning of each selected channel/thread,
so it catches ancient edits. With pruning enabled (the default), archived
messages that Discord no longer returns are treated as missed deletes and
removed, including their Nextcloud blobs. Inaccessible channels are skipped
without pruning. This is deliberately manual because a full pass costs far more
API calls than the hourly incremental sync.

## Dev endpoints

* `POST /api/dev/simulate` `{event, data}` — runs the real dispatch + aggregation
  pipeline but records instead of sending (works tokenless).
