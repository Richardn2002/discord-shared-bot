"""Platform configuration, loaded from config.json at the repo root."""
from __future__ import annotations

import json
import os
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
ROOT_DIR = APP_DIR.parent
CONFIG_PATH = ROOT_DIR / "config.json"

_DEFAULT_ARCHIVE = {
    "enabled": True,
    "dir": "archive",
    "sync_on_startup": True,
    "startup_restore": True,
    "sync_interval_minutes": 60,
    "database": "archive.db",
    "cache_dir": "archive/cache",
    "cache_max_bytes": 10 * 1024 * 1024 * 1024,
    "download_attachments": True,
    # direct links in message content are fetched when they resolve to a file
    "download_direct_links": True,
    # link-preview / embed images are metadata only; never store their media
    "download_embed_media": False,
    "keep_local_after_upload": False,
    "max_concurrent_downloads": 24,
    "max_concurrent_jobs": 4,
    "max_concurrent_uploads": 2,
    "nextcloud": {
        "enabled": True,
        "url": "https://cloud.richardn.me",
        "username": None,
        "app_password": None,
        "base_path": "discord-archive",
        "verify_tls": True,
        "timeout": 120,
    },
    "backup": {
        "enabled": True,
        "interval_hours": 24,
        "keep": 7,
    },
}

_DEFAULTS = {
    "projects_enabled": True,    # false = archive-only mode: no project event dispatch
    "token": None,               # discord bot token; DISCORD_TOKEN env overrides
    "access_token": None,        # web login token or list of tokens;
                                 # ACCESS_TOKEN(S) env overrides.
                                 # null/empty = no auth (dev only!)
    "listen": "0.0.0.0",         # web listen address
    "port": 8000,                # web listen port
    "domain": "http://localhost:8000",  # public base URL, incl. path prefix if proxied
    "files_dir": "files",        # user content (projects live here)
    "site_title": "\U0001F916 shared-bot",  # shown in the top bar and page titles
    "font_ui": '-apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif',
    "font_code": '"SF Mono", "Cascadia Code", Consolas, monospace',
    "log_level": "INFO",         # DEBUG / INFO / WARNING / ERROR
    "archive": _DEFAULT_ARCHIVE,
}


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _token_values(value) -> list[str]:
    """Normalize access_token config into a list of accepted tokens."""
    if value is None:
        return []
    if isinstance(value, str):
        parts = value.splitlines() if "\n" in value else [value]
    elif isinstance(value, (list, tuple)):
        parts = list(value)
    else:
        parts = [value]
    out = []
    for part in parts:
        token = str(part).strip()
        if token:
            out.append(token)
    return out


class Config:
    def __init__(self) -> None:
        data = dict(_DEFAULTS)
        if CONFIG_PATH.exists():
            try:
                data.update(json.loads(CONFIG_PATH.read_text()))
            except Exception as e:  # keep booting with defaults
                print(f"[config] failed to parse {CONFIG_PATH}: {e}")
        def _bool(value, default=True):
            if value is None:
                return default
            if isinstance(value, str):
                return value.strip().lower() not in ("0", "false", "no", "off", "")
            return bool(value)

        self.projects_enabled = _bool(
            os.environ.get("PROJECTS_ENABLED", data.get("projects_enabled")), True)
        self.token: str | None = os.environ.get("DISCORD_TOKEN") or data["token"]
        # The running platform only uses config.json/env for the bot token.
        # The repo-root DISCORD_TOKEN file is reserved for standalone tooling.
        env_tokens = os.environ.get("ACCESS_TOKENS")
        if env_tokens:
            access_values = [x.strip() for x in env_tokens.split(",")]
        else:
            access_values = (os.environ.get("ACCESS_TOKEN")
                             or data.get("access_token")
                             or data.get("access_tokens"))
        self.access_tokens: list[str] = list(dict.fromkeys(
            _token_values(access_values)))
        # Backwards-compatible singular attribute for any external tooling.
        self.access_token: str | None = (
            self.access_tokens[0] if self.access_tokens else None)
        self.listen: str = data["listen"]
        self.port: int = int(data["port"])
        self.domain: str = data["domain"]
        self.files_dir: Path = (ROOT_DIR / data["files_dir"]).resolve()
        self.site_title: str = data["site_title"]
        self.font_ui: str = data["font_ui"]
        self.font_code: str = data["font_code"]
        self.log_level: str = str(data["log_level"]).upper()
        self.projects_dir: Path = self.files_dir / "projects"
        self.projects_dir.mkdir(parents=True, exist_ok=True)

        self.archive: dict = _deep_merge(_DEFAULT_ARCHIVE, data.get("archive") or {})
        nc = self.archive["nextcloud"]
        nc["app_password"] = (os.environ.get("NEXTCLOUD_APP_PASSWORD")
                              or nc.get("app_password"))
        self.archive_dir: Path = (ROOT_DIR / self.archive["dir"]).resolve()
        self.archive_dir.mkdir(parents=True, exist_ok=True)


CONFIG = Config()
