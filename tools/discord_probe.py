#!/usr/bin/env python3
"""Read-only Discord inventory probe.

Loads the bot token from the repo-root DISCORD_TOKEN file, enumerates the
guilds/channels/threads the bot can see, and prints a summary. It never prints
the token and never writes to Discord.

Run with: ./venv/bin/python tools/discord_probe.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import aiohttp

ROOT = Path(__file__).resolve().parent.parent
API = "https://discord.com/api/v10"
TOKEN_PATH = ROOT / "DISCORD_TOKEN"

CHANNEL_TYPES = {
    0: "text",
    2: "voice",
    4: "category",
    5: "news",
    10: "thread_news",
    11: "thread_public",
    12: "thread_private",
    13: "stage",
    15: "forum",
    16: "media",
}


def load_token() -> str:
    raw = TOKEN_PATH.read_text(encoding="utf-8").strip()
    if raw.startswith("DISCORD_TOKEN="):
        raw = raw.split("=", 1)[1].strip()
    if raw.startswith("Bot "):
        raw = raw[4:].strip()
    if not raw:
        raise SystemExit("DISCORD_TOKEN file is empty")
    return raw


class DiscordAPI:
    def __init__(self, token: str):
        self.session: aiohttp.ClientSession | None = None
        self.token = token

    async def __aenter__(self):
        self.session = aiohttp.ClientSession(
            headers={
                "Authorization": f"Bot {self.token}",
                "User-Agent": "DiscordBot (https://github.com/Richardn2002/discord-shared-bot, 1.0)",
            }
        )
        return self

    async def __aexit__(self, *exc):
        if self.session:
            await self.session.close()

    async def get(self, path: str, **params):
        assert self.session
        url = API + path
        for attempt in range(6):
            async with self.session.get(url, params=params) as r:
                if r.status == 429:
                    data = await r.json()
                    wait = float(data.get("retry_after", 1.0))
                    await asyncio.sleep(wait + 0.2)
                    continue
                if r.status == 403:
                    return None, 403
                if not r.ok:
                    text = await r.text()
                    raise RuntimeError(f"GET {path} -> {r.status}: {text[:500]}")
                data = await r.json()
                remaining = r.headers.get("X-RateLimit-Remaining")
                if remaining is not None and int(remaining) <= 1:
                    reset = float(r.headers.get("X-RateLimit-Reset-After", 0.0))
                    if reset > 0:
                        await asyncio.sleep(reset + 0.1)
                return data, r.status
        raise RuntimeError(f"GET {path} kept hitting rate limits")


async def main() -> int:
    token = load_token()
    async with DiscordAPI(token) as api:
        me, _ = await api.get("/users/@me")
        print(f"bot: {me['username']}#{me.get('discriminator', '0')} id={me['id']}")

        guilds, _ = await api.get("/users/@me/guilds")
        if not guilds:
            print("bot is not in any guilds")
            return 1

        inventory = []
        for g in guilds:
            gid = g["id"]
            channels, status = await api.get(f"/guilds/{gid}/channels")
            if channels is None:
                print(f"\nguild {g['name']} ({gid}): channels forbidden")
                continue
            active, _ = await api.get(f"/guilds/{gid}/threads/active")
            active_threads = active.get("threads", []) if active else []

            counts: dict[str, int] = {}
            for ch in channels:
                name = CHANNEL_TYPES.get(ch["type"], str(ch["type"]))
                counts[name] = counts.get(name, 0) + 1

            print(f"\nguild: {g['name']} ({gid})")
            print(f"  channels: {len(channels)}  {dict(sorted(counts.items()))}")
            print(f"  active threads: {len(active_threads)}")

            inventory.append({
                "guild": {"id": gid, "name": g["name"]},
                "channels": channels,
                "active_threads": active_threads,
            })

        out = ROOT / "archive" / "meta" / "discord_inventory.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(inventory, indent=2, ensure_ascii=False))
        print(f"\ninventory written to {out.relative_to(ROOT)}")
        return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
