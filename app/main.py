"""FastAPI app: REST API for projects / KV / packages, static frontend, and
the always-on discord bot + worker manager living in the same event loop."""
from __future__ import annotations

import asyncio
import hmac
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

from fastapi import Body, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import bot, packages, projects
from .archive import explorer as archive_explorer
from .archive.service import archive_service
from .archive.store import ArchiveQueryError
from .config import APP_DIR, CONFIG
from .kvstore import KVStore
from .manager import Manager

manager = Manager()

logging.basicConfig(level=getattr(logging, CONFIG.log_level, logging.INFO),
                    format="[%(levelname)s] %(name)s: %(message)s", force=True)


def _project_or_404(slug: str) -> projects.Project:
    try:
        return projects.get(slug)
    except projects.ProjectError as e:
        raise HTTPException(404, str(e))


@asynccontextmanager
async def lifespan(app: FastAPI):
    await archive_service.start()
    await bot.start_bot(manager, CONFIG.token)
    yield
    await bot.stop_bot()
    await archive_service.stop()
    await manager.shutdown()


app = FastAPI(title="Discord Shared Bot Platform", lifespan=lifespan)


# ---------------------------------------------------------------------------
# access token auth (single shared token; HttpOnly cookie for the web UI,
# X-Token header / ?token= query param for scripting and downloads)
# ---------------------------------------------------------------------------

AUTH_COOKIE = "sb_auth"
COOKIE_MAX_AGE = 30 * 24 * 3600  # 30 days


def _token_ok(candidate: str | None) -> bool:
    if not CONFIG.access_token:
        return True
    return bool(candidate) and hmac.compare_digest(str(candidate), CONFIG.access_token)


def _candidate_from(request: Request) -> str | None:
    return (request.cookies.get(AUTH_COOKIE)
            or request.headers.get("x-token")
            or request.query_params.get("token"))


@app.middleware("http")
async def auth_guard(request: Request, call_next):
    path = request.url.path
    if (CONFIG.access_token
            and path.startswith("/api")
            and not path.startswith("/api/auth/")
            and not _token_ok(_candidate_from(request))):
        return JSONResponse({"detail": "unauthorized"}, status_code=401)
    resp = await call_next(request)
    # always revalidate our own html/js/css (monaco assets stay cacheable)
    if path == "/" or (path.startswith("/static/") and "/monaco/" not in path):
        resp.headers["Cache-Control"] = "no-cache"
    return resp


@app.get("/api/auth/check")
async def auth_check(request: Request):
    if not CONFIG.access_token:
        return {"required": False, "ok": True}
    return {"required": True, "ok": _token_ok(_candidate_from(request))}


@app.post("/api/auth/login")
async def auth_login(payload: dict = Body(...), response: Response = None):
    if not CONFIG.access_token:
        return {"ok": True, "required": False}
    token = str(payload.get("token") or "")
    if not _token_ok(token):
        raise HTTPException(401, "wrong token")
    response.set_cookie(AUTH_COOKIE, token, max_age=COOKIE_MAX_AGE,
                        httponly=True, samesite="lax",
                        secure=CONFIG.domain.startswith("https"))
    return {"ok": True, "required": True}


@app.post("/api/auth/logout")
async def auth_logout(response: Response):
    response.delete_cookie(AUTH_COOKIE)
    return {"ok": True}


# ---------------------------------------------------------------------------
# system
# ---------------------------------------------------------------------------

@app.get("/api/status")
async def get_status():
    return {
        "bot": bot.STATUS,
        "bot_configured": bool(CONFIG.token),
        "domain": CONFIG.domain,
        "projects_enabled": CONFIG.projects_enabled,
        "projects": len(projects.list_all()),
        "deployed": len([p for p in projects.list_all() if p.deployed]),
        "archive": archive_service.status(),
    }


# ---------------------------------------------------------------------------
# archive (read-only explorer API)
# ---------------------------------------------------------------------------

def _archive_store():
    store = archive_service.store
    if store is None:
        raise HTTPException(503, "archive is not enabled or not running")
    return store


def _serialize_messages(rows: list[dict], store) -> list[dict]:
    return archive_explorer.serialize_messages(store, rows)


def _run_query(store, body: dict) -> dict:
    try:
        return archive_explorer.query_messages(store, body)
    except ArchiveQueryError as e:
        raise HTTPException(400, str(e))


@app.get("/api/archive/stats")
async def archive_stats(guild_id: str | None = Query(None)):
    store = _archive_store()
    return await asyncio.to_thread(archive_explorer.stats, store, guild_id)


@app.get("/api/archive/guilds")
async def archive_guilds():
    store = _archive_store()
    return await asyncio.to_thread(archive_explorer.guilds, store)


