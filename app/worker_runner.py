"""Worker process: loads one project's python file and executes its handlers.

Spawned by the core as:
    python -m app.worker_runner --project-dir <dir> --file <handler.py|draft.py>
        --kv <kv.json path> --slug <slug>

Protocol: JSON lines on stdin/stdout (UTF-8).
  in : {"type":"run","id":N,"event":"on_message","data":{...},"timeout":sec}
       event "__failure__": data = original event data plus
       "_failure_of" (original event name) and "_error" (string).
  out: {"type":"ready"}                      module loaded fine
       {"type":"init_error","traceback":...} module failed to import
       {"type":"log","line":...}             streamed log (log() / print)
       {"type":"result","id":N,"actions":[...],"logs":[...],
        "error":{"message","traceback"}|null,"duration":sec}

Actions produced by framework calls during a run:
  send     {"action":"send","channel_id":..., "content":...}
           (or with "embed":{...} / "file":{"filename","data_b64"} instead of content)
  reply    {"action":"reply","channel_id":...,"message_id":...,"content":...}
  react    {"action":"react","channel_id":...,"message_id":...,"emoji":...}
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import inspect
import io
import json
import sys
import threading
import time
import traceback
import types
from pathlib import Path

# Keep a reference to the real stdout for protocol traffic; user print()
# is redirected to the project console afterwards.
_PROTO = sys.stdout
_WRITE_LOCK = threading.Lock()

# Per-run context (one run at a time in this process).
_ctx = None          # dict with event/data/actions/logs/queries
_kv = None           # KVStore
_slug = "?"
_project_dir = "."

import itertools
_qids = itertools.count(1)


def _send(obj: dict) -> None:
    line = json.dumps(obj, ensure_ascii=False)
    with _WRITE_LOCK:
        _PROTO.write(line + "\n")
        _PROTO.flush()


class _PrintCapture(io.TextIOBase):
    def __init__(self):
        self._buf = ""

    def write(self, s):
        if not s:
            return 0
        self._buf += s
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            if line:
                if _ctx is not None:
                    _ctx["logs"].append(line)
                _send({"type": "log", "line": line})
        return len(s)

    def flush(self):
        if self._buf:
            if _ctx is not None:
                _ctx["logs"].append(self._buf)
            _send({"type": "log", "line": self._buf})
            self._buf = ""


# --------------------------------------------------------------------------
# Framework functions injected into the handler module's namespace
# --------------------------------------------------------------------------

def _event_channel():
    if _ctx is not None:
        return _ctx["data"].get("channel_id")
    return None


def _event_message_id():
    if _ctx is None:
        return None
    d = _ctx["data"]
    return d.get("id") or d.get("message_id")


def _record(action: dict):
    if _ctx is None:
        _send({"type": "log", "line":
               f"[framework] warning: {action['action']}() called outside an event, ignored"})
        return
    _ctx["actions"].append(action)


def send(text, channel_id=None):
    _record_send(str(text), channel_id, None)


def reply(text):
    """Same as send(), but the (aggregated) message quotes the triggering
    message via Discord's reply reference."""
    mid = _ctx["data"].get("id") if _ctx is not None else None
    if mid is None:
        log("[framework] reply() dropped: the current event has no message to reply to")
        return
    _record_send(str(text), None, mid)


def _record_send(text, channel_id, reply_to_message_id):
    ch = channel_id if channel_id is not None else _event_channel()
    if ch is None:
        log("[framework] send() dropped: no channel context")
        return
    a = {"action": "send", "channel_id": ch, "content": text}
    if reply_to_message_id is not None:
        a["reply_to_message_id"] = reply_to_message_id
    _record(a)


def add_reaction(emoji, message_id=None):
    ch = _event_channel()
    mid = message_id if message_id is not None else _event_message_id()
    if ch is None or mid is None:
        log("[framework] add_reaction() dropped: no message context")
        return
    _record({"action": "react", "channel_id": ch, "message_id": mid, "emoji": str(emoji)})


