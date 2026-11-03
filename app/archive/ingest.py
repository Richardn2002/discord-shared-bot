"""Message ingestion + attachment pipeline shared by backfill and live sync."""
from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import shutil
import time
import uuid
from pathlib import Path

from .discord_source import DiscordSource
from .nextcloud import NextcloudError, NextcloudStore
from .store import ArchiveStore, _safe_filename

log = logging.getLogger("archive.ingest")

URL_RE = re.compile(r"https?://[^\s<>()\]\[{}]+", re.IGNORECASE)
FILE_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".svg",
    ".pdf", ".zip", ".7z", ".rar", ".tar", ".gz", ".bz2", ".xz", ".zst",
    ".mp3", ".ogg", ".opus", ".wav", ".flac", ".m4a", ".aac",
    ".mp4", ".webm", ".mov", ".mkv", ".avi", ".m4v",
    ".txt", ".md", ".csv", ".json", ".xml", ".yaml", ".yml",
    ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".epub",
    ".py", ".sh", ".ipynb", ".sql", ".toml", ".ini",
}
DIRECT_CONTENT_TYPES = (
    "image/", "video/", "audio/", "application/pdf", "application/zip",
    "application/x-7z-compressed", "application/x-rar-compressed",
    "application/gzip", "application/x-tar", "application/octet-stream",
    "text/plain", "text/markdown", "text/csv",
)


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _guess_filename(url: str) -> str:
    from urllib.parse import unquote, urlparse
    path = urlparse(url).path
    name = unquote(path.rsplit("/", 1)[-1]) or "download"
    name = re.sub(r"[?#].*$", "", name)
    return _safe_filename(name)


