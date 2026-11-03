#!/usr/bin/env python3
"""Create a consistent SQLite snapshot, compress it, and upload it to Nextcloud."""
from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.archive.nextcloud import NextcloudStore  # noqa: E402
from app.config import CONFIG  # noqa: E402


def _nc_config() -> dict:
    nc = dict(CONFIG.archive.get("nextcloud") or {})
    if not nc.get("app_password"):
        secret = ROOT / ".secrets" / "nextcloud_app_password.json"
        if secret.exists():
            data = json.loads(secret.read_text())
            nc["username"] = nc.get("username") or data.get("loginName")
            nc["url"] = nc.get("url") or data.get("base")
            nc["app_password"] = data.get("appPassword")
    return nc


def make_snapshot(db: Path, out_dir: Path) -> tuple[Path, dict]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    raw = out_dir / f"archive-{stamp}.db"
    if raw.exists():
        raw.unlink()
    src = sqlite3.connect(str(db))
    dst = sqlite3.connect(str(raw))
    with dst:
        src.backup(dst)
    src.close()
    dst.close()

    c = sqlite3.connect(str(raw))
    c.row_factory = sqlite3.Row
    stats = {
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "schema_version": (c.execute("select value from meta where key='schema_version'").fetchone() or [None])[0],
        "messages": c.execute("select count(*) from messages").fetchone()[0],
        "attachments": c.execute("select count(*) from attachments").fetchone()[0],
        "blobs": c.execute("select count(*) from blobs").fetchone()[0],
        "remote_blobs": c.execute("select count(*) from blobs where state='remote'").fetchone()[0],
        "blob_bytes": c.execute("select coalesce(sum(size_bytes),0) from blobs").fetchone()[0],
        "oldest": c.execute("select min(created_at) from messages").fetchone()[0],
        "newest": c.execute("select max(created_at) from messages").fetchone()[0],
    }
    c.close()

    compressed = out_dir / f"archive-{stamp}.db.zst"
    if compressed.exists():
        compressed.unlink()
    subprocess.run(["zstd", "-T0", "-10", "-q", "-f",
                    str(raw), "-o", str(compressed)], check=True)
    raw.unlink()
    manifest = out_dir / f"manifest-{stamp}.json"
    manifest.write_text(json.dumps(stats, indent=2, ensure_ascii=False))
    return compressed, {"stats": stats, "manifest": manifest}


def prune_local(out_dir: Path, keep: int) -> None:
    snaps = sorted(out_dir.glob("archive-*.db.zst"))
    for p in snaps[:-keep] if keep > 0 else []:
        p.unlink(missing_ok=True)
    manifests = sorted(out_dir.glob("manifest-*.json"))
    for p in manifests[:-keep] if keep > 0 else []:
        p.unlink(missing_ok=True)


async def upload(compressed: Path, manifest: Path, nc_cfg: dict) -> None:
    nc = NextcloudStore(nc_cfg)
    base = f"{nc.base_path}/backups"
    async with nc:
        await nc.ensure_dir(base)
        for local, remote in [
            (compressed, f"{base}/{compressed.name}"),
            (manifest, f"{base}/{manifest.name}"),
        ]:
            await nc.upload_file(local, remote)
            print("uploaded", remote)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-upload", action="store_true")
    ap.add_argument("--keep", type=int, default=None)
    args = ap.parse_args()

    db = CONFIG.archive_dir / CONFIG.archive["database"]
    out_dir = CONFIG.archive_dir / "backups"
    keep = args.keep if args.keep is not None else int(CONFIG.archive.get("backup", {}).get("keep", 7))
    compressed, info = make_snapshot(db, out_dir)
    prune_local(out_dir, keep)
    print("snapshot:", compressed, compressed.stat().st_size, "bytes")
    print(json.dumps(info["stats"], indent=2, ensure_ascii=False))
    if not args.no_upload:
        nc_cfg = _nc_config()
        if not nc_cfg.get("app_password"):
            print("Nextcloud credentials missing; snapshot kept locally only")
            return 1
        asyncio.run(upload(compressed, info["manifest"], nc_cfg))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
