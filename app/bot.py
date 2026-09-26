"""Discord gateway client and the executor that turns framework actions into
real Discord API calls."""
from __future__ import annotations

import asyncio
import base64
import io

import discord

from .manager import Manager

STATUS = {"online": False, "user": None, "guilds": [], "latency_ms": None}


def user_payload(u) -> dict:
    return {
        "id": u.id,
        "name": u.name,
        "display_name": getattr(u, "display_name", u.name),
    }


def message_payload(m: discord.Message, old_content=None) -> dict:
    guild = m.guild
    return {
        "id": m.id,
        "content": m.content,
        "old_content": old_content,
        "author": user_payload(m.author),
        "channel_id": m.channel.id,
        "channel_name": getattr(m.channel, "name", None) or "dm",
        "guild_id": guild.id if guild else None,
        "guild_name": guild.name if guild else None,
        "attachments": [a.url for a in (m.attachments or [])],
    }


def reaction_payload(reaction: discord.Reaction, user) -> dict:
    m = reaction.message
    guild = getattr(m, "guild", None)
    return {
        "emoji": str(reaction.emoji),
        "message_id": m.id,
        "channel_id": m.channel.id,
        "channel_name": getattr(m.channel, "name", None) or "dm",
        "user": user_payload(user),
        "message_author": user_payload(m.author) if m.author else None,
        "guild_id": guild.id if guild else None,
        "guild_name": guild.name if guild else None,
    }


class BotClient(discord.Client):
    def __init__(self, manager: Manager):
        intents = discord.Intents.none()
        intents.guilds = True
        intents.messages = True          # guild + dm messages
        intents.message_content = True   # requires the dev-portal toggle!
        intents.reactions = True
        super().__init__(intents=intents)
        self.manager = manager

    def _dispatch(self, event: str, data: dict):
        asyncio.create_task(self.manager.dispatch(event, data))

    async def on_ready(self):
        STATUS.update({
            "online": True,
            "user": str(self.user),
            "guilds": [g.name for g in self.guilds],
        })
        print(f"[bot] online as {self.user} | guilds: {[g.name for g in self.guilds]}")

    async def on_resumed(self):
        STATUS["online"] = True

    async def on_disconnect(self):
        STATUS["online"] = False

    async def on_message(self, m: discord.Message):
        if m.author.bot:
            return
        self._dispatch("on_message", message_payload(m))

    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        if after.author and after.author.bot:
            return
        self._dispatch("on_message_edit",
                       message_payload(after, old_content=before.content))

    async def on_message_delete(self, m: discord.Message):
        if m.author and m.author.bot:
            return
        self._dispatch("on_message_delete", message_payload(m))

    async def on_reaction_add(self, reaction: discord.Reaction, user):
        if user.bot:
            return
        self._dispatch("on_reaction_add", reaction_payload(reaction, user))

    async def on_reaction_remove(self, reaction: discord.Reaction, user):
        if user.bot:
            return
        self._dispatch("on_reaction_remove", reaction_payload(reaction, user))


class DiscordExecutor:
    """Turns actions coming back from workers into Discord API calls."""

    def __init__(self, client: BotClient):
        self.client = client

    async def _channel(self, channel_id: int):
        ch = self.client.get_channel(channel_id)
        if ch is None:
            ch = await self.client.fetch_channel(channel_id)
        return ch

    async def send_text(self, channel_id: int, content: str) -> None:
        ch = await self._channel(channel_id)
        await ch.send(content)

    async def execute(self, action: dict) -> None:
        kind = action.get("action")
        ch = await self._channel(action["channel_id"])
        if kind == "send" and "embed" in action:
            e = action["embed"]
            embed = discord.Embed(
                title=e.get("title"),
                description=e.get("description"),
                color=discord.Color(e.get("color") or 0x5865F2),
            )
            for f in (e.get("fields") or [])[:25]:
                embed.add_field(name=str(f.get("name", ""))[:256],
                                value=str(f.get("value", ""))[:1024],
                                inline=bool(f.get("inline", True)))
            await ch.send(embed=embed)
        elif kind == "send" and "file" in action:
            f = action["file"]
            data = base64.b64decode(f["data_b64"])
            await ch.send(file=discord.File(io.BytesIO(data), filename=f["filename"]))
        elif kind == "reply":
            ref = discord.MessageReference(message_id=action["message_id"],
                                           channel_id=action["channel_id"],
                                           fail_if_not_exists=False)
            await ch.send(action["content"], reference=ref,
                          allowed_mentions=discord.AllowedMentions(replied_user=False))
        elif kind == "react":
            msg = ch.get_partial_message(action["message_id"])
            await msg.add_reaction(action["emoji"])
        elif kind == "send":
            await ch.send(action.get("content", ""))


BOT: BotClient | None = None
_bot_task: asyncio.Task | None = None


async def start_bot(manager: Manager, token: str | None) -> None:
    """Start the discord client in the background if a token is configured."""
    global BOT, _bot_task
    if not token:
        print("[bot] no token configured — running web UI only")
        return
    client = BotClient(manager)
    manager.executor = DiscordExecutor(client)
    BOT = client

    async def runner():
        while True:
            try:
                await client.start(token, reconnect=True)
            except discord.LoginFailure:
                print("[bot] LOGIN FAILED — check your token")
                break
            except Exception as e:
                print(f"[bot] connection error: {e}; retrying in 10s")
                STATUS["online"] = False
                await asyncio.sleep(10)

    _bot_task = asyncio.create_task(runner())


async def stop_bot() -> None:
    global BOT, _bot_task
    if BOT:
        try:
            await BOT.close()
        except Exception:
            pass
    if _bot_task:
        _bot_task.cancel()
    BOT = None
    _bot_task = None
    STATUS.update({"online": False, "user": None, "guilds": []})
