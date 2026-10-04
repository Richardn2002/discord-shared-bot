"""Minimal .env parse/dump (KEY=VALUE lines, # comments, optional quotes,
optional `export ` prefix). Shared by the backend (secret store endpoints)
and worker processes (the secret_get primitive).
"""
from __future__ import annotations

import re

KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def parse_env(text: str) -> dict[str, str]:
    data: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            inner = value[1:-1]
            if value[0] == '"':
                inner = inner.replace('\\"', '"').replace('\\n', '\n').replace('\\\\', '\\')
            value = inner
        if KEY_RE.match(key):
            data[key] = value
    return data


def dump_env(data: dict[str, str]) -> str:
    lines = ["# secrets for this project — this file is gitignored; keep it that way."]
    for key in sorted(data):
        value = str(data[key])
        if re.search(r"[\s#\"'\\$]|^$", value):
            value = '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
        lines.append(f"{key}={value}")
    return "\n".join(lines) + "\n"
