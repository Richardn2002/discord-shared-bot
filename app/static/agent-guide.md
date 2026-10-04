# Agent Guide — Discord Shared Bot platform

You are helping develop a feature ("project") for a shared Discord bot.
Everything a human can do in the web console, you can do over plain HTTP:
create/edit code, run tests with fake Discord events, read logs, deploy.

All paths below are relative to the base URL your user gave you.
If an `ACCESS TOKEN` was provided, send it on **every** request either as
header `X-Token: <token>` or query param `?token=<token>`.
The web page at `/` explains the platform for humans. The console at `/app`
is not for you — prefer this API.

---

## 1. The mental model

- A **project** = one Python file + one persistent JSON key-value store.
- You never touch Discord libraries. You write **handler functions**; the
  platform calls them when matching things happen on Discord.
- Inside handlers you call **framework functions** (`send(...)`, `kv_get(...)`,
  …). They are injected into the global namespace — **do not import anything
  for them**, just call them.
- Code runs in its own process. `print()` and `log()` go to the project's
  console, readable over HTTP.
- When several projects react to the same event, their `send()` outputs are
  combined into one Discord message, each project getting a
  `[project nickname]:` section. So just write plain text; formatting is done
  for you.
- Limits per project: `timeout` = max seconds a handler may run (typ. 5 s;
  on expiry the worker is killed and restarted), `tolerance` = how long the
  combined message may be delayed waiting for slow projects.
- If a handler raises (or times out), `on_failure(event_name, event_data,
  error)` is called if you define it. Keep it simple — if it also fails, the
  failure is only logged.

## 2. Events you can handle (functions in your project file)

```python
def on_message(message): ...
def on_message_edit(message): ...      # same dict, plus message["old_content"] (may be None)
def on_message_delete(message): ...    # "content"/"author" may be None if uncached
def on_reaction_add(reaction): ...
def on_reaction_remove(reaction): ...
def on_failure(event_name, event_data, error): ...
```

Message dict:
```json
{"id": 1001, "content": "text", "old_content": null,
 "author": {"id": 42, "name": "alice", "display_name": "Alice"},
 "channel_id": 555, "channel_name": "general",
 "guild_id": 999, "guild_name": "Our Server", "attachments": [],
 "reply_to": {"message_id": 555, "channel_id": 555},
 "mentions": [{"id": 42, "name": "alice", "display_name": "Alice"}],
 "mention_everyone": false, "pinned": false,
 "created_at": "2026-01-01T12:00:00+00:00", "edited_at": null,
 "jump_url": "https://discord.com/channels/999/555/1001"}
```
`reply_to` is present only when the message is a reply (null otherwise).
Fetch the replied-to message's content with
`get_message(message["reply_to"]["message_id"])`.
Reaction dict:
```json
{"emoji": "👍", "message_id": 1001, "channel_id": 555, "channel_name": "general",
 "user": {"id": 42, "name": "alice", "display_name": "Alice"},
 "message_author": {"id": 7, "name": "bob", "display_name": "Bob"},
 "guild_id": 999, "guild_name": "Our Server"}
```
Messages from bots (including this bot itself) are never dispatched —
no loops. Custom emojis arrive as `"<:name:id>"`.

## 3. Framework functions (globals, no import)