def send_embed(title=None, description=None, color=0x5865F2, fields=None, channel_id=None):
    ch = channel_id if channel_id is not None else _event_channel()
    if ch is None:
        log("[framework] send_embed() dropped: no channel context")
        return
    if isinstance(color, str):
        color = int(color.lstrip("#"), 16)
    embed = {"title": title, "description": description, "color": color,
             "fields": fields or []}
    _record({"action": "send", "channel_id": ch, "embed": embed})


def send_file(filename, content, channel_id=None):
    ch = channel_id if channel_id is not None else _event_channel()
    if ch is None:
        log("[framework] send_file() dropped: no channel context")
        return
    if isinstance(content, str):
        content = content.encode("utf-8")
    _record({"action": "send", "channel_id": ch,
             "file": {"filename": str(filename),
                      "data_b64": base64.b64encode(content).decode("ascii")}})


def log(msg):
    line = f"{msg}"
    if _ctx is not None:
        _ctx["logs"].append(line)
    _send({"type": "log", "line": line})


def kv_get(key, default=None):
    return _kv.get(key, default)


def kv_set(key, value):
    _kv.set(key, value)


def kv_delete(key):
    return _kv.delete(key)


def kv_keys():
    return _kv.keys()


def kv_all():
    return _kv.items()


def secret_get(key, default=None):
    """Read the project's secret store (.env file). Strings only.
    Re-read from disk on every call, so UI edits apply immediately."""
    from app.envfile import parse_env  # sys.path set up by main()
    try:
        text = (Path(_project_dir) / ".env").read_text()
    except FileNotFoundError:
        return default
    return parse_env(text).get(str(key), default)


def get_message(message_id, channel_id=None):
    """Fetch any message the bot can see. Returns a message dict, or None on
    any failure (not found / no access / bot offline) with a console note.
    channel_id defaults to the current event's channel."""
    ch = channel_id if channel_id is not None else _event_channel()
    if ch is None:
        log("[framework] get_message() dropped: no channel context")
        return None
    args = {"message_id": int(message_id), "channel_id": int(ch)}
    # Test mode: never touch Discord. Serve the configured fake message when
    # its id matches, otherwise None.
    if _ctx is not None and _ctx.get("test_mode"):
        fm = _ctx.get("fake_message")
        try:
            found = fm is not None and int(fm.get("id")) == int(message_id)
        except (TypeError, ValueError):
            found = False
        _record_query(args, fm if found else None, None)
        return fm if found else None
    qid = next(_qids)
    _send({"type": "query", "qid": qid, "query": "get_message", "args": args})
    # Wait for the core to answer on stdin. Nothing else can interleave: the
    # core only writes run-requests when idle and query-results on demand.
    while True:
        line = sys.stdin.readline()
        if not line:  # core died
            _record_query(args, None, "lost connection to core")
            return None
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if msg.get("type") == "query_result" and msg.get("qid") == qid:
            err = msg.get("error")
            _record_query(args, msg.get("value"), err)
            if err:
                log(f"[framework] get_message({message_id}) failed: {err}")
                return None
            return msg.get("value")


def _record_query(args, value, error):
    if _ctx is not None:
        _ctx["queries"].append({"query": "get_message", "args": args,
                                "result": value, "error": error})


FRAMEWORK_FUNCS = {
    "send": send,
    "reply": reply,
    "add_reaction": add_reaction,
    "send_embed": send_embed,
    "send_file": send_file,
    "log": log,
    "kv_get": kv_get,
    "kv_set": kv_set,
    "kv_delete": kv_delete,
    "kv_keys": kv_keys,
    "kv_all": kv_all,
    "secret_get": secret_get,
    "get_message": get_message,
}

EVENT_HANDLERS = [
    "on_message", "on_message_edit", "on_message_delete",
    "on_reaction_add", "on_reaction_remove",
]


# --------------------------------------------------------------------------
# Module loading and handler execution
# --------------------------------------------------------------------------

_module = None          # loaded handler module
_module_ok = False
_init_traceback = None


