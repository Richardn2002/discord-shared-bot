"""Read-only Discord REST source used by the archive backfill/sync tooling.

This deliberately does not open a gateway connection, so running it cannot
interfere with the live bot. It only performs GET requests.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import AsyncIterator

import aiohttp

from ..config import ROOT_DIR

API = "https://discord.com/api/v10"


def load_discord_token() -> str:
    for path in (ROOT_DIR / "DISCORD_TOKEN",):
        if path.exists():
            raw = path.read_text(encoding="utf-8").strip()
            if raw.startswith("DISCORD_TOKEN="):
                raw = raw.split("=", 1)[1].strip()
            if raw.startswith("Bot "):
                raw = raw[4:].strip()
            if raw:
                return raw
    from ..config import CONFIG
    if CONFIG.token:
        return CONFIG.token
    raise RuntimeError("no Discord token found (DISCORD_TOKEN file or config.json)")


class DiscordSource:
    def __init__(self, token: str | None = None):
        self.token = token or load_discord_token()
        self.session: aiohttp.ClientSession | None = None

    async def __aenter__(self):
        self.session = aiohttp.ClientSession(
            headers={
                "Authorization": f"Bot {self.token}",
                "User-Agent": "DiscordBot (https://github.com/Richardn2002/discord-shared-bot, archive)",
            },
            timeout=aiohttp.ClientTimeout(total=120),
        )
        return self

    async def __aexit__(self, *exc):
        if self.session:
            await self.session.close()

    async def get(self, path: str, **params):
        """GET a Discord API path. Returns (data, status). 403 -> (None, 403)."""
        assert self.session
        url = API + path
        for attempt in range(8):
            try:
                async with self.session.get(url, params=params) as r:
                    if r.status == 429:
                        try:
                            data = await r.json()
                        except Exception:
                            data = {}
                        wait = float(data.get("retry_after", 1.0))
                        await asyncio.sleep(wait + 0.25)
                        continue
                    if r.status in (500, 502, 503, 504):
                        await asyncio.sleep(min(2 ** attempt, 30))
                        continue
                    if r.status == 403:
                        return None, 403
                    if r.status == 404:
                        return None, 404
                    if not r.ok:
                        text = await r.text()
                        raise RuntimeError(f"GET {path} -> {r.status}: {text[:500]}")
                    data = await r.json()
                    remaining = r.headers.get("X-RateLimit-Remaining")
                    if remaining is not None and int(remaining) <= 0:
                        reset = float(r.headers.get("X-RateLimit-Reset-After", 0.0))
                        if reset > 0:
                            await asyncio.sleep(reset + 0.1)
                    return data, r.status
            except (aiohttp.ClientError, asyncio.TimeoutError):
                if attempt == 7:
                    raise
                await asyncio.sleep(min(2 ** attempt, 30))
        raise RuntimeError(f"GET {path} kept failing")

    async def me(self) -> dict:
        data, _ = await self.get("/users/@me")
        return data

    async def guilds(self) -> list[dict]:
        data, _ = await self.get("/users/@me/guilds")
        return data or []

    async def guild_channels(self, guild_id: int) -> list[dict]:
        data, status = await self.get(f"/guilds/{guild_id}/channels")
        return data or []

    async def guild_member(self, guild_id: int, user_id: int) -> dict | None:
        data, _ = await self.get(f"/guilds/{guild_id}/members/{user_id}")
        return data

    async def active_threads(self, guild_id: int) -> list[dict]:
        data, status = await self.get(f"/guilds/{guild_id}/threads/active")
        if not data:
            return []
        return data.get("threads") or []

    async def archived_threads(self, channel_id: int, private: bool = False,
                               before: str | None = None) -> list[dict]:
        kind = "private" if private else "public"
        params = {"limit": 100}
        if before:
            params["before"] = before
        data, status = await self.get(
            f"/channels/{channel_id}/threads/archived/{kind}", **params)
        if status in (400, 403, 404):
            return []
        return (data or {}).get("threads") or []

    async def messages_page(self, channel_id: int, *, after: int | None = None,
                            before: int | None = None,
                            limit: int = 100) -> tuple[list[dict], int]:
        """Like messages(), but also returns the HTTP status so a reconcile
        pass can distinguish an empty channel from an inaccessible one."""
        params: dict = {"limit": min(int(limit), 100)}
        if after is not None:
            params["after"] = int(after)
        if before is not None:
            params["before"] = int(before)
        data, status = await self.get(f"/channels/{channel_id}/messages", **params)
        if status in (403, 404):
            return [], status
        return data or [], status

    async def messages(self, channel_id: int, *, after: int | None = None,
                       before: int | None = None, limit: int = 100) -> list[dict]:
        data, _status = await self.messages_page(
            channel_id, after=after, before=before, limit=limit)
        return data

    async def message(self, channel_id: int, message_id: int) -> dict | None:
        data, status = await self.get(f"/channels/{channel_id}/messages/{message_id}")
        return data if status == 200 else None

    async def download(self, url: str, dest: Path) -> tuple[int, str | None]:
        """Stream a CDN attachment to dest. Returns (bytes, content-type).
        Computes no hash here; the ingestor hashes the resulting file."""
        assert self.session
        dest.parent.mkdir(parents=True, exist_ok=True)
        total = 0
        ctype = None
        for attempt in range(5):
            try:
                async with self.session.get(url) as r:
                    if r.status == 429:
                        wait = float(r.headers.get("Retry-After", "1"))
                        await asyncio.sleep(wait + 0.25)
                        continue
                    if r.status in (500, 502, 503, 504):
                        await asyncio.sleep(min(2 ** attempt, 20))
                        continue
                    if r.status not in (200, 206):
                        raise RuntimeError(f"download -> {r.status}")
                    ctype = r.headers.get("Content-Type")
                    with dest.open("wb") as f:
                        async for chunk in r.content.iter_chunked(256 * 1024):
                            f.write(chunk)
                            total += len(chunk)
                    return total, ctype
            except (aiohttp.ClientError, asyncio.TimeoutError):
                if attempt == 4:
                    raise
                await asyncio.sleep(min(2 ** attempt, 20))
        raise RuntimeError("download kept failing")
