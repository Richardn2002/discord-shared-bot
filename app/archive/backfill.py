"""Discord archive discovery and backfill/sync runner.

The same runner is used for the initial full import and for later incremental
catch-up. It only reads from Discord; the live bot process is untouched.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path

from .discord_source import DiscordSource
from .ingest import ArchiveIngestor
from .store import ArchiveStore

log = logging.getLogger("archive.backfill")

CHANNEL_SCAN_TYPES = {0, 2, 5, 13}      # text, voice, announcement, stage
THREAD_PARENT_TYPES = {0, 5, 15, 16}


@dataclass
class ScanJob:
    channel_id: int
    guild_id: int
    name: str
    kind: str
    archived: bool = False


class BackfillRunner:
    def __init__(self, store: ArchiveStore, discord: DiscordSource,
                 ingestor: ArchiveIngestor, *, guild_ids: list[int] | None = None,
                 force: bool = False, reconcile: bool = False,
                 prune: bool = True):
        self.store = store
        self.discord = discord
        self.ingestor = ingestor
        self.guild_ids = set(guild_ids or [])
        self.reconcile = reconcile
        self.prune = prune
        # A reconcile pass is always a full scan.
        self.force = force or reconcile
        self.jobs: list[ScanJob] = []
        self.seen_jobs: set[int] = set()
        self.messages_seen = 0
        self.messages_added = 0
        self.messages_deleted = 0
        self.messages_missing = 0
        self.jobs_done = 0
        self.jobs_total = 0

    # -- discovery --------------------------------------------------------
    def _add_job(self, job: ScanJob) -> None:
        if job.channel_id in self.seen_jobs:
            return
        self.seen_jobs.add(job.channel_id)
        self.jobs.append(job)

    async def discover(self) -> list[ScanJob]:
        guilds = await self.discord.guilds()
        for g in guilds:
            gid = int(g["id"])
            if self.guild_ids and gid not in self.guild_ids:
                continue
            self.store.upsert_guild(g)
            self.store.commit()
            channels = await self.discord.guild_channels(gid)
            log.info("guild %s (%s): %d channels", g.get("name"), gid, len(channels))
            for ch in channels:
                cid = int(ch["id"])
                is_thread = ch.get("type") in (10, 11, 12)
                self.store.upsert_channel(ch, gid, is_thread=is_thread)
                if ch.get("type") in CHANNEL_SCAN_TYPES:
                    self._add_job(ScanJob(cid, gid, ch.get("name") or str(cid), "channel"))
            self.store.commit()

            # Active threads (all parents).
            active = await self.discord.active_threads(gid)
            for t in active:
                self._upsert_thread(t, gid, archived=False)
                self._add_job(ScanJob(int(t["id"]), gid, t.get("name") or str(t["id"]),
                                      "thread", archived=False))
            log.info("guild %s: %d active threads", g.get("name"), len(active))

            # Archived threads for every parent that can host them.
            for ch in channels:
                if ch.get("type") not in THREAD_PARENT_TYPES:
                    continue
                parent_id = int(ch["id"])
                for private in (False, True):
                    await self._discover_archived_threads(parent_id, gid, ch, private)

        self.jobs_total = len(self.jobs)
        log.info("discovered %d channels/threads to scan", self.jobs_total)
        return self.jobs

    def _upsert_thread(self, t: dict, guild_id: int, archived: bool) -> None:
        self.store.upsert_channel(t, guild_id, is_thread=True, archived=archived)
        self.store.commit()

    async def _discover_archived_threads(self, parent_id: int, guild_id: int,
                                         parent: dict, private: bool) -> None:
        before = None
        pages = 0
        while True:
            threads = await self.discord.archived_threads(parent_id, private=private,
                                                          before=before)
            if not threads:
                break
            pages += 1
            for t in threads:
                self._upsert_thread(t, guild_id, archived=True)
                self._add_job(ScanJob(int(t["id"]), guild_id,
                                      t.get("name") or str(t["id"]), "thread",
                                      archived=True))
            if len(threads) < 100:
                break
            stamps = [((t.get("thread_metadata") or {}).get("archive_timestamp"))
                      for t in threads]
            stamps = [s for s in stamps if s]
            if not stamps:
                break
            new_before = min(stamps)
            if new_before == before:
                break
            before = new_before
        if pages:
            kind = "private" if private else "public"
            log.info("parent %s: archived %s thread pages=%d",
                     parent.get("name"), kind, pages)

    # -- scanning ---------------------------------------------------------
    async def run(self) -> dict:
        if not self.jobs:
            await self.discover()
        started = time.time()
        max_jobs = max(1, int(self.ingestor.cfg.get("max_concurrent_jobs", 4)))
        sem = asyncio.Semaphore(max_jobs)
        total = len(self.jobs)
        self.jobs_total = total
        log.info("scanning %d jobs with concurrency %d", total, max_jobs)

        async def one(job: ScanJob) -> None:
            async with sem:
                log.info("%s %s (%s)", job.kind, job.name, job.channel_id)
                try:
                    await self.scan_job(job)
                    self.store.update_backfill(
                        job.channel_id, guild_id=job.guild_id, name=job.name,
                        kind=job.kind, status="complete", error=None)
                except Exception as e:
                    log.exception("scan failed for %s", job.name)
                    self.store.update_backfill(
                        job.channel_id, guild_id=job.guild_id, name=job.name,
                        kind=job.kind, status="error", error=str(e)[:2000])
                self.jobs_done += 1
                if self.jobs_done % 10 == 0:
                    log.info("progress: %d/%d jobs, %d messages, %d attachments",
                             self.jobs_done, total, self.messages_seen,
                             self.ingestor.download_count)

        await asyncio.gather(*(one(j) for j in self.jobs))
        await self.ingestor.drain()
        await self.ingestor.process_pending_attachments()
        await self.ingestor.drain()
        await self.ingestor.retry_pending_uploads(limit=10_000)
        elapsed = time.time() - started
        result = {
            "jobs": total,
            "reconcile": self.reconcile,
            "prune_missing": self.prune if self.reconcile else False,
            "messages_seen": self.messages_seen,
            "messages_added": self.messages_added,
            "messages_missing": self.messages_missing,
            "messages_deleted": self.messages_deleted,
            "attachments_downloaded": self.ingestor.download_count,
            "attachments_uploaded": self.ingestor.upload_count,
            "attachment_errors": self.ingestor.error_count,
            "elapsed_seconds": round(elapsed, 1),
            "stats": self.store.stats(),
        }
        log.info("backfill done: %s", json.dumps(result, ensure_ascii=False))
        return result

    async def scan_job(self, job: ScanJob) -> None:
        state = self.store.get_backfill(job.channel_id)
        # A normal run is incremental: resume from the last cursor even when the
        # previous run completed. --force re-scans from the beginning.
        cursor = int(state["cursor"]) if state and not self.force else 0
        seen = int(state["messages_seen"]) if state and not self.force else 0
        added = int(state["messages_added"]) if state and not self.force else 0
        atts = int(state["attachments"]) if state and not self.force else 0
        self.store.update_backfill(
            job.channel_id, guild_id=job.guild_id, name=job.name, kind=job.kind,
            status="running", cursor=cursor, messages_seen=seen,
            messages_added=added, attachments=atts, error=None)

        # Record the scan start before the first request. Rows touched by live
        # events after this point are not candidates for delete detection.
        scan_started = time.time()
        seen_ids: set[int] = set()

        # Snapshot the newest message so a busy channel can still finish.
        latest, latest_status = await self.discord.messages_page(
            job.channel_id, limit=1)
        if latest_status in (403, 404):
            if self.reconcile:
                log.warning("  %s: inaccessible (%s); skipping reconcile",
                            job.name, latest_status)
                self.store.update_backfill(
                    job.channel_id, status="skipped",
                    error=f"channel inaccessible ({latest_status})")
                return
            end_id = None
        else:
            end_id = int(latest[0]["id"]) if latest else None

        page_count = 0
        while True:
            page, status = await self.discord.messages_page(
                job.channel_id, after=cursor, limit=100)
            if status in (403, 404):
                if self.reconcile:
                    log.warning("  %s: inaccessible (%s); skipping reconcile",
                                job.name, status)
                    self.store.update_backfill(
                        job.channel_id, status="skipped",
                        error=f"channel inaccessible ({status})")
                    return
                break
            if not page:
                break
            # Discord returns ascending by id when `after` is used.
            for m in page:
                if self.reconcile:
                    seen_ids.add(int(m["id"]))
                info = await self.ingestor.ingest_message(
                    m, guild_id=job.guild_id, channel_id=job.channel_id, commit=False)
                seen += 1
                if info.get("inserted"):
                    added += 1
                atts += len(info.get("attachments") or [])
                self.messages_seen += 1
                if info.get("inserted"):
                    self.messages_added += 1
            self.store.commit()
            cursor = max(int(m["id"]) for m in page)
            page_count += 1
            self.store.update_backfill(
                job.channel_id, status="running", cursor=cursor,
                messages_seen=seen, messages_added=added, attachments=atts)
            if page_count % 10 == 0:
                log.info("  %s: %d messages, cursor %s", job.name, seen, cursor)
            if len(page) < 100:
                break
            if end_id is not None and cursor >= end_id:
                break

        if self.reconcile:
            archived_ids = self.store.message_ids_for_channel(
                job.channel_id, end_id, scan_started)
            missing = [mid for mid in archived_ids if mid not in seen_ids]
            self.messages_missing += len(missing)
            if self.prune:
                for mid in missing:
                    await self.ingestor.delete_message(mid)
                    self.messages_deleted += 1
                if missing:
                    log.info("  %s: pruned %d missing messages",
                             job.name, len(missing))
            elif missing:
                log.info("  %s: %d missing messages (prune disabled)",
                         job.name, len(missing))

        self.store.update_backfill(
            job.channel_id, status="complete", cursor=cursor,
            messages_seen=seen, messages_added=added, attachments=atts)
        log.info("  %s: complete, %d messages, %d attachments",
                 job.name, seen, atts)
