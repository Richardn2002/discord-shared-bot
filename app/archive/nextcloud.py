"""Nextcloud WebDAV blob storage for the archive.

Files are uploaded under a content-addressed path:
    <base_path>/attachments/<sha[:2]>/<sha[2:4]>/<sha>/<original filename>

Large uploads use Nextcloud's chunked upload v2 so normal PHP upload limits do
not silently truncate videos. Credentials come from config.json / env.
"""
from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from urllib.parse import quote

import aiohttp


class NextcloudError(RuntimeError):
    pass


class NextcloudStore:
    def __init__(self, config: dict):
        self.cfg = config
        self.base_url = str(config.get("url") or "").rstrip("/")
        self.username = config.get("username") or ""
        self.password = config.get("app_password") or ""
        self.base_path = str(config.get("base_path") or "discord-archive").strip("/")
        self.verify_tls = bool(config.get("verify_tls", True))
        self.timeout = aiohttp.ClientTimeout(total=float(config.get("timeout", 120)))
        self.session: aiohttp.ClientSession | None = None
        self._ready_dirs: set[str] = set()
        self._dir_lock: asyncio.Lock | None = None
        if not self.enabled:
            raise NextcloudError("Nextcloud is not configured/enabled")
        if not self.username or not self.password:
            raise NextcloudError("Nextcloud username/app_password is missing")

    @property
    def enabled(self) -> bool:
        return bool(self.cfg.get("enabled") and self.cfg.get("url"))

    async def __aenter__(self):
        self.session = aiohttp.ClientSession(
            auth=aiohttp.BasicAuth(self.username, self.password),
            timeout=self.timeout,
        )
        self._dir_lock = asyncio.Lock()
        return self

    async def __aexit__(self, *exc):
        if self.session:
            await self.session.close()

    # -- URL helpers ------------------------------------------------------
    def _path_url(self, path: str) -> str:
        path = path.lstrip("/")
        return (f"{self.base_url}/remote.php/dav/files/{quote(self.username)}/"
                f"{quote(path, safe='/')}")

    def _uploads_url(self, upload_id: str | None = None) -> str:
        base = (f"{self.base_url}/remote.php/dav/uploads/{quote(self.username)}")
        return f"{base}/{upload_id}" if upload_id else base

    @property
    def root_path(self) -> str:
        return self.base_path

    def remote_attachment_path(self, sha: str, filename: str | None) -> str:
        safe = (filename or "file").replace("\\", "_").replace("/", "_").strip() or "file"
        safe = safe[:180]
        return f"{self.base_path}/attachments/{sha[:2]}/{sha[2:4]}/{sha}/{safe}"

    # -- basic operations -------------------------------------------------
    async def _request(self, method: str, url: str, *, data=None, headers=None,
                       expect=(200, 201, 204, 207, 301, 302, 404, 405, 409),
                       retries: int = 3) -> tuple[int, bytes]:
        assert self.session
        last_error = None
        for attempt in range(retries):
            try:
                async with self.session.request(method, url, data=data,
                                                headers=headers) as r:
                    body = await r.read()
                    if r.status in expect:
                        return r.status, body
                    if r.status in (423, 429, 500, 502, 503, 504):
                        await asyncio.sleep(1 + attempt * 2)
                        last_error = f"HTTP {r.status}: {body[:200]!r}"
                        continue
                    raise NextcloudError(f"{method} {url} -> {r.status}: {body[:300]!r}")
            except (aiohttp.ClientError, aiohttp.ServerTimeoutError) as e:
                last_error = str(e)
                await asyncio.sleep(1 + attempt * 2)
        raise NextcloudError(f"{method} {url} failed: {last_error}")

    async def ensure_dir(self, path: str) -> None:
        """Create a path segment-by-segment; existing components are ignored."""
        path = path.strip("/")
        if path in self._ready_dirs:
            return
        assert self._dir_lock is not None
        async with self._dir_lock:
            if path in self._ready_dirs:
                return
            parts = [p for p in path.split("/") if p]
            current = ""
            for part in parts:
                current = f"{current}/{part}" if current else part
                await self._request("MKCOL", self._path_url(current),
                                    expect=(200, 201, 204, 405))
            self._ready_dirs.add(path)

    async def exists(self, path: str) -> bool:
        status, _ = await self._request("HEAD", self._path_url(path),
                                        expect=(200, 204, 404, 403))
        return status in (200, 204)

    async def delete(self, path: str) -> bool:
        status, _ = await self._request("DELETE", self._path_url(path),
                                        expect=(200, 204, 404))
        return status in (200, 204)

    async def upload_file(self, local_path: Path, remote_path: str,
                          content_type: str | None = None) -> None:
        local_path = Path(local_path)
        parent = remote_path.rsplit("/", 1)[0] if "/" in remote_path else ""
        if parent:
            await self.ensure_dir(parent)
        size = local_path.stat().st_size
        headers = {"Content-Type": content_type or "application/octet-stream"}
        # Nextcloud deployments often cap a single PUT. Chunk large files.
        if size > 32 * 1024 * 1024:
            await self._upload_chunked(local_path, remote_path, content_type)
            return
        with local_path.open("rb") as f:
            status, body = await self._request(
                "PUT", self._path_url(remote_path), data=f, headers=headers,
                expect=(200, 201, 204), retries=3,
            )
        if status not in (200, 201, 204):
            raise NextcloudError(f"upload {remote_path} -> {status}: {body[:200]!r}")

    async def _upload_chunked(self, local_path: Path, remote_path: str,
                              content_type: str | None) -> None:
        upload_id = uuid.uuid4().hex
        upload_base = self._uploads_url(upload_id)
        # 1. create upload collection
        status, body = await self._request("MKCOL", upload_base,
                                           expect=(200, 201, 204, 405))
        if status not in (200, 201, 204, 405):
            raise NextcloudError(f"chunked upload MKCOL -> {status}: {body[:200]!r}")

        chunk_size = 16 * 1024 * 1024
        index = 0
        with local_path.open("rb") as f:
            while True:
                chunk = f.read(chunk_size)
                if not chunk:
                    break
                status, body = await self._request(
                    "PUT", f"{upload_base}/{index:05d}", data=chunk,
                    headers={"Content-Type": "application/octet-stream"},
                    expect=(200, 201, 204),
                )
                if status not in (200, 201, 204):
                    raise NextcloudError(f"chunk {index} -> {status}: {body[:200]!r}")
                index += 1

        # 2. assemble into the final path
        dest = self._path_url(remote_path)
        headers = {
            "Destination": dest,
            "Overwrite": "T",
            "OC-Total-Length": str(local_path.stat().st_size),
            "Content-Type": content_type or "application/octet-stream",
        }
        status, body = await self._request(
            "MOVE", f"{upload_base}/.file", headers=headers,
            expect=(200, 201, 204, 502, 503),
        )
        if status not in (200, 201, 204):
            # best-effort cleanup of the upload collection
            await self._request("DELETE", upload_base, expect=(200, 204, 404))
            raise NextcloudError(f"chunked MOVE -> {status}: {body[:300]!r}")

    async def read_bytes(self, path: str, *, limit: int | None = None) -> bytes:
        assert self.session
        url = self._path_url(path)
        headers = {"Range": f"bytes=0-{limit - 1}"} if limit else None
        async with self.session.get(url, headers=headers) as r:
            if r.status not in (200, 206):
                raise NextcloudError(f"download {path} -> {r.status}")
            return await r.read()

    async def iter_bytes(self, path: str, chunk_size: int = 256 * 1024):
        """Yield a remote file in chunks for the web attachment endpoint."""
        assert self.session
        async with self.session.get(self._path_url(path)) as r:
            if r.status not in (200, 206):
                raise NextcloudError(f"download {path} -> {r.status}")
            async for chunk in r.content.iter_chunked(chunk_size):
                yield chunk

    async def download_to(self, path: str, dest: Path) -> None:
        assert self.session
        dest.parent.mkdir(parents=True, exist_ok=True)
        async with self.session.get(self._path_url(path)) as r:
            if r.status not in (200, 206):
                raise NextcloudError(f"download {path} -> {r.status}")
            with dest.open("wb") as f:
                async for chunk in r.content.iter_chunked(256 * 1024):
                    f.write(chunk)
