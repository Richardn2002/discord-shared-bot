"""pip package management, shared venv.

Installed packages become importable by workers immediately (Python resolves
imports at first use); restarting a project's worker is only needed if the
project already imported an older copy.
"""
from __future__ import annotations

import asyncio
import json
import re
import sys

SPEC_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9._\-\[\], ~=<>!;()'\"]{0,200})$")


class PackageError(Exception):
    pass


async def _run_pip(*args: str) -> dict:
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "pip", *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=300)
    except asyncio.TimeoutError:
        proc.kill()
        raise PackageError("pip timed out after 300s")
    return {"ok": proc.returncode == 0,
            "output": out.decode("utf-8", "replace")}


PROTECTED = {"pip", "setuptools", "wheel", "fastapi", "uvicorn",
             "discord.py", "discord-py", "discordpy"}


def _norm(name: str) -> str:
    return name.lower().replace("_", "-")


def platform_dependency_closure() -> set[str]:
    """Roots (platform packages) + everything they depend on, transitively.
    Markers are only consulted to skip optional extras; anything else is
    included (over-protecting is the safe side for uninstall guards)."""
    try:
        from importlib.metadata import requires
    except Exception:
        return {_norm(x) for x in PROTECTED}
    seen = {_norm(x) for x in PROTECTED}
    stack = list(seen)
    while stack:
        name = stack.pop()
        try:
            reqs = requires(name) or []
        except Exception:
            continue
        for r in reqs:
            if "extra ==" in r:  # optional extra dependency
                continue
            dep = _norm(re.split(r"[ <>=!~;\[\(]", r, 1)[0].strip())
            if dep and dep not in seen:
                seen.add(dep)
                stack.append(dep)
    return seen


async def list_packages() -> list[dict]:
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "pip", "list", "--format=json",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, _ = await proc.communicate()
    try:
        pkgs = json.loads(out.decode())
        pkgs.sort(key=lambda p: p["name"].lower())
        for p in pkgs:
            p["protected"] = _norm(p["name"]) in platform_dependency_closure()
        return pkgs
    except Exception:
        return []


async def install(spec: str) -> dict:
    spec = spec.strip()
    if not spec or not SPEC_RE.match(spec):
        raise PackageError("invalid package specifier")
    return await _run_pip("install", spec)


async def uninstall(name: str) -> dict:
    name = name.strip()
    if not name or not re.match(r"^[A-Za-z0-9._\-]+$", name):
        raise PackageError("invalid package name")
    if _norm(name) in platform_dependency_closure():
        raise PackageError(
            f"refusing to uninstall '{name}' (needed by the bot platform)")
    return await _run_pip("uninstall", "-y", name)
