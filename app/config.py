"""Platform configuration, loaded from config.json at the repo root."""
from __future__ import annotations

import json
import os
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
ROOT_DIR = APP_DIR.parent
CONFIG_PATH = ROOT_DIR / "config.json"

_DEFAULTS = {
    "token": None,               # discord bot token; DISCORD_TOKEN env overrides
    "access_token": None,        # web UI login token; ACCESS_TOKEN env overrides.
                                 # null/empty = no auth (dev only!)
    "listen": "0.0.0.0",         # web listen address
    "port": 8000,                # web listen port
    "domain": "http://localhost:8000",  # public base URL, incl. path prefix if proxied
    "files_dir": "files",        # user content (projects live here)
    "site_title": "\U0001F916 shared-bot",  # shown in the top bar and page titles
    "font_ui": '-apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif',
    "font_code": '"SF Mono", "Cascadia Code", Consolas, monospace',
    "log_level": "INFO",         # DEBUG / INFO / WARNING / ERROR
}


class Config:
    def __init__(self) -> None:
        data = dict(_DEFAULTS)
        if CONFIG_PATH.exists():
            try:
                data.update(json.loads(CONFIG_PATH.read_text()))
            except Exception as e:  # keep booting with defaults
                print(f"[config] failed to parse {CONFIG_PATH}: {e}")
        self.token: str | None = os.environ.get("DISCORD_TOKEN") or data["token"]
        self.access_token: str | None = (
            os.environ.get("ACCESS_TOKEN") or data["access_token"] or None)
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


CONFIG = Config()
