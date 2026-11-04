"""Read-only archive query facade shared by the web API and project workers.

The heavy lifting (SQL, filtering, pagination, heatmap aggregation) lives in
``store.ArchiveStore``. This module only shapes results for public callers and
lets the web API opt into string IDs while Python projects keep integer IDs.
"""
from __future__ import annotations

import json

from .store import ArchiveQueryError


def _sid(value, string_ids: bool):
    if value is None:
        return None
    return str(value) if string_ids else value


def attachment_payload(row, *, string_ids: bool = True) -> dict:
    row = dict(row)
    return {
        "id": _sid(row.get("id"), string_ids),
        "message_id": _sid(row.get("message_id"), string_ids),
        "channel_id": _sid(row.get("channel_id"), string_ids),
        "guild_id": _sid(row.get("guild_id"), string_ids),
        "filename": row.get("filename"),
        "content_type": row.get("content_type"),
        "size_bytes": row.get("size_bytes"),
        "width": row.get("width"),
        "height": row.get("height"),
        "kind": row.get("kind"),
        "state": row.get("state"),
        "sha256": row.get("sha256"),
        "available": bool(row.get("sha256")) and row.get("state") == "ready",
    }


def _mentions_payload(raw: dict, *, string_ids: bool) -> list[dict]:
    out = []
    for user in raw.get("mentions") or []:
        if not isinstance(user, dict):
            continue
        out.append({
            "id": _sid(user.get("id"), string_ids),
            "name": user.get("username") or user.get("name"),
            "display_name": (user.get("global_name") or user.get("display_name")
                             or user.get("username") or user.get("name")),
            "is_bot": bool(user.get("bot")),
        })
    return out


def _embeds_payload(raw: dict, *, limit: int = 5) -> list[dict]:
    embeds = []
    for e in (raw.get("embeds") or [])[:limit]:
        if not isinstance(e, dict):
            continue
        embeds.append({
            "type": e.get("type"),
            "title": e.get("title"),
            "description": e.get("description"),
            "url": e.get("url"),
            "color": e.get("color"),
            "timestamp": e.get("timestamp"),
            "fields": [
                {"name": f.get("name"), "value": f.get("value"),
                 "inline": bool(f.get("inline"))}
                for f in (e.get("fields") or []) if isinstance(f, dict)
            ][:20],
            "author": {
                "name": (e.get("author") or {}).get("name"),
                "url": (e.get("author") or {}).get("url"),
                "icon_url": (e.get("author") or {}).get("icon_url"),
            } if isinstance(e.get("author"), dict) else None,
            "footer": {
                "text": (e.get("footer") or {}).get("text"),
                "icon_url": (e.get("footer") or {}).get("icon_url"),
            } if isinstance(e.get("footer"), dict) else None,
            "image": {
                "url": (e.get("image") or {}).get("url"),
                "width": (e.get("image") or {}).get("width"),
                "height": (e.get("image") or {}).get("height"),
            } if isinstance(e.get("image"), dict) else None,
            "thumbnail": {
                "url": (e.get("thumbnail") or {}).get("url"),
                "width": (e.get("thumbnail") or {}).get("width"),
                "height": (e.get("thumbnail") or {}).get("height"),
            } if isinstance(e.get("thumbnail"), dict) else None,
        })
    return embeds


