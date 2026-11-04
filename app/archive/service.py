"""Live archive integration for the always-on Discord bot.

The service owns one ArchiveStore/ArchiveIngestor and consumes gateway events
through a small ordered queue. Startup/periodic catch-up uses the same
BackfillRunner as the CLI, so there is only one archiving code path.
"""
from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

from ..config import CONFIG
from .backfill import BackfillRunner
from .discord_source import DiscordSource
from .ingest import ArchiveIngestor
from .nextcloud import NextcloudStore
from .store import SCHEMA_VERSION, ArchiveStore, _iso_ts

log = logging.getLogger("archive.service")


def _nextcloud_config(cfg: dict) -> dict:
    # The running platform takes Nextcloud credentials from config.json only;
    # the standalone tools still support the .secrets fallback.
    return dict(cfg.get("nextcloud") or {})


def _guild_payload(guild) -> dict:
    icon = getattr(guild, "icon", None)
    return {
        "id": guild.id,
        "name": guild.name,
        "icon": getattr(icon, "key", str(icon)) if icon else None,
    }


def _channel_payload(channel, guild_id: int | None) -> dict:
    ctype = getattr(channel, "type", None)
    ctype = getattr(ctype, "value", ctype)
    try:
        ctype = int(ctype)
    except Exception:
        pass
    meta = getattr(channel, "thread_metadata", None)
    archived = bool(getattr(channel, "archived", False))
    if meta is not None:
        archived = bool(getattr(meta, "archived", archived))
    return {
        "id": channel.id,
        "guild_id": guild_id,
        "name": getattr(channel, "name", None),
        "type": ctype,
        "parent_id": getattr(channel, "parent_id", None),
        "position": getattr(channel, "position", None),
        "thread_metadata": {
            "archived": archived,
            "archive_timestamp": (
                meta.archive_timestamp.isoformat()
                if meta is not None and getattr(meta, "archive_timestamp", None)
                else None
            ),
        } if meta is not None else None,
        "archived": archived,
    }


def _message_payload(message) -> dict:
    """Convert a discord.py Message into the REST-ish shape used by the store."""
    author = message.author
    avatar = getattr(author, "avatar", None)
    guild = message.guild
    channel = message.channel
    ref = getattr(message, "reference", None)
    attachments = []
    for a in getattr(message, "attachments", None) or []:
        attachments.append({
            "id": str(a.id),
            "filename": a.filename,
            "content_type": getattr(a, "content_type", None),
            "size": getattr(a, "size", None),
            "width": getattr(a, "width", None),
            "height": getattr(a, "height", None),
            "url": a.url,
            "proxy_url": getattr(a, "proxy_url", None),
        })
    embeds = []
    for e in getattr(message, "embeds", None) or []:
        try:
            embeds.append(e.to_dict())
        except Exception:
            pass
    stickers = []
    for s in getattr(message, "stickers", None) or []:
        try:
            stickers.append(s.to_dict())
        except Exception:
            stickers.append({"id": str(getattr(s, "id", "")),
                             "name": getattr(s, "name", None)})
    return {
        "id": message.id,
        "guild_id": guild.id if guild else None,
        "channel_id": channel.id,
        "content": getattr(message, "content", "") or "",
        "timestamp": message.created_at.isoformat() if message.created_at else None,
        "edited_timestamp": (message.edited_at.isoformat()
                             if getattr(message, "edited_at", None) else None),
        "type": getattr(message.type, "value", message.type),
        "pinned": bool(getattr(message, "pinned", False)),
        "author": {
            "id": author.id,
            "username": getattr(author, "name", None),
            "global_name": getattr(author, "global_name", None),
            "display_name": getattr(author, "display_name", None),
            "avatar": getattr(avatar, "key", str(avatar)) if avatar else None,
            "bot": bool(getattr(author, "bot", False)),
        },
        "attachments": attachments,
        "embeds": embeds,
        "stickers": stickers,
        "message_reference": ({
            "message_id": ref.message_id,
            "channel_id": getattr(ref, "channel_id", None),
        } if ref is not None and getattr(ref, "message_id", None) else None),
        "_channel": _channel_payload(channel, guild.id if guild else None),
        "_guild": _guild_payload(guild) if guild else None,
    }