class ArchiveIngestor:
    def __init__(self, store: ArchiveStore, discord: DiscordSource | None,
                 config: dict, nextcloud: NextcloudStore | None = None):
        self.store = store
        self.discord = discord
        self.cfg = config
        self.nextcloud = nextcloud
        self.archive_dir = Path(config["_archive_dir"])
        self.cache_dir = self.archive_dir / "cache" / "blobs"
        self.tmp_dir = self.archive_dir / "tmp"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        self.queue: asyncio.Queue[str] = asyncio.Queue()
        self.workers: list[asyncio.Task] = []
        self._queued: set[str] = set()
        self.download_count = 0
        self.upload_count = 0
        self.error_count = 0
        self._upload_locks: dict[str, asyncio.Lock] = {}

    async def __aenter__(self):
        if self.nextcloud:
            await self.nextcloud.__aenter__()
        n = max(1, int(self.cfg.get("max_concurrent_downloads", 3)))
        self.workers = [asyncio.create_task(self._worker(i)) for i in range(n)]
        return self

    async def __aexit__(self, *exc):
        await self.drain()
        for w in self.workers:
            w.cancel()
        if self.workers:
            await asyncio.gather(*self.workers, return_exceptions=True)
        self.workers.clear()
        if self.nextcloud:
            await self.nextcloud.__aexit__(*exc)

    def _cache_path(self, sha: str) -> Path:
        return self.cache_dir / sha[:2] / sha[2:4] / sha

    def _remote_path(self, sha: str, filename: str | None) -> str:
        if self.nextcloud:
            return self.nextcloud.remote_attachment_path(sha, filename)
        return self.store.blob_path(sha, filename)

    def submit_attachment(self, attachment_id: str) -> None:
        if attachment_id in self._queued:
            return
        self._queued.add(attachment_id)
        self.queue.put_nowait(attachment_id)

    async def drain(self) -> None:
        await self.queue.join()

    async def _worker(self, index: int) -> None:
        while True:
            att_id = await self.queue.get()
            self._queued.discard(att_id)
            try:
                await self.process_attachment(att_id)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.error_count += 1
                log.exception("attachment %s failed", att_id)
                self.store.set_attachment_error(att_id, str(e))
            finally:
                self.queue.task_done()

    async def ingest_message(self, message: dict, guild_id=None,
                             channel_id=None, commit: bool = True) -> dict:
        info = self.store.upsert_message(message, guild_id=guild_id,
                                         channel_id=channel_id, commit=commit)
        if info.get("released_blobs") and self.nextcloud:
            for blob in info["released_blobs"]:
                remote = blob.get("remote_path")
                if remote:
                    try:
                        await self.nextcloud.delete(remote)
                    except Exception as e:
                        log.warning("could not delete remote blob %s: %s", remote, e)
        if not info.get("deleted") and not info.get("stale") and \
                self.cfg.get("download_attachments", True):
            for a in message.get("attachments") or []:
                if a.get("id") and a.get("url"):
                    self.submit_attachment(str(a["id"]))
            if self.cfg.get("download_direct_links", True):
                await self._ingest_direct_links(message, guild_id, channel_id)
        return info

    async def _ingest_direct_links(self, message: dict, guild_id, channel_id) -> None:
        content = message.get("content") or ""
        urls = {u.rstrip(".,;:!?)\"'") for u in URL_RE.findall(content)}
        if not urls:
            return
        for url in urls:
            try:
                if await self._is_direct_file(url):
                    self._upsert_link_attachment(message, url, guild_id, channel_id)
            except Exception as e:
                log.debug("direct link check failed for %s: %s", url, e)

    async def _is_direct_file(self, url: str) -> bool:
        if not self.discord:
            return False
        from urllib.parse import urlparse
        ext = Path(urlparse(url).path).suffix.lower()
        if ext in FILE_EXTENSIONS:
            return True
        # HEAD can be blocked/expensive; only use it when the path has no extension.
        if ext:
            return False
        return False

    def _upsert_link_attachment(self, message: dict, url: str, guild_id, channel_id) -> None:
        import hashlib as _h
        mid = int(message["id"])
        token = _h.sha1(url.encode()).hexdigest()[:16]
        aid = f"link:{mid}:{token}"
        pseudo = {
            "id": aid,
            "filename": _guess_filename(url),
            "content_type": None,
            "size": None,
            "url": url,
            "proxy_url": None,
            "kind": "link",
        }
        self.store.upsert_attachment_dict(pseudo, mid, guild_id, channel_id)
        self.submit_attachment(aid)

    async def process_attachment(self, attachment_id: str) -> None:
        row = self.store.get_attachment(attachment_id)
        if row is None or row["state"] != "pending":
            return
        url = row["url"]
        if not url:
            self.store.set_attachment_error(attachment_id, "no URL")
            return

        tmp = self.tmp_dir / f"{uuid.uuid4().hex}.part"
        try:
            try:
                size, ctype = await self.discord.download(url, tmp)
            except Exception as first_error:
                # CDN URLs can expire; refresh the message and retry once.
                refreshed = await self._refresh_attachment_url(attachment_id)
                if not refreshed:
                    raise first_error
                size, ctype = await self.discord.download(refreshed, tmp)

            sha = await asyncio.to_thread(_sha256_file, tmp)
            ctype = ctype or row["content_type"]
            filename = row["filename"] or _guess_filename(url)
            blob = self.store.get_blob(sha)
            remote_path = blob["remote_path"] if blob else None
            local_path = self._cache_path(sha)

            if local_path.exists():
                tmp.unlink(missing_ok=True)
                local_path_str = str(local_path)
            else:
                local_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(tmp), str(local_path))
                local_path_str = str(local_path)

            self.store.mark_attachment_downloaded(
                attachment_id, sha, size, ctype, local_path_str, remote_path,
                filename=filename,
            )
            self.download_count += 1

            if remote_path and not self.cfg.get("keep_local_after_upload", False):
                local_path.unlink(missing_ok=True)
                self.store.set_blob_local_path(sha, None)
            elif self.nextcloud and not remote_path:
                await self.upload_blob(sha, local_path, filename, ctype)
        finally:
            tmp.unlink(missing_ok=True)

    async def _refresh_attachment_url(self, attachment_id: str) -> str | None:
        row = self.store.get_attachment(attachment_id)
        if row is None or not self.discord:
            return None
        fresh = await self.discord.message(int(row["channel_id"]), int(row["message_id"]))
        if not fresh:
            return None
        for a in fresh.get("attachments") or []:
            if str(a.get("id")) == attachment_id and a.get("url"):
                self.store.set_attachment_url(attachment_id, a["url"])
                return a["url"]
        return None

    async def upload_blob(self, sha: str, local_path: Path,
                          filename: str | None, content_type: str | None) -> None:
        if not self.nextcloud:
            return
        lock = self._upload_locks.setdefault(sha, asyncio.Lock())
        async with lock:
            blob = self.store.get_blob(sha)
            if blob and blob["remote_path"]:
                if not self.cfg.get("keep_local_after_upload", False):
                    local_path.unlink(missing_ok=True)
                    self.store.set_blob_local_path(sha, None)
                return
            remote_path = self._remote_path(sha, filename)
            try:
                if not local_path.exists():
                    # Another worker may have completed the upload and evicted
                    # the cache while this worker was waiting on the lock.
                    if await self.nextcloud.exists(remote_path):
                        self.store.mark_blob_uploaded(sha, remote_path)
                        return
                    raise FileNotFoundError(str(local_path))
                # Content-addressed PUT is idempotent; skip a HEAD round-trip
                # for the normal new-blob path.
                await self.nextcloud.upload_file(local_path, remote_path, content_type)
                self.store.mark_blob_uploaded(sha, remote_path)
                self.upload_count += 1
                if not self.cfg.get("keep_local_after_upload", False):
                    try:
                        local_path.unlink(missing_ok=True)
                    except OSError:
                        pass
                    self.store.set_blob_local_path(sha, None)
            except Exception as e:
                # Keep the local file so a later retry can upload it.
                log.warning("Nextcloud upload failed for %s: %s", sha, e)

    async def delete_message(self, message_id: int) -> None:
        released = self.store.delete_message(message_id)
        if self.nextcloud:
            for blob in released:
                remote = blob.get("remote_path")
                if remote:
                    try:
                        await self.nextcloud.delete(remote)
                    except Exception as e:
                        log.warning("could not delete remote blob %s: %s", remote, e)

    async def process_pending_attachments(self, limit: int = 10_000) -> int:
        if not self.cfg.get("download_attachments", True):
            return 0
        total = 0
        while True:
            rows = self.store.pending_attachments(limit)
            if not rows:
                break
            for row in rows:
                self.submit_attachment(str(row["id"]))
            await self.drain()
            total += len(rows)
            # If the same rows are still pending (e.g. no URL), stop to avoid a loop.
            still = [r for r in self.store.pending_attachments(limit)
                     if str(r["id"]) in {str(x["id"]) for x in rows}]
            if still:
                break
        return total

    async def retry_pending_uploads(self, limit: int = 100) -> int:
        if not self.nextcloud:
            return 0
        rows = self.store.blobs_pending_upload(limit)
        done = 0
        for row in rows:
            p = Path(row["local_path"]) if row["local_path"] else None
            if not p or not p.exists():
                continue
            await self.upload_blob(row["sha256"], p, row["filename"],
                                   row["content_type"])
            done += 1
        return done
