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

## Dev endpoints

* `POST /api/dev/simulate` `{event, data}` — runs the real dispatch + aggregation
  pipeline but records instead of sending (works tokenless).