def _forward_payload(raw: dict, *, string_ids: bool) -> dict | None:
    ref = raw.get("message_reference") or {}
    snapshots = raw.get("message_snapshots") or []
    snap = None
    if snapshots and isinstance(snapshots[0], dict):
        candidate = snapshots[0].get("message")
        if isinstance(candidate, dict):
            snap = candidate
    if ref.get("type") != 1 and snap is None:
        return None
    snap = snap or {}
    author = snap.get("author") if isinstance(snap.get("author"), dict) else {}
    return {
        "message_id": _sid(ref.get("message_id"), string_ids),
        "channel_id": _sid(ref.get("channel_id"), string_ids),
        "guild_id": _sid(ref.get("guild_id"), string_ids),
        "content": snap.get("content") or "",
        "timestamp": snap.get("timestamp"),
        "author": {
            "id": _sid(author.get("id"), string_ids),
            "name": author.get("username") or author.get("name"),
            "display_name": (author.get("global_name") or author.get("display_name")
                             or author.get("username") or author.get("name")),
            "is_bot": bool(author.get("bot")),
        } if author else None,
        "mentions": _mentions_payload(snap, string_ids=string_ids),
        "embeds": _embeds_payload(snap),
        "attachments": [{
            "id": _sid(a.get("id"), string_ids),
            "filename": a.get("filename"),
            "content_type": a.get("content_type"),
            "size_bytes": a.get("size"),
            "width": a.get("width"),
            "height": a.get("height"),
        } for a in (snap.get("attachments") or []) if isinstance(a, dict)],
    }


def message_payload(row: dict, attachments=None, reply=None, *,
                    string_ids: bool = True) -> dict:
    raw = {}
    try:
        raw = json.loads(row.get("raw_json") or "{}")
    except Exception:
        raw = {}
    ref = raw.get("message_reference") or {}
    ref_type = ref.get("type")
    embeds = _embeds_payload(raw)
    mentions = _mentions_payload(raw, string_ids=string_ids)
    forward = _forward_payload(raw, string_ids=string_ids)
    reply_to = None
    if reply:
        reply_to = {
            "id": _sid(reply.get("id"), string_ids),
            "author_name": reply.get("author_display_name") or reply.get("author_name"),
            "content": (reply.get("content") or "")[:160],
        }
    rid = row.get("id")
    gid = row.get("guild_id")
    cid = row.get("channel_id")
    jump_url = (f"https://discord.com/channels/{gid}/{cid}/{rid}"
                if rid is not None and gid is not None and cid is not None else None)
    return {
        "id": _sid(rid, string_ids),
        "guild_id": _sid(gid, string_ids),
        "channel_id": _sid(cid, string_ids),
        "channel_name": row.get("channel_name"),
        "channel_is_thread": bool(row.get("channel_is_thread")),
        "guild_name": row.get("guild_name"),
        "author": {
            "id": _sid(row.get("author_id"), string_ids),
            "name": row.get("author_name"),
            "display_name": row.get("author_display_name") or row.get("author_name"),
            "is_bot": bool(row.get("author_is_bot")),
        },
        "content": row.get("content") or "",
        "created_at": row.get("created_at"),
        "created_ts": row.get("created_ts"),
        "edited_at": row.get("edited_at"),
        "edited_ts": row.get("edited_ts"),
        "message_type": row.get("message_type"),
        "reference_message_id": _sid(row.get("reference_message_id"), string_ids),
        "reference_channel_id": _sid(row.get("reference_channel_id"), string_ids),
        "pinned": bool(row.get("pinned")),
        "has_attachments": bool(row.get("has_attachments")),
        "has_embeds": bool(row.get("has_embeds")),
        "reference_type": ref_type,
        "reference_guild_id": _sid(ref.get("guild_id"), string_ids),
        "mention_everyone": bool(raw.get("mention_everyone")),
        "mentions": mentions,
        "mention_roles": [str(r) for r in (raw.get("mention_roles") or [])],
        "forward": forward,
        "attachments": [attachment_payload(a, string_ids=string_ids)
                        for a in (attachments or [])],
        "embeds": embeds,
        "reply_to": reply_to,
        "jump_url": jump_url,
    }


def serialize_messages(store, rows: list[dict], *,
                       string_ids: bool = True) -> list[dict]:
    if not rows:
        return []
    ids = [r.get("id") for r in rows]
    attachment_map = store.attachments_for_messages(ids)
    ref_ids = [r.get("reference_message_id") for r in rows
               if r.get("reference_message_id")]
    ref_map = {str(r["id"]): r for r in store.get_messages(ref_ids)} if ref_ids else {}
    return [
        message_payload(
            row,
            attachments=attachment_map.get(str(row.get("id")), []),
            reply=ref_map.get(str(row.get("reference_message_id"))),
            string_ids=string_ids,
        )
        for row in rows
    ]


