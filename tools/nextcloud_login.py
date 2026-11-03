#!/usr/bin/env python3
"""Nextcloud Login Flow v2 helper.

`start` asks Nextcloud for a login URL, saves the polling token under
.secrets/, and prints the URL for the human to open. `poll` waits for the
login to finish and stores the resulting app password under .secrets/.

No password/token is printed. The app password is written to
.secrets/nextcloud_app_password.json (mode 0600) for the archive tooling.

Usage:
    ./venv/bin/python tools/nextcloud_login.py start [base_url]
    ./venv/bin/python tools/nextcloud_login.py poll  [base_url]
"""
from __future__ import annotations

import json
import os
import stat
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SECRETS = ROOT / ".secrets"
PENDING = SECRETS / "nextcloud_login_pending.json"
CREDS = SECRETS / "nextcloud_app_password.json"
DEFAULT_BASE = "https://cloud.richardn.me"


def _post_json(url: str, payload: dict, timeout: float = 30) -> dict:
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _poll_json(url: str, token: str, timeout: float = 30) -> dict:
    # Login Flow v2 polls with a form-encoded POST, not GET.
    body = urllib.parse.urlencode({"token": token}).encode()
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded",
                 "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _write_private(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2))
    os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)
    os.replace(tmp, path)


def _base(arg: str | None) -> str:
    return (arg or os.environ.get("NEXTCLOUD_URL") or DEFAULT_BASE).rstrip("/")


def start(base: str) -> int:
    endpoint = base + "/index.php/login/v2"
    try:
        data = _post_json(endpoint, {})
    except urllib.error.HTTPError as e:
        print(f"Nextcloud login flow failed: HTTP {e.code} {e.reason}")
        print("The server may have login flow v2 disabled or may need a trailing path fix.")
        return 1
    except Exception as e:
        print(f"Nextcloud login flow failed: {e}")
        return 1

    poll = data.get("poll") or {}
    token = poll.get("token")
    if not token:
        print("Unexpected response (no poll token).")
        return 1
    _write_private(PENDING, {"base": base, "poll": poll, "created": time.time()})
    print("Open this URL in your browser and log in:\n")
    print(data.get("login"))
    print("\nAfter you have approved the login, run:")
    print(f"  ./venv/bin/python tools/nextcloud_login.py poll {base}")
    return 0


def poll(base: str) -> int:
    if not PENDING.exists():
        print("No pending login flow. Run the 'start' command first.")
        return 1
    pending = json.loads(PENDING.read_text())
    poll_info = pending["poll"]
    endpoint = poll_info["endpoint"]
    token = poll_info["token"]
    print("Waiting for Nextcloud login approval...")
    deadline = time.time() + 10 * 60
    while time.time() < deadline:
        try:
            data = _poll_json(endpoint, token)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                time.sleep(2)
                continue
            print(f"Poll failed: HTTP {e.code} {e.reason}")
            return 1
        except Exception as e:
            print(f"Poll error: {e}")
            time.sleep(2)
            continue

        if data.get("appPassword"):
            _write_private(CREDS, {
                "base": base,
                "server": data.get("server"),
                "loginName": data.get("loginName"),
                "appPassword": data.get("appPassword"),
                "created": time.time(),
            })
            PENDING.unlink(missing_ok=True)
            print(f"Nextcloud app password saved to {CREDS.relative_to(ROOT)}")
            print(f"account: {data.get('loginName')} @ {data.get('server')}")
            return 0
        time.sleep(2)
    print("Timed out waiting for login.")
    return 1


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] not in ("start", "poll"):
        print(__doc__)
        return 2
    base = _base(sys.argv[2] if len(sys.argv) > 2 else None)
    return start(base) if sys.argv[1] == "start" else poll(base)


if __name__ == "__main__":
    raise SystemExit(main())