@app.get("/api/archive/channels")
async def archive_channels(guild_id: str | None = Query(None)):
    store = _archive_store()
    return await asyncio.to_thread(archive_explorer.channels, store, guild_id)


@app.get("/api/archive/users")
async def archive_users(guild_id: str | None = Query(None),
                        q: str | None = Query(None),
                        limit: int = Query(50, ge=1, le=200)):
    store = _archive_store()
    return await asyncio.to_thread(archive_explorer.users, store, guild_id, q, limit)


@app.get("/api/archive/messages")
async def archive_messages(
        guild_id: str | None = Query(None), channel_id: str | None = Query(None),
        author_id: str | None = Query(None), before: float | None = Query(None),
        after: float | None = Query(None), limit: int = Query(50, ge=1, le=200),
        cursor: str | None = Query(None), order: str = Query("desc", pattern="^(asc|desc)$"),
        include_bots: bool = Query(True)):
    store = _archive_store()
    return await asyncio.to_thread(_run_query, store, {
        "guild_id": guild_id, "channel_id": channel_id, "author_id": author_id,
        "before": before, "after": after, "limit": limit, "cursor": cursor,
        "order": order, "include_bots": include_bots,
    })


@app.post("/api/archive/query")
async def archive_query(payload: dict = Body(...)):
    store = _archive_store()
    body = dict(payload or {})
    body["limit"] = max(1, min(int(body.get("limit", 50)), 200))
    if str(body.get("order", "desc")).lower() not in ("asc", "desc"):
        raise HTTPException(400, "order must be asc or desc")
    return await asyncio.to_thread(_run_query, store, body)


@app.get("/api/archive/messages/{message_id}")
async def archive_message(message_id: str, channel_id: str | None = Query(None)):
    store = _archive_store()
    row = await asyncio.to_thread(store.get_message, message_id, channel_id)
    if not row:
        raise HTTPException(404, "message not found")
    return (await asyncio.to_thread(_serialize_messages, [row], store))[0]


@app.get("/api/archive/context")
async def archive_context(message_id: str = Query(...),
                          channel_id: str | None = Query(None),
                          before: int = Query(20, ge=0, le=100),
                          after: int = Query(20, ge=0, le=100)):
    store = _archive_store()
    result = await asyncio.to_thread(
        store.message_context, message_id, channel_id=channel_id,
        before=before, after=after)
    if not result:
        raise HTTPException(404, "message not found")
    result["items"] = _serialize_messages(result["items"], store)
    return result


@app.get("/api/archive/heatmap")
async def archive_heatmap(
        guild_id: str | None = Query(None), channel_id: str | None = Query(None),
        after: float | None = Query(None), before: float | None = Query(None),
        bucket: str = Query("hour", pattern="^(hour|day)$"),
        tz_offset: int = Query(0, ge=-840, le=840),
        include_bots: bool = Query(True)):
    store = _archive_store()
    seconds = 3600 if bucket == "hour" else 86400
    return await asyncio.to_thread(
        store.heatmap, guild_id=guild_id, channel_id=channel_id,
        after=after, before=before, bucket_seconds=seconds,
        tz_offset_minutes=tz_offset, include_bots=include_bots)


@app.get("/api/archive/attachments/{attachment_id}")
async def archive_attachment(attachment_id: str, download: bool = False):
    store = _archive_store()
    row = await asyncio.to_thread(store.get_attachment, attachment_id)
    if not row:
        raise HTTPException(404, "attachment not found")
    sha = row["sha256"]
    if not sha:
        raise HTTPException(404, "attachment bytes are not archived")
    blob = await asyncio.to_thread(store.get_blob, sha)
    filename = row["filename"] or (blob["filename"] if blob else None) or "file"
    ctype = row["content_type"] or (blob["content_type"] if blob else None) \
        or "application/octet-stream"
    disposition = "attachment" if download else "inline"
    headers = {
        "Content-Disposition": f"{disposition}; filename*=UTF-8''{quote(filename, safe='')}",
        "Cache-Control": "private, max-age=3600",
    }
    candidates = []
    if blob and blob["local_path"]:
        candidates.append(Path(blob["local_path"]))
    candidates.append(CONFIG.archive_dir / "cache" / "blobs" /
                      sha[:2] / sha[2:4] / sha)
    for path in candidates:
        if path.exists() and path.is_file():
            return FileResponse(path, media_type=ctype, headers=headers)
    remote = blob["remote_path"] if blob else None
    if remote and archive_service.nextcloud:
        return StreamingResponse(
            archive_service.nextcloud.iter_bytes(remote),
            media_type=ctype, headers=headers)
    raise HTTPException(404, "attachment blob is unavailable")