| call | effect |
|---|---|
| `send(text, channel_id=None)` | queue a message (default: the channel the event happened in). Plain text sends are aggregated across projects. |
| `reply(text)` | reply to the triggering message (message events only) |
| `add_reaction(emoji, message_id=None)` | react (default: the event's message) |
| `send_embed(title=None, description=None, color=0x5865F2, fields=None, channel_id=None)` | rich embed; `fields=[{"name":…,"value":…,"inline":True}]` (max 25) |
| `send_file(filename, content, channel_id=None)` | attach a file; `content` is `str` or `bytes` |
| `log(msg)` / `print(...)` | write to the project console |
| `kv_get(key, default=None)` / `kv_set(key, value)` / `kv_delete(key)` / `kv_keys()` / `kv_all()` | persistent per-project JSON store; **values must be JSON-serializable** |
| `get_message(message_id, channel_id=None)` | fetch any message the bot can see (channel defaults to the event's). Returns the message dict above, or **None** on any failure (not found / no access / bot offline — also logged). Lookups appear under `queries` in results. **In test mode it never touches Discord**: pass `fake_message` with the test call — an id matching it returns it, any other id returns None. |
| `secret_get(key, default=None)` | read the project's `.env` secret store (read-only from code). Strings. File is gitignored by convention. |

Rules of thumb:
- Handlers must finish quickly (see `timeout`); never busy-wait.
- Use the KV store for anything that must survive deploys/restarts.
- Stdlib + server-installed packages are importable (`pip` packages can be
  listed/installed via the API below; they reach your code on next deploy).

## 4. HTTP API (curl examples; add `X-Token` header when a token is set)

All JSON. `<BASE>` = the platform URL, e.g. `https://bots.example.com`.

### Projects
```bash
curl $BASE/api/projects                                   # list (+ worker state)
curl -X POST $BASE/api/projects -d '{"slug":"my-bot","nickname":"My Bot","author":"you"}'
curl $BASE/api/projects/me                                # one project
curl -X PATCH $BASE/api/projects/me -d '{"timeout":3,"tolerance":8,"nickname":"Me v2"}'
```

### Code workflow — THIS is how you develop
```bash
# read current draft (or the live one with which=active)
curl "$BASE/api/projects/me/code?which=draft"

# write a new draft
curl -X PUT $BASE/api/projects/me/code -d '{"code": "def on_message(m):\n    if m[\"content\"] == \"!hi\":\n        send(\"hi!\")\n"}'

# py-compile the draft (fast syntax check, line numbers in errors)
curl -X POST $BASE/api/projects/me/compile

# TEST: run the DRAFT against a fake event. The project's KV is cloned into a
# test store before the run, so tests can't corrupt real data. Nothing is
# sent to Discord. Returns actions/logs/error/duration.
curl -X POST $BASE/api/projects/me/test -d '{"event":"on_message","data":{"id":1,
  "content":"!hi","author":{"id":42,"name":"t","display_name":"T"},
  "channel_id":555,"channel_name":"general","guild_id":9,"guild_name":"G","attachments":[]}}'

# if your code uses get_message(): pass "fake_message" — a message dict that
# get_message() will return when asked for its id (any other id → None).
# In test mode get_message NEVER touches Discord.

# DEPLOY: draft becomes live (compile-checked first), worker restarts.
# Response includes the worker state — "broken" + init_error means your file
# fails at import time.
curl -X POST $BASE/api/projects/me/deploy

# console: last N lines (log(), print(), errors, lifecycle)
curl "$BASE/api/projects/me/console?tail=100"
```

### Test/debug like a user of the web console, part 2
```bash
# fire an event through the REAL dispatch pipeline of all deployed projects,
# but record instead of sending to Discord (aggregation, ordering, tolerance):
curl -X POST $BASE/api/dev/simulate -d '{"event":"on_message","data":{"id":2,
  "content":"!party","author":{"id":42,"name":"t","display_name":"T"},
  "channel_id":555,"channel_name":"general"}}'
# → see "recorded_messages" (the exact Discord messages) + "recorded_actions"
#   (replies/reactions/embeds/files) + per-project results/errors.

# restart the worker of your project (reload code, pick up new packages)
curl -X POST $BASE/api/projects/me/restart
```

### KV store (both a `real` and a `test` store exist; `which=real|test`)
```bash
curl "$BASE/api/projects/me/kv"                           # dump whole store
curl "$BASE/api/projects/me/kv?which=test"
curl -X PUT "$BASE/api/projects/me/kv" -d '{"score": 123}'            # replace all
curl -X PUT "$BASE/api/projects/me/kv/score" -d '{"value": 124}'      # set one key
curl -X DELETE "$BASE/api/projects/me/kv/score"
# export file:  curl -OJ "$BASE/api/projects/me/kv?download=1"
```

### Secrets (one .env store per project; no real/test split)
```bash
curl $BASE/api/projects/me/secrets                        # whole store as JSON
curl -X PUT $BASE/api/projects/me/secrets -d '{"OPENAI_KEY": "sk-..."}'   # replace all
curl -X PUT $BASE/api/projects/me/secrets/OPENAI_KEY -d '{"value": "sk-..."}'
curl -X DELETE $BASE/api/projects/me/secrets/OPENAI_KEY
```
Keys must match `[A-Za-z_][A-Za-z0-9_]*`, values are plain strings. Changes
apply immediately (read from disk on every `secret_get` call). The .env file
is listed in files/.gitignore so it won't end up in the shared git repo.

### Packages
```bash
curl $BASE/api/packages                                   # installed (name, version)
curl -X POST $BASE/api/packages/install -d '{"spec":"cowsay"}'       # returns pip output
```

## 5. Recommended workflow (what a great agent does)

1. `GET /api/projects` (or ask the user which slug) →
   `GET .../code?which=draft` to see what exists.
2. Edit the **draft only**. Never edit deployed code directly.
3. `POST .../compile` → fix until `{"ok": true}`.
4. `POST .../test` with **realistic fake events** — several cases, including
   inputs that should *not* trigger. Check `actions` are exactly what the user
   asked for, `queries` show the lookups you expect, and `logs` show no
   surprises. Note: without a Discord token, `get_message()` returns None —
   code should tolerate that. The fake-event payload templates the web UI uses
   live in the guide's section 2 example dicts; include the new fields
   (`reply_to`, `mentions`, `mention_everyone`, `pinned`, `created_at`,
   `edited_at`, `jump_url`) for realism.
5. If the feature needs state: check `GET .../kv`, then use `kv_*` in code.
6. Iterate: also test error paths (`on_failure`) and slow paths (stay well
   under `timeout` seconds).
7. `POST .../deploy` → confirm `worker.state` is `"running"`.
8. Confidence check: `POST /api/dev/simulate` with a final fake event and
   verify the recorded messages; peek at `GET .../console`.
9. Report to the user: what it does, what commands it adds, example output.

## 6. Etiquette (shared bot!)

- Only touch the project you were asked about. Other slugs belong to others.
- Keep the draft compiling at all times; deploy deliberately.
- Prefer small, quiet features: respond to explicit commands/keywords rather
  than every message, unless asked.
- Do not spam `send()`: one or two messages per event max.