def _local_db_state(path: Path) -> dict:
    state = {"exists": path.exists(), "mtime": 0.0, "schema_version": None,
             "messages": 0, "newest_ts": 0.0, "error": None}
    if not path.exists():
        return state
    state["mtime"] = path.stat().st_mtime
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
        try:
            row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
            state["schema_version"] = int(row[0]) if row and row[0] is not None else None
            state["messages"] = int(conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0])
            state["newest_ts"] = float(conn.execute(
                "SELECT COALESCE(MAX(created_ts),0) FROM messages").fetchone()[0])
        finally:
            conn.close()
    except Exception as e:
        state["error"] = str(e)
    return state


def _validate_restored_db(path: Path) -> dict:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30)
    try:
        check = conn.execute("PRAGMA quick_check").fetchone()[0]
        if check != "ok":
            raise RuntimeError(f"sqlite quick_check failed: {check}")
        row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        schema = int(row[0]) if row and row[0] is not None else None
        if schema != SCHEMA_VERSION:
            raise RuntimeError(
                f"snapshot schema_version={schema}, expected {SCHEMA_VERSION}")
        return {
            "schema_version": schema,
            "messages": int(conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]),
            "newest_ts": float(conn.execute(
                "SELECT COALESCE(MAX(created_ts),0) FROM messages").fetchone()[0]),
        }
    finally:
        conn.close()


