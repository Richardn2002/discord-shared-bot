"""Project CRUD: on-disk layout, metadata, compile checks, deploy.

Layout per project (under files/projects/<slug>/):
    project.json   metadata
    draft.py       edited in the UI
    handler.py     live code (created by deploy)
    kv.json        real KV store
    kv_test.json   test KV store
    console.log    project console (log(), print(), errors, lifecycle)
"""
from __future__ import annotations

import json
import py_compile
import re
import shutil
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

from .config import CONFIG
from .template import HANDLER_TEMPLATE

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")

DEFAULT_TIMEOUT = 5.0    # max handler execution seconds (deadlock catcher)
DEFAULT_TOLERANCE = 10.0  # max seconds the aggregator waits before flushing


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ProjectError(Exception):
    pass


class Project:
    def __init__(self, slug: str, meta: dict):
        self.slug = slug
        self.meta = meta

    # -- paths -------------------------------------------------------------
    @property
    def dir(self) -> Path:
        return CONFIG.projects_dir / self.slug

    @property
    def draft(self) -> Path:
        return self.dir / "draft.py"

    @property
    def handler(self) -> Path:
        return self.dir / "handler.py"

    @property
    def kv_path(self) -> Path:
        return self.dir / "kv.json"

    @property
    def kv_test_path(self) -> Path:
        return self.dir / "kv_test.json"

    @property
    def console_path(self) -> Path:
        return self.dir / "console.log"

    # -- metadata ----------------------------------------------------------
    @property
    def deployed(self) -> bool:
        return self.handler.exists()

    @property
    def timeout(self) -> float:
        return float(self.meta.get("timeout", DEFAULT_TIMEOUT))

    @property
    def tolerance(self) -> float:
        return float(self.meta.get("tolerance", DEFAULT_TOLERANCE))

    @property
    def created_ts(self) -> float:
        return float(self.meta.get("created_ts", 0.0))

    def save_meta(self) -> None:
        (self.dir / "project.json").write_text(
            json.dumps(self.meta, indent=2, ensure_ascii=False)
        )

    def public_info(self, runtime: dict | None = None) -> dict:
        info = {
            "slug": self.slug,
            "nickname": self.meta.get("nickname", self.slug),
            "author": self.meta.get("author", ""),
            "timeout": self.timeout,
            "tolerance": self.tolerance,
            "created_at": self.meta.get("created_at"),
            "created_ts": self.created_ts,
            "deployed": self.deployed,
            "deployed_at": self.meta.get("deployed_at"),
            "runtime": runtime or {},
        }
        return info


def _load(slug: str) -> Project:
    d = CONFIG.projects_dir / slug
    meta_path = d / "project.json"
    if not meta_path.exists():
        raise ProjectError(f"project '{slug}' not found")
    try:
        meta = json.loads(meta_path.read_text())
    except Exception:
        meta = {}
    return Project(slug, meta)


def get(slug: str) -> Project:
    if not SLUG_RE.match(slug):
        raise ProjectError("invalid slug")
    return _load(slug)


def list_all() -> list[Project]:
    projects = []
    if CONFIG.projects_dir.exists():
        for d in sorted(CONFIG.projects_dir.iterdir()):
            if d.is_dir() and (d / "project.json").exists():
                try:
                    projects.append(_load(d.name))
                except Exception:
                    continue
    projects.sort(key=lambda p: p.created_ts)
    return projects


def create(slug: str, nickname: str, author: str) -> Project:
    slug = slug.strip()
    if not SLUG_RE.match(slug):
        raise ProjectError(
            "invalid slug: use lowercase letters, digits, '-' and '_' (start with letter/digit)"
        )
    d = CONFIG.projects_dir / slug
    if d.exists():
        raise ProjectError(f"project '{slug}' already exists")
    d.mkdir(parents=True)
    meta = {
        "slug": slug,
        "nickname": nickname.strip() or slug,
        "author": author.strip(),
        "timeout": DEFAULT_TIMEOUT,
        "tolerance": DEFAULT_TOLERANCE,
        "created_at": now_iso(),
        "created_ts": time.time(),
        "deployed_at": None,
    }
    p = Project(slug, meta)
    p.save_meta()
    p.draft.write_text(
        HANDLER_TEMPLATE.format(
            nickname=meta["nickname"], slug=slug, author=meta["author"] or "?"
        )
    )
    p.kv_path.write_text("{}")
    return p


def update_meta(project: Project, updates: dict) -> Project:
    for key in ("nickname", "author"):
        if key in updates:
            project.meta[key] = str(updates[key])
    for key in ("timeout", "tolerance"):
        if key in updates:
            try:
                val = float(updates[key])
            except (TypeError, ValueError):
                raise ProjectError(f"{key} must be a number")
            if not (0.2 <= val <= 600):
                raise ProjectError(f"{key} must be between 0.2 and 600 seconds")
            project.meta[key] = val
    project.save_meta()
    return project


def delete(project: Project) -> None:
    shutil.rmtree(project.dir, ignore_errors=True)


# -- code operations -------------------------------------------------------

def read_code(project: Project, which: str = "draft") -> str:
    path = project.draft if which == "draft" else project.handler
    if not path.exists():
        return ""
    return path.read_text()


def write_draft(project: Project, code: str) -> None:
    project.draft.write_text(code)


def compile_check(project: Project) -> dict:
    """py-compile the draft. Returns {"ok": bool, "error": str|None}."""
    try:
        py_compile.compile(str(project.draft), doraise=True, quiet=1)
        return {"ok": True, "error": None}
    except py_compile.PyCompileError as e:
        return {"ok": False, "error": e.msg.strip()}
    except Exception:
        return {"ok": False, "error": traceback.format_exc()}


def deploy(project: Project) -> None:
    check = compile_check(project)
    if not check["ok"]:
        raise ProjectError("draft does not compile: " + (check["error"] or ""))
    shutil.copyfile(project.draft, project.handler)
    project.meta["deployed_at"] = now_iso()
    project.save_meta()


def console_append(project: Project, line: str, level: str = "INFO") -> None:
    """Append a line to the project console log (capped file)."""
    path = project.console_path
    ts = now_iso()
    try:
        with path.open("a") as f:
            f.write(f"[{ts}] [{level}] {line}\n")
        # cap at ~512KB, keep the tail
        if path.stat().st_size > 512 * 1024:
            data = path.read_bytes()
            path.write_bytes(b"... [log truncated] ...\n" + data[-256 * 1024:])
    except Exception:
        pass
