#!/usr/bin/env python3
"""Manually run a full archive reconciliation.

Unlike the normal incremental sync, this re-scans every selected channel/thread
from the beginning. It catches edits to ancient messages that happened while the
bot was offline. By default it also removes archived messages that Discord no
longer returns (missed deletes); pass --no-prune to keep them.

Examples:
    ./venv/bin/python tools/archive_reconcile.py --only-channel 123
    ./venv/bin/python tools/archive_reconcile.py --guild 1107319501843873935
    ./venv/bin/python tools/archive_reconcile.py --all --no-prune

Run this manually; it is intentionally not part of the hourly incremental sync.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.archive import ArchiveStore, DiscordSource, ArchiveIngestor, NextcloudStore  # noqa: E402
from app.archive.backfill import BackfillRunner  # noqa: E402
from app.config import CONFIG  # noqa: E402


def _nextcloud_config(cfg: dict) -> dict:
    nc = dict(cfg.get("nextcloud") or {})
    if not nc.get("app_password"):
        secret = ROOT / ".secrets" / "nextcloud_app_password.json"
        if secret.exists():
            data = json.loads(secret.read_text())
            nc["username"] = nc.get("username") or data.get("loginName")
            nc["url"] = nc.get("url") or data.get("base")
            nc["app_password"] = data.get("appPassword")
    return nc


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--guild", type=int, action="append", default=[],
                    help="limit to a guild id (repeatable)")
    ap.add_argument("--only-channel", type=int, action="append", default=[],
                    help="reconcile only these channel/thread ids (repeatable)")
    ap.add_argument("--all", action="store_true",
                    help="reconcile every discovered channel/thread (required "
                         "unless --guild/--only-channel is given)")
    ap.add_argument("--no-prune", action="store_true",
                    help="detect missing messages but do not delete them")
    ap.add_argument("--text-only", action="store_true",
                    help="do not download attachments (metadata still stored)")
    ap.add_argument("--no-nextcloud", action="store_true",
                    help="keep attachment bytes local instead of uploading")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    if not args.all and not args.guild and not args.only_channel:
        ap.error("give --all, --guild, or --only-channel to avoid an accidental full scan")
    if args.all and (args.guild or args.only_channel):
        ap.error("--all cannot be combined with --guild/--only-channel")

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    cfg = dict(CONFIG.archive)
    cfg["_archive_dir"] = str(CONFIG.archive_dir)
    if args.text_only:
        cfg["download_attachments"] = False
        cfg["download_direct_links"] = False

    store = ArchiveStore(CONFIG.archive_dir / CONFIG.archive["database"])
    nc = None
    nc_cfg = _nextcloud_config(cfg)
    if not args.no_nextcloud and nc_cfg.get("enabled") and nc_cfg.get("app_password"):
        nc = NextcloudStore(nc_cfg)
    elif not args.no_nextcloud:
        logging.warning("Nextcloud credentials not configured; keeping blobs local")

    async with DiscordSource() as discord:
        ingestor = ArchiveIngestor(store, discord, cfg, nc)
        async with ingestor:
            runner = BackfillRunner(
                store, discord, ingestor,
                guild_ids=args.guild,
                reconcile=True,
                prune=not args.no_prune,
            )
            await runner.discover()
            if args.only_channel:
                only = set(args.only_channel)
                runner.jobs = [j for j in runner.jobs if j.channel_id in only]
                runner.jobs_total = len(runner.jobs)
                if not runner.jobs:
                    logging.error("no discovered channels/threads match --only-channel")
                    return 2
            result = await runner.run()
            print(json.dumps(result, indent=2, ensure_ascii=False))
    store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