def query_messages(store, body: dict, *, string_ids: bool = True) -> dict:
    result = store.query_messages(
        body.get("filter"), guild_id=body.get("guild_id"),
        channel_id=body.get("channel_id"), author_id=body.get("author_id"),
        before=body.get("before"), after=body.get("after"),
        limit=body.get("limit", 50), cursor=body.get("cursor"),
        order=body.get("order", "desc"),
        include_bots=body.get("include_bots", True),
    )
    result["items"] = serialize_messages(store, result["items"],
                                         string_ids=string_ids)
    return result


def search_messages(store, query=None, *, guild_id=None, channel_id=None,
                    author_id=None, before=None, after=None, limit=50,
                    cursor=None, order="desc", include_bots=True,
                    string_ids: bool = True) -> dict:
    """Discord-like multi-term search: every term must match search text."""
    terms = str(query or "").split()
    filter_ast = None
    if terms:
        filter_ast = {
            "kind": "group", "op": "AND",
            "children": [{"kind": "condition", "field": "search_text",
                          "op": "contains", "value": term}
                         for term in terms],
        }
    return query_messages(store, {
        "filter": filter_ast, "guild_id": guild_id, "channel_id": channel_id,
        "author_id": author_id, "before": before, "after": after, "limit": limit,
        "cursor": cursor, "order": order, "include_bots": include_bots,
    }, string_ids=string_ids)


def message_context(store, message_id, *, channel_id=None, before=20, after=20,
                    string_ids: bool = True) -> dict | None:
    result = store.message_context(message_id, channel_id=channel_id,
                                   before=before, after=after)
    if result:
        result["items"] = serialize_messages(store, result["items"],
                                             string_ids=string_ids)
    return result


def get_message(store, message_id, *, channel_id=None,
                string_ids: bool = True) -> dict | None:
    row = store.get_message(message_id, channel_id=channel_id)
    if not row:
        return None
    return serialize_messages(store, [row], string_ids=string_ids)[0]


def guilds(store, *, string_ids: bool = True) -> list[dict]:
    rows = store.guilds()
    if string_ids:
        for row in rows:
            row["id"] = _sid(row.get("id"), string_ids)
    return rows


def channels(store, guild_id=None, *, string_ids: bool = True) -> list[dict]:
    rows = store.channels(guild_id)
    if string_ids:
        for row in rows:
            row["id"] = _sid(row.get("id"), string_ids)
            row["guild_id"] = _sid(row.get("guild_id"), string_ids)
            row["parent_id"] = _sid(row.get("parent_id"), string_ids)
    return rows


def users(store, guild_id=None, q=None, limit=50, *,
          string_ids: bool = True) -> list[dict]:
    rows = store.users(guild_id, q, limit)
    if string_ids:
        for row in rows:
            row["id"] = _sid(row.get("id"), string_ids)
    return rows


def stats(store, guild_id=None) -> dict:
    return store.stats(guild_id)


def heatmap(store, *, guild_id=None, channel_id=None, after=None, before=None,
            bucket="hour", tz_offset=0, include_bots=True) -> dict:
    seconds = 86400 if bucket == "day" else 3600
    return store.heatmap(guild_id=guild_id, channel_id=channel_id,
                         after=after, before=before, bucket_seconds=seconds,
                         tz_offset_minutes=tz_offset,
                         include_bots=include_bots)


def attachment_meta(store, attachment_id, *,
                    string_ids: bool = True) -> dict | None:
    row = store.get_attachment(str(attachment_id))
    return attachment_payload(row, string_ids=string_ids) if row else None


__all__ = [
    "ArchiveQueryError", "attachment_meta", "attachment_payload", "channels",
    "get_message", "guilds", "heatmap", "message_context", "message_payload",
    "query_messages", "search_messages", "serialize_messages", "stats", "users",
]