def load_module(path: Path):
    global _module, _module_ok, _init_traceback
    mod = types.ModuleType("project_handler")
    mod.__dict__.update(FRAMEWORK_FUNCS)
    mod.__dict__["__file__"] = str(path)
    mod.__dict__["__name__"] = "project_handler"
    _module = mod
    try:
        src = path.read_text(encoding="utf-8")
        code = compile(src, str(path), "exec")
        exec(code, mod.__dict__)
        _module_ok = True
    except BaseException:
        _module_ok = False
        _init_traceback = traceback.format_exc()


def _handler_for(event: str):
    if event == "__failure__":
        return getattr(_module, "on_failure", None), "on_failure"
    return getattr(_module, event, None), event


def _call(func, args):
    ret = func(*args)
    if inspect.isawaitable(ret):
        asyncio.run(ret)


def run_event(req: dict) -> dict:
    global _ctx
    rid = req["id"]
    event = req["event"]
    data = req.get("data") or {}
    started = time.monotonic()

    result = {"type": "result", "id": rid, "actions": [], "logs": [],
              "queries": [], "error": None, "duration": 0.0}

    if not _module_ok:
        result["error"] = {"message": "project code failed to load",
                           "traceback": _init_traceback or ""}
        return result

    func, handler_name = _handler_for(event)
    if func is None:
        if event == "__failure__":
            result["error"] = None  # nothing to do, not an error
        return result

    _ctx = {"event": event, "data": data, "actions": result["actions"],
            "logs": result["logs"], "queries": result["queries"],
            "test_mode": bool(req.get("test")),
            "fake_message": req.get("fake_message")}
    try:
        if event == "__failure__":
            failure_of = data.get("_failure_of", "?")
            err = data.get("_error", "unknown error")
            orig = {k: v for k, v in data.items() if not k.startswith("_")}
            try:
                _call(func, (failure_of, orig, err))
            except BaseException:
                # failure handler itself failed: log and do nothing
                tb = traceback.format_exc()
                result["logs"].append(f"[framework] on_failure itself failed:\n{tb}")
                _send({"type": "log",
                       "line": f"[framework] on_failure itself failed:\n{tb}"})
        else:
            try:
                _call(func, (data,))
            except BaseException:
                tb = traceback.format_exc()
                result["error"] = {"message": tb.strip().splitlines()[-1],
                                   "traceback": tb}
                # in-process failure handler
                on_failure = getattr(_module, "on_failure", None)
                if callable(on_failure):
                    try:
                        _call(on_failure, (event, data, tb.strip().splitlines()[-1]))
                    except BaseException:
                        ftb = traceback.format_exc()
                        result["logs"].append(
                            f"[framework] on_failure itself failed:\n{ftb}")
                        _send({"type": "log",
                               "line": f"[framework] on_failure itself failed:\n{ftb}"})
    finally:
        sys.stdout.flush()
        _ctx = None
        result["actions"] = [a for a in result["actions"]]
        result["logs"] = [l for l in result["logs"]]
        result["duration"] = round(time.monotonic() - started, 4)
    return result


def main():
    global _kv, _slug, _project_dir
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-dir", required=True)
    parser.add_argument("--file", required=True)
    parser.add_argument("--kv", required=True)
    parser.add_argument("--slug", default="?")
    args = parser.parse_args()
    _slug = args.slug
    _project_dir = args.project_dir

    sys.path.insert(0, str(Path(args.project_dir).resolve()))

    from app.kvstore import KVStore  # noqa: E402  (late import, needs sys.path)
    _kv = KVStore(Path(args.kv))

    sys.stdout = _PrintCapture()

    load_module(Path(args.file))
    if _module_ok:
        _send({"type": "ready"})
    else:
        _send({"type": "init_error", "traceback": _init_traceback})

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue
        if req.get("type") == "run":
            res = run_event(req)
            _send(res)
        elif req.get("type") == "shutdown":
            break
    sys.stdout.flush()


if __name__ == "__main__":
    main()