# ---------------------------------------------------------------------------
# projects
# ---------------------------------------------------------------------------

@app.get("/api/projects")
async def list_projects():
    return [p.public_info(runtime=manager.status(p.slug))
            for p in projects.list_all()]


@app.post("/api/projects")
async def create_project(payload: dict = Body(...)):
    try:
        p = projects.create(payload.get("slug", ""),
                            payload.get("nickname", ""),
                            payload.get("author", ""))
        return p.public_info()
    except projects.ProjectError as e:
        raise HTTPException(400, str(e))


@app.get("/api/projects/{slug}")
async def get_project(slug: str):
    p = _project_or_404(slug)
    return p.public_info(runtime=manager.status(slug))


@app.patch("/api/projects/{slug}")
async def patch_project(slug: str, payload: dict = Body(...)):
    p = _project_or_404(slug)
    try:
        projects.update_meta(p, payload)
    except projects.ProjectError as e:
        raise HTTPException(400, str(e))
    return p.public_info(runtime=manager.status(slug))


@app.delete("/api/projects/{slug}")
async def delete_project(slug: str):
    p = _project_or_404(slug)
    await manager.remove(slug)
    projects.delete(p)
    return {"ok": True}


# -- code -------------------------------------------------------------------

@app.get("/api/projects/{slug}/code")
async def get_code(slug: str, which: str = Query("draft", pattern="^(draft|active)$")):
    p = _project_or_404(slug)
    return {"which": which, "code": projects.read_code(p, which)}


@app.put("/api/projects/{slug}/code")
async def put_code(slug: str, payload: dict = Body(...)):
    p = _project_or_404(slug)
    projects.write_draft(p, payload.get("code", ""))
    return {"ok": True}


@app.post("/api/projects/{slug}/compile")
async def compile_project(slug: str):
    p = _project_or_404(slug)
    return projects.compile_check(p)


@app.post("/api/projects/{slug}/deploy")
async def deploy_project(slug: str):
    p = _project_or_404(slug)
    try:
        projects.deploy(p)
    except projects.ProjectError as e:
        raise HTTPException(400, str(e))
    status = await manager.restart(slug)   # restarts worker, surfaces init errors
    projects.console_append(p, f"deployed (worker state: {status.get('state')})")
    return {"ok": True, "deployed_at": p.meta.get("deployed_at"), "worker": status}


@app.post("/api/projects/{slug}/restart")
async def restart_project(slug: str):
    _project_or_404(slug)
    return await manager.restart(slug)


@app.post("/api/projects/{slug}/worker")
async def worker_control(slug: str, payload: dict = Body(...)):
    _project_or_404(slug)
    action = payload.get("action")
    try:
        if action == "start":
            return await manager.set_running(slug, True)
        if action == "stop":
            return await manager.set_running(slug, False)
        if action == "restart":
            return await manager.restart(slug)
    except projects.ProjectError as e:
        raise HTTPException(400, str(e))
    raise HTTPException(400, "action must be start/stop/restart")


@app.post("/api/projects/{slug}/test")
async def test_project(slug: str, payload: dict = Body(...)):
    p = _project_or_404(slug)
    check = projects.compile_check(p)
    if not check["ok"]:
        return {"ok": False, "phase": "compile", "error": check["error"]}
    event = payload.get("event", "on_message")
    data = payload.get("data") or {}
    fake = payload.get("fake_message")
    if fake is not None and not isinstance(fake, dict):
        raise HTTPException(400, "fake_message must be an object or null")
    res = await manager.run_test(p, event, data, fake_message=fake)
    return {"ok": res.get("error") is None, "phase": "run", **res}


@app.get("/api/projects/{slug}/console")
async def get_console(slug: str, tail: int = Query(200, ge=1, le=2000)):
    p = _project_or_404(slug)
    path = p.console_path
    if not path.exists():
        return {"lines": []}
    lines = path.read_text(errors="replace").splitlines()
    return {"lines": lines[-tail:]}


# -- kv ---------------------------------------------------------------------

def _kv_for(slug: str, which: str) -> KVStore:
    p = _project_or_404(slug)
    path = p.kv_path if which == "real" else p.kv_test_path
    return KVStore(path)


@app.get("/api/projects/{slug}/kv")
async def kv_get_all(slug: str, which: str = Query("real", pattern="^(real|test)$"),
                     download: bool = False):
    store = _kv_for(slug, which)
    data = store.items()
    if download:
        return Response(
            json.dumps(data, indent=2, ensure_ascii=False),
            media_type="application/json",
            headers={"Content-Disposition":
                     f'attachment; filename="{slug}-kv-{which}.json"'},
        )
    return data