class ArchiveService:
    """Owns the live archive pipeline. Safe to import when archiving is off."""

    def __init__(self):
        self.cfg: dict = CONFIG.archive
        self.enabled: bool = bool(self.cfg.get("enabled", False))
        self.store: ArchiveStore | None = None
        self.discord: DiscordSource | None = None
        self.nextcloud: NextcloudStore | None = None
        self.ingestor: ArchiveIngestor | None = None
        self.queue: asyncio.Queue[dict | None] = asyncio.Queue()
        self.event_task: asyncio.Task | None = None
        self.sync_task: asyncio.Task | None = None
        self.periodic_task: asyncio.Task | None = None
        self.sync_lock = asyncio.Lock()
        self.running = False
        self.syncing = False
        self.last_sync_ts: float | None = None
        self.last_sync_result: dict | None = None
        self.last_sync_error: str | None = None

    async def _restore_newer_remote_backup(self, db_path: Path) -> bool:
        """Before opening the local DB, replace it with a newer Nextcloud
        snapshot if one exists. This lets a dev/VPS instance that was offline
        catch up from the other instance's latest backup without a full scan."""
        if not self.cfg.get("startup_restore", True):
            return False
        if self.nextcloud is None:
            return False
        backup_dir = f"{self.nextcloud.base_path}/backups"
        local = _local_db_state(db_path)
        try:
            async with self.nextcloud:
                entries = await self.nextcloud.list_dir(backup_dir)
                manifests = sorted(
                    (e for e in entries
                     if not e.get("is_dir") and e["name"].startswith("manifest-")
                     and e["name"].endswith(".json")),
                    key=lambda e: e["name"], reverse=True)
                if not manifests:
                    log.info("archive startup restore: no remote backups found")
                    return False
                for entry in manifests:
                    try:
                        raw_manifest = await self.nextcloud.read_bytes(
                            f"{backup_dir}/{entry['name']}")
                        manifest = json.loads(raw_manifest.decode("utf-8"))
                    except Exception as e:
                        log.warning("archive startup restore: cannot read %s: %s",
                                    entry["name"], e)
                        continue
                    stats = manifest.get("stats") if isinstance(manifest.get("stats"), dict) else manifest
                    remote_created = _iso_ts(stats.get("created_at")) or 0.0
                    remote_newest = _iso_ts(stats.get("newest")) or 0.0
                    remote_messages = int(stats.get("messages") or 0)
                    future = (not local["exists"] or local["messages"] == 0
                              or remote_newest > local["newest_ts"] + 1
                              or (remote_created > local["mtime"] + 60
                                  and remote_messages >= local["messages"]))
                    if not future:
                        log.info("archive startup restore: local db is current "
                                 "(remote %s)", entry["name"])
                        return False
                    snapshot = stats.get("snapshot") or entry["name"].replace(
                        "manifest-", "archive-").replace(".json", ".db.zst")
                    remote_snapshot = f"{backup_dir}/{snapshot}"
                    tmp_dir = CONFIG.archive_dir / "tmp"
                    tmp_dir.mkdir(parents=True, exist_ok=True)
                    db_path.parent.mkdir(parents=True, exist_ok=True)
                    token = uuid.uuid4().hex
                    compressed = tmp_dir / f"restore-{token}.db.zst"
                    # Keep the decompressed file next to the target DB so the
                    # final os.replace() is atomic (not cross-filesystem).
                    restored = db_path.with_name(f".{db_path.name}.restore-{token}")
                    try:
                        log.info("archive startup restore: downloading %s", remote_snapshot)
                        await self.nextcloud.download_to(remote_snapshot, compressed)
                        subprocess.run(
                            ["zstd", "-d", "-q", "-f", str(compressed), "-o", str(restored)],
                            check=True, capture_output=True,
                        )
                        info = _validate_restored_db(restored)
                        if (local["exists"] and local["messages"] > 0
                                and info["newest_ts"] <= local["newest_ts"] + 1):
                            # Same newest message is acceptable only when the
                            # remote snapshot was taken after the local DB was
                            # last touched (edits/deletes/prunes may differ).
                            if not (remote_created > local["mtime"] + 60
                                    and info["messages"] >= local["messages"]):
                                log.info("archive startup restore: snapshot is not newer "
                                         "than local db; keeping local")
                                return False
                        rollback = db_path.with_name(db_path.name + ".pre-restore")
                        moved_old = False
                        if db_path.exists():
                            db_path.replace(rollback)
                            moved_old = True
                        for suffix in ("-wal", "-shm"):
                            db_path.with_name(db_path.name + suffix).unlink(missing_ok=True)
                        try:
                            restored.replace(db_path)
                        except Exception:
                            # Roll back if the final swap fails for any reason.
                            if moved_old and rollback.exists() and not db_path.exists():
                                rollback.replace(db_path)
                            raise
                        log.info("archive startup restore: restored %s (%s messages, "
                                 "newest_ts=%s)", snapshot, info["messages"],
                                 info["newest_ts"])
                        return True
                    except FileNotFoundError:
                        log.warning("archive startup restore: zstd is not installed; "
                                    "cannot restore remote snapshots")
                        return False
                    except Exception as e:
                        log.warning("archive startup restore: failed for %s: %s",
                                    snapshot, e)
                        continue
                    finally:
                        compressed.unlink(missing_ok=True)
                        restored.unlink(missing_ok=True)
                        for suffix in ("-wal", "-shm"):
                            Path(str(restored) + suffix).unlink(missing_ok=True)
        except Exception as e:
            log.warning("archive startup restore failed: %s", e)
        return False

    async def start(self) -> None:
        if not self.enabled or self.running:
            return
        token = CONFIG.token
        if not token:
            log.warning("archive enabled but no Discord token; archive service not started")
            return

        db_path = CONFIG.archive_dir / str(self.cfg.get("database", "archive.db"))
        cfg = dict(self.cfg)
        cfg["_archive_dir"] = str(CONFIG.archive_dir)

        nc_cfg = _nextcloud_config(cfg)
        if nc_cfg.get("enabled") and nc_cfg.get("url"):
            try:
                self.nextcloud = NextcloudStore(nc_cfg)
            except Exception as e:
                log.warning("Nextcloud not available (%s); keeping blobs local", e)
                self.nextcloud = None
        else:
            self.nextcloud = None

        # Restore before ArchiveStore opens/creates the local DB, otherwise we
        # would overwrite an open SQLite connection.
        await self._restore_newer_remote_backup(db_path)
        self.store = ArchiveStore(db_path)

        self.discord = DiscordSource(token)
        await self.discord.__aenter__()
        self.ingestor = ArchiveIngestor(self.store, self.discord, cfg, self.nextcloud)
        await self.ingestor.__aenter__()

        self.running = True
        self.event_task = asyncio.create_task(self._event_worker())
        if bool(self.cfg.get("sync_on_startup", True)):
            self.sync_task = asyncio.create_task(self._run_sync(reason="startup"))
        interval = float(self.cfg.get("sync_interval_minutes", 0) or 0)
        if interval > 0:
            self.periodic_task = asyncio.create_task(self._periodic(interval))
        log.info("archive service started (db=%s, nextcloud=%s)",
                 db_path, bool(self.nextcloud))

    async def stop(self) -> None:
        if not self.running:
            return
        self.running = False
        for task in (self.periodic_task, self.sync_task):
            if task and not task.done():
                task.cancel()
        if self.event_task and not self.event_task.done():
            await self.queue.put(None)
            try:
                await asyncio.wait_for(self.queue.join(), timeout=15)
            except asyncio.TimeoutError:
                self.event_task.cancel()
        for task in (self.periodic_task, self.sync_task, self.event_task):
            if task:
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
        self.periodic_task = self.sync_task = self.event_task = None
        if self.ingestor:
            await self.ingestor.__aexit__(None, None, None)
            self.ingestor = None
        if self.discord:
            await self.discord.__aexit__(None, None, None)
            self.discord = None
        if self.store:
            self.store.close()
            self.store = None
        log.info("archive service stopped")

    def status(self) -> dict:
        out = {
            "enabled": self.enabled,
            "running": self.running,
            "syncing": self.syncing,
            "last_sync_ts": self.last_sync_ts,
            "last_sync_error": self.last_sync_error,
        }
        if self.store:
            try:
                out["stats"] = self.store.stats()
            except Exception:
                pass
        return out

    # -- gateway event entry points --------------------------------------
    def enqueue_message(self, message) -> None:
        if not self.running or message.guild is None:
            return
        try:
            payload = _message_payload(message)
        except Exception:
            log.exception("could not normalize message %s", getattr(message, "id", "?"))
            return
        self.queue.put_nowait({"type": "message", "message": payload})

    def enqueue_delete(self, message_id: int, channel_id: int | None = None) -> None:
        if not self.running:
            return
        self.queue.put_nowait({"type": "delete", "id": int(message_id),
                               "channel_id": channel_id})

    def enqueue_delete_many(self, message_ids: list[int]) -> None:
        if not self.running:
            return
        for mid in message_ids:
            self.queue.put_nowait({"type": "delete", "id": int(mid)})

    async def _event_worker(self) -> None:
        while True:
            event = await self.queue.get()
            try:
                if event is None:
                    break
                if event["type"] == "delete":
                    await self.ingestor.delete_message(event["id"])
                elif event["type"] == "message":
                    msg = event["message"]
                    channel = msg.get("_channel")
                    guild = msg.get("_guild")
                    if guild:
                        self.store.upsert_guild(guild)
                    if channel:
                        ctype = channel.get("type")
                        self.store.upsert_channel(
                            channel,
                            guild_id=msg.get("guild_id"),
                            is_thread=ctype in (10, 11, 12),
                            archived=bool(channel.get("archived")),
                        )
                    await self.ingestor.ingest_message(
                        msg, guild_id=msg.get("guild_id"),
                        channel_id=msg.get("channel_id"),
                    )
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("archive event failed: %r", event)
            finally:
                self.queue.task_done()

    # -- catch-up sync ----------------------------------------------------
    async def _run_sync(self, reason: str = "manual") -> dict | None:
        if not self.running or not self.ingestor or not self.discord:
            return None
        async with self.sync_lock:
            self.syncing = True
            started = time.time()
            try:
                log.info("archive %s sync starting", reason)
                runner = BackfillRunner(self.store, self.discord, self.ingestor)
                await runner.discover()
                result = await runner.run()
                result["reason"] = reason
                result["elapsed_seconds"] = round(time.time() - started, 1)
                self.last_sync_ts = time.time()
                self.last_sync_result = result
                self.last_sync_error = None
                log.info("archive %s sync finished: %s", reason,
                         json.dumps(result, ensure_ascii=False))
                return result
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.last_sync_error = str(e)
                self.last_sync_ts = time.time()
                log.exception("archive %s sync failed", reason)
                return None
            finally:
                self.syncing = False

    async def _periodic(self, interval_minutes: float) -> None:
        while True:
            await asyncio.sleep(max(60.0, interval_minutes * 60.0))
            await self._run_sync(reason="periodic")

    def trigger_sync(self, reason: str = "manual") -> asyncio.Task | None:
        if not self.running:
            return None
        return asyncio.create_task(self._run_sync(reason=reason))


archive_service = ArchiveService()
