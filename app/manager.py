"""Worker manager: owns one subprocess per deployed project, dispatches
Discord events to all of them, enforces per-project timeouts, and aggregates
plain text messages across projects (creation order, "[nickname]:" sections)
before handing them to an executor.

Executors (see bot.py / simulate endpoint):
    async send_text(channel_id: int, content: str) -> None
    async execute(action: dict) -> None        # reply / react / embed / file
"""
from __future__ import annotations

import asyncio
import json
import sys
import traceback
from pathlib import Path

from . import projects
from .config import APP_DIR, CONFIG, ROOT_DIR

MAX_DISCORD_LEN = 2000

# Worker protocol lines carry base64 payloads (attached files/embeds), so they
# can be much larger than asyncio's 64 KiB default StreamReader limit. Without
# this, any single action above ~48 KiB makes the reader give up and the worker
# look like it crashed.
MAX_WORKER_LINE = 64 * 1024 * 1024


def _console(project, line, level="INFO"):
    projects.console_append(project, line, level)


class Worker:
    """One project worker subprocess (lazy start, serialized runs)."""

    def __init__(self, project: projects.Project,
                 code_file: str = "handler.py", kv_file: str = "kv.json"):
        self.project = project
        self.code_path = project.dir / code_file
        self.kv_path = project.dir / kv_file
        self.proc: asyncio.subprocess.Process | None = None
        self.reader: asyncio.Task | None = None
        self.stderr_reader: asyncio.Task | None = None
        self.lock = asyncio.Lock()
        self.pending: dict[int, asyncio.Future] = {}
        self.next_id = 1
        self.state = "stopped"        # stopped | starting | running | broken | exited
        self.init_error: str | None = None
        self.last_error: str | None = None
        self._killed_intentionally = False
        self.disabled = False         # true after an explicit Stop; events skip it

    # -- lifecycle ---------------------------------------------------------
    async def ensure_started(self) -> None:
        if self.proc and self.proc.returncode is None and self.state == "running":
            return
        await self._kill()
        self.state = "starting"  # type: ignore
        _console(self.project, f"starting worker (file={self.code_path.name}, kv={self.kv_path.name})")
        try:
            self.proc = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "app.worker_runner",
                "--project-dir", str(self.project.dir),
                "--file", str(self.code_path),
                "--kv", str(self.kv_path),
                "--slug", self.project.slug,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                limit=MAX_WORKER_LINE,
                cwd=str(ROOT_DIR),
            )
        except Exception:
            self.state = "exited"  # type: ignore
            raise

        # handshake: read lines until ready / init_error
        try:
            while True:
                raw = await asyncio.wait_for(self.proc.stdout.readline(), timeout=15)
                if not raw:
                    self.state = "broken"  # type: ignore
                    self.init_error = "worker exited during startup"
                    raise RuntimeError(self.init_error)
                msg = json.loads(raw.decode("utf-8", "replace"))
                if msg.get("type") == "ready":
                    break
                if msg.get("type") == "init_error":
                    self.state = "broken"  # type: ignore
                    self.init_error = msg.get("traceback", "unknown init error")
                    _console(self.project, "worker failed to load module:\n" + self.init_error, "ERROR")
                    self.reader = asyncio.create_task(self._reader())
                    self.stderr_reader = asyncio.create_task(self._stderr_reader())
                    raise RuntimeError("init_error")
                if msg.get("type") == "log":
                    _console(self.project, msg.get("line", ""))
        except asyncio.TimeoutError:
            self.state = "broken"  # type: ignore
            self.init_error = "worker startup timed out"
            await self._kill()
            raise RuntimeError(self.init_error)

        self.state = "running"  # type: ignore
        self.init_error = None
        self.reader = asyncio.create_task(self._reader())
        self.stderr_reader = asyncio.create_task(self._stderr_reader())
        _console(self.project, f"worker running (pid={self.proc.pid})")

    async def stop(self) -> None:
        await self._kill()
        self.state = "stopped"  # type: ignore
        _console(self.project, "worker stopped")

    async def _kill(self) -> None:
        for t in (self.reader, self.stderr_reader):
            if t:
                t.cancel()
        self.reader = self.stderr_reader = None
        if self.proc and self.proc.returncode is None:
            self._killed_intentionally = True
            try:
                self.proc.kill()
                await self.proc.wait()
            except ProcessLookupError:
                pass
            except Exception:
                pass
        self.proc = None
        for fut in self.pending.values():
            if not fut.done():
                fut.set_exception(RuntimeError("worker stopped"))
        self.pending.clear()

    # -- io ----------------------------------------------------------------
    async def _reader(self) -> None:
        try:
            assert self.proc and self.proc.stdout
            async for raw in self.proc.stdout:
                try:
                    msg = json.loads(raw.decode("utf-8", "replace"))
                except json.JSONDecodeError:
                    continue
                t = msg.get("type")
                if t == "result":
                    fut = self.pending.pop(msg.get("id"), None)
                    if fut and not fut.done():
                        fut.set_result(msg)
                elif t == "log":
                    _console(self.project, msg.get("line", ""))
                elif t == "query":
                    asyncio.create_task(self._answer_query(msg))
        except Exception:
            pass
        finally:
            if self.proc and self.proc.returncode is None:
                try:
                    self.proc.kill()
                except Exception:
                    pass
            code = self.proc.returncode if self.proc else "?"
            intentional = self._killed_intentionally
            self._killed_intentionally = False
            for fut in list(self.pending.values()):
                if not fut.done():
                    fut.set_exception(RuntimeError(f"worker exited (code {code})"))
            self.pending.clear()
            if self.state == "running":
                if intentional:
                    self.state = "stopped"  # type: ignore
                else:
                    self.state = "exited"  # type: ignore
                    _console(self.project, f"worker exited unexpectedly (code {code})", "ERROR")

    async def _answer_query(self, msg: dict) -> None:
        """Worker asked us something mid-handler (e.g. get_message). Answer over
        its stdin so the handler can continue."""
        answer = {"type": "query_result", "qid": msg.get("qid")}
        try:
            if msg.get("query") != "get_message":
                answer["error"] = f"unknown query '{msg.get('query')}'"
            else:
                args = msg.get("args") or {}
                ex = Manager.INSTANCE.executor if Manager.INSTANCE else None
                if ex is None or not hasattr(ex, "fetch_message"):
                    answer["error"] = "bot is offline (no Discord token)"
                elif args.get("channel_id") is None:
                    answer["error"] = "no channel_id"
                else:
                    answer["value"] = await ex.fetch_message(
                        channel_id=int(args["channel_id"]),
                        message_id=int(args["message_id"]))
        except Exception as e:
            answer["error"] = str(e)
        try:
            if self.proc and self.proc.stdin and self.proc.returncode is None:
                self.proc.stdin.write((json.dumps(answer) + "\n").encode("utf-8"))
                await self.proc.stdin.drain()
        except Exception:
            pass

    async def _stderr_reader(self) -> None:
        try:
            assert self.proc and self.proc.stderr
            async for raw in self.proc.stderr:
                line = raw.decode("utf-8", "replace").rstrip()
                if line:
                    _console(self.project, f"[stderr] {line}", "WARN")
        except Exception:
            pass

    # -- running handlers ----------------------------------------------------
    async def run(self, event: str, data: dict, timeout: float,
                  allow_failure_handler: bool = True,
                  test_mode: bool = False, fake_message: dict | None = None) -> dict:
        """Run one event through the worker. Never raises; returns a result dict."""
        async with self.lock:
            try:
                await self.ensure_started()
            except RuntimeError as e:
                return {"actions": [], "logs": [],
                        "error": {"message": f"project failed to load: {e}",
                                  "traceback": self.init_error or ""},
                        "duration": 0.0, "init_error": True}

            rid = self.next_id
            self.next_id += 1
            fut = asyncio.get_running_loop().create_future()
            self.pending[rid] = fut
            req = {"type": "run", "id": rid, "event": event, "data": data,
                   "timeout": timeout}
            if test_mode:
                req["test"] = True
                req["fake_message"] = fake_message
            try:
                self.proc.stdin.write((json.dumps(req) + "\n").encode("utf-8"))
                await self.proc.stdin.drain()
            except Exception as e:
                self.pending.pop(rid, None)
                return {"actions": [], "logs": [],
                        "error": {"message": f"failed to talk to worker: {e}",
                                  "traceback": ""},
                        "duration": 0.0}
            try:
                result = await asyncio.wait_for(fut, timeout)
            except asyncio.TimeoutError:
                _console(self.project,
                         f"handler '{event}' timed out after {timeout}s; killing worker",
                         "ERROR")
                await self._kill()
                self.state = "stopped"  # type: ignore
                result = {"actions": [], "logs": [],
                          "error": {"message": f"handler timed out after {timeout}s",
                                    "traceback": ""},
                          "duration": timeout, "timeout": True}
                if allow_failure_handler and event != "__failure__":
                    asyncio.create_task(self._run_failure_handler(event, data, result, timeout))
            except Exception as e:
                result = {"actions": [], "logs": [],
                          "error": {"message": str(e),
                                    "traceback": traceback.format_exc()},
                          "duration": 0.0}
            return result

    async def _run_failure_handler(self, event: str, data: dict, result: dict,
                                   timeout: float) -> None:
        """After a hard timeout: restart the worker and call its on_failure."""
        data = dict(data)
        data["_failure_of"] = event
        data["_error"] = result["error"]["message"] if result.get("error") else "timeout"
        res = await self.run("__failure__", data, timeout, allow_failure_handler=False)
        if res.get("error"):
            _console(self.project,
                     "on_failure (after timeout) errored: "
                     + res["error"]["message"], "ERROR")
        if res.get("actions") and Manager.INSTANCE:
            await Manager.INSTANCE.deliver_adhoc(self.project, res["actions"])