@app.put("/api/projects/{slug}/kv")
async def kv_replace(slug: str, payload: dict = Body(...),
                     which: str = Query("real", pattern="^(real|test)$")):
    store = _kv_for(slug, which)
    try:
        store.replace_all(payload)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "keys": len(payload)}


@app.put("/api/projects/{slug}/kv/{key}")
async def kv_set_key(slug: str, key: str, payload: dict = Body(...),
                     which: str = Query("real", pattern="^(real|test)$")):
    store = _kv_for(slug, which)
    try:
        store.set(key, payload.get("value"))
    except (TypeError, ValueError) as e:
        raise HTTPException(400, f"value must be JSON-serializable: {e}")
    return {"ok": True}


@app.delete("/api/projects/{slug}/kv/{key}")
async def kv_delete_key(slug: str, key: str,
                        which: str = Query("real", pattern="^(real|test)$")):
    store = _kv_for(slug, which)
    return {"ok": store.delete(key)}


# -- secrets (.env store) -----------------------------------------------------

@app.get("/api/projects/{slug}/secrets")
async def secrets_list(slug: str):
    return projects.secrets_read(_project_or_404(slug))


@app.put("/api/projects/{slug}/secrets")
async def secrets_replace(slug: str, payload: dict = Body(...)):
    p = _project_or_404(slug)
    try:
        projects.secrets_write(p, payload)
    except projects.ProjectError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "keys": len(payload)}


@app.put("/api/projects/{slug}/secrets/{key}")
async def secrets_set(slug: str, key: str, payload: dict = Body(...)):
    p = _project_or_404(slug)
    try:
        projects.secrets_set(p, key, payload.get("value"))
    except projects.ProjectError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@app.delete("/api/projects/{slug}/secrets/{key}")
async def secrets_delete(slug: str, key: str):
    return {"ok": projects.secrets_delete(_project_or_404(slug), key)}


# ---------------------------------------------------------------------------
# packages
# ---------------------------------------------------------------------------

@app.get("/api/packages")
async def pkg_list():
    return await packages.list_packages()


@app.post("/api/packages/install")
async def pkg_install(payload: dict = Body(...)):
    try:
        return await packages.install(payload.get("spec", ""))
    except packages.PackageError as e:
        raise HTTPException(400, str(e))


@app.post("/api/packages/uninstall")
async def pkg_uninstall(payload: dict = Body(...)):
    try:
        return await packages.uninstall(payload.get("name", ""))
    except packages.PackageError as e:
        raise HTTPException(400, str(e))


# ---------------------------------------------------------------------------
# dev: simulate a real discord event end-to-end without touching the gateway
# ---------------------------------------------------------------------------

class RecordingExecutor:
    def __init__(self):
        self.messages: list[dict] = []
        self.actions: list[dict] = []

    async def send_text(self, channel_id, content, reference_message_id=None):
        self.messages.append({"channel_id": channel_id, "content": content,
                              "reference_message_id": reference_message_id})

    async def execute(self, action):
        self.actions.append(action)


@app.post("/api/dev/simulate")
async def dev_simulate(payload: dict = Body(...)):
    """Run an event through the real dispatch+aggregation pipeline, but record
    actions instead of performing them. Works without a bot token."""
    executor = RecordingExecutor()
    event = payload.get("event", "on_message")
    data = payload.get("data") or {}
    trace = await manager.dispatch(event, data, executor=executor)
    trace["recorded_messages"] = executor.messages
    trace["recorded_actions"] = executor.actions
    return trace


# ---------------------------------------------------------------------------
# static frontend
# ---------------------------------------------------------------------------

STATIC_DIR = APP_DIR / "static"


def _render_page(filename: str) -> str:
    """Serve an html page with site config substituted in."""
    html = (STATIC_DIR / filename).read_text()
    head_extra = (
        f"<style>:root{{--font-ui:{CONFIG.font_ui};--font-code:{CONFIG.font_code};}}"
        f"</style>\n"
        f"<script>window.SITE_CONFIG={json.dumps({
            'title': CONFIG.site_title,
            'font_ui': CONFIG.font_ui,
            'font_code': CONFIG.font_code,
        })}</script>"
    )
    html = html.replace("{{HEAD_EXTRA}}", head_extra)
    html = html.replace("{{SITE_TITLE}}", CONFIG.site_title)
    html = html.replace("{{DOMAIN}}", CONFIG.domain.rstrip("/"))
    return html


@app.get("/")
async def landing():
    return Response(_render_page("landing.html"), media_type="text/html")


@app.get("/app")
async def console_app():
    return Response(_render_page("index.html"), media_type="text/html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