class Manager:
    INSTANCE: "Manager | None" = None

    def __init__(self):
        self.workers: dict[str, Worker] = {}
        self.executor = None  # set by bot layer
        self.test_locks: dict[str, asyncio.Lock] = {}
        Manager.INSTANCE = self

    # -- workers -------------------------------------------------------------
    def get_worker(self, slug: str) -> Worker | None:
        return self.workers.get(slug)

    def _worker_for(self, project: projects.Project) -> Worker:
        w = self.workers.get(project.slug)
        if w is None or w.code_path.name != "handler.py":
            w = Worker(project)
            self.workers[project.slug] = w
        return w

    def status(self, slug: str) -> dict:
        w = self.workers.get(slug)
        if not w:
            return {"state": "sleeping", "disabled": False}
        return {
            "state": w.state,
            "disabled": w.disabled,
            "pid": w.proc.pid if w.proc and w.proc.returncode is None else None,
            "init_error": w.init_error,
            "busy": w.lock.locked(),
        }

    async def set_running(self, slug: str, running: bool) -> dict:
        """Explicitly start/stop a project's worker. A stopped project stays
        stopped for incoming events until started again."""
        project = projects.get(slug)
        w = self._worker_for(project)
        if running:
            if not project.deployed:
                raise projects.ProjectError("project is not deployed yet")
            w.disabled = False
            try:
                await asyncio.wait_for(w.ensure_started(), timeout=20)
            except Exception:
                pass  # state/init_error in status tells the story
            projects.console_append(project, "worker start requested")
        else:
            w.disabled = True
            await w._kill()
            w.state = "stopped"  # type: ignore
            projects.console_append(project, "worker stopped by user")
        return self.status(slug)

    async def restart(self, slug: str) -> dict:
        project = projects.get(slug)
        w = self._worker_for(project)
        await w._kill()
        w.state = "stopped"  # type: ignore
        w.disabled = False
        if project.deployed:
            try:
                await asyncio.wait_for(w.ensure_started(), timeout=20)
            except Exception:
                pass
        return self.status(slug)

    async def remove(self, slug: str) -> None:
        w = self.workers.pop(slug, None)
        if w:
            await w.stop()

    async def shutdown(self) -> None:
        for w in list(self.workers.values()):
            try:
                await w._kill()
            except Exception:
                pass
        self.workers.clear()

    # -- event dispatch + aggregation ----------------------------------------
    async def dispatch(self, event: str, data: dict, executor=None) -> dict:
        """Dispatch an event to all deployed projects, aggregate plain-text
        sends in project creation order, execute everything via `executor`.
        Returns a trace (used by the dev/simulate endpoint and tests)."""
        executor = executor or self.executor
        if executor is None:
            raise RuntimeError("no executor available (bot offline)")
        deployed = [p for p in projects.list_all() if p.deployed]
        deployed = [p for p in deployed
                    if not (self.workers.get(p.slug) and self.workers[p.slug].disabled)]
        trace = {"event": event, "projects": {}, "messages_sent": [], "flush": None}
        if not deployed:
            trace["flush"] = "no deployed projects"
            return trace

        sections: dict[str, list[str]] = {}    # slug -> buffered plain texts
        immediate: list[tuple[projects.Project, dict]] = []
        results: dict[str, dict] = {}
        done: set[str] = set()
        all_done = asyncio.Event()
        channel_id = data.get("channel_id")
        reply_ref: int | None = None           # triggering message to quote
        nick_of = {p.slug: p.meta.get("nickname", p.slug) for p in deployed}
        proj_of = {p.slug: p for p in deployed}

        async def collect(p: projects.Project, res: dict):
            if res.get("error"):
                _console(p, f"handler '{event}' failed: {res['error']['message']}", "ERROR")
            results[p.slug] = res
            sections.setdefault(p.slug, [])
            nonlocal reply_ref
            for a in res.get("actions", []):
                if a.get("action") == "send" and "content" in a \
                        and "embed" not in a and "file" not in a:
                    sections[p.slug].append(a["content"])
                    if a.get("reply_to_message_id"):
                        reply_ref = int(a["reply_to_message_id"])
                else:
                    immediate.append((p, a))
            done.add(p.slug)
            if len(done) == len(deployed):
                all_done.set()

        collectors: list[asyncio.Task] = []
        for p in deployed:
            async def run_one(p=p):
                res = await self._worker_for(p).run(event, data, p.timeout)
                await collect(p, res)
            collectors.append(asyncio.create_task(run_one()))

        async def flush(kind: str, only_slug: str | None = None) -> bool:
            """Send buffered sections (in creation order). Returns True if sent."""
            slugs = [only_slug] if only_slug else [p.slug for p in deployed]
            blocks = []
            for slug in slugs:
                texts = sections.get(slug) or []
                if texts:
                    blocks.append(f"[{nick_of[slug]}]:\n" + "\n".join(texts))
                    sections[slug] = []
            if not blocks:
                return False
            for chunk in _chunk_blocks(blocks):
                trace["messages_sent"].append(
                    {"channel_id": channel_id, "content": chunk, "why": kind,
                     "reply_to": reply_ref})
                if channel_id is None:
                    continue
                try:
                    await executor.send_text(channel_id, chunk, reply_ref)
                except Exception as e:
                    for p in deployed:
                        _console(p, f"failed to send aggregated message: {e}", "ERROR")
                    break
            return True

        tolerance = min(p.tolerance for p in deployed)
        flushed_partial = False
        try:
            await asyncio.wait_for(all_done.wait(), timeout=tolerance)
        except asyncio.TimeoutError:
            # tolerance expired: ship whatever sections we already have
            if await flush("tolerance-expired"):
                trace["flush"] = f"tolerance {tolerance:g}s expired"
                flushed_partial = True

        if not flushed_partial:
            sent = await flush("complete")
            trace["flush"] = "complete" if sent else "complete (nothing to send)"

        # late finishers: each remaining section goes out on its own message
        if flushed_partial:
            late_budget = max((p.timeout for p in deployed), default=0.0) + 1.0
            try:
                await asyncio.wait_for(all_done.wait(), timeout=late_budget)
            except asyncio.TimeoutError:
                pass  # hung workers are already being killed/reported
            for p in deployed:
                if sections.get(p.slug):
                    await flush(f"late:{p.slug}", only_slug=p.slug)

        # non-aggregatable actions (reply / react / embed / file)
        for (p, a) in immediate:
            try:
                await executor.execute(a)
            except Exception as e:
                _console(p, f"action {a.get('action')} failed: {e}", "ERROR")
        for p in deployed:
            trace["projects"][p.slug] = results.get(p.slug, {"missing": True})
        return trace

    async def deliver_adhoc(self, project: projects.Project, actions: list[dict]):
        """Deliver actions from a post-timeout on_failure run: aggregate the
        project's own text sends and execute the rest."""
        if self.executor is None:
            return
        texts, rest = [], []
        reply_ref = None
        for a in actions:
            if a.get("action") == "send" and "content" in a \
                    and "embed" not in a and "file" not in a:
                texts.append(a["content"])
                if a.get("reply_to_message_id"):
                    reply_ref = int(a["reply_to_message_id"])
            else:
                rest.append(a)
        if texts:
            nick = project.meta.get("nickname", project.slug)
            ch = next((a.get("channel_id") for a in actions if a.get("channel_id")), None)
            for chunk in _chunk_blocks([f"[{nick}]:\n" + "\n".join(texts)]):
                if ch is not None:
                    await self.executor.send_text(ch, chunk, reply_ref)
        for a in rest:
            try:
                await self.executor.execute(a)
            except Exception as e:
                _console(project, f"failure-handler action failed: {e}", "ERROR")

    # -- test runner ----------------------------------------------------------
    async def run_test(self, project: projects.Project, event: str, data: dict,
                       fake_message: dict | None = None) -> dict:
        """Run draft.py once against a freshly cloned test KV store."""
        from .kvstore import KVStore
        lock = self.test_locks.setdefault(project.slug, asyncio.Lock())
        async with lock:
            KVStore(project.kv_test_path).clone_from(project.kv_path)
            w = Worker(project, code_file="draft.py", kv_file="kv_test.json")
            try:
                res = await w.run(event, data, project.timeout,
                                  allow_failure_handler=False,
                                  test_mode=True, fake_message=fake_message)
            finally:
                await w._kill()
            return res


def _chunk_blocks(blocks: list[str]) -> list[str]:
    """Join "[nick]: ..." blocks into <=2000-char messages, splitting when needed."""
    msgs: list[str] = []
    cur = ""
    for b in blocks:
        if not cur:
            cur = b
        elif len(cur) + 2 + len(b) <= MAX_DISCORD_LEN:
            cur += "\n\n" + b
        else:
            msgs.append(cur)
            cur = b
    if cur:
        msgs.append(cur)
    # hard-split any overlong single message
    out: list[str] = []
    for m in msgs:
        while len(m) > MAX_DISCORD_LEN:
            cut = m.rfind("\n", 0, MAX_DISCORD_LEN)
            if cut < MAX_DISCORD_LEN // 2:
                cut = MAX_DISCORD_LEN
            out.append(m[:cut])
            m = m[cut:].lstrip("\n")
        if m:
            out.append(m)
    return out
