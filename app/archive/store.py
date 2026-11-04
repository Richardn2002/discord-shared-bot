"""SQLite-backed message archive.

The archive is a local index of Discord messages plus metadata. Attachments are
content-addressed blobs whose bytes live on Nextcloud, with an optional local
cache. This module contains no Discord/network code, so it can be reused by the
gateway ingestor, the backfill tool and the web API.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Iterable, Iterator

SCHEMA_VERSION = 2


def _regexp(pattern, value) -> int:
    """Small SQLite REGEXP helper used by the read-only query builder."""
    if pattern is None or value is None:
        return 0
    pattern = str(pattern)
    if len(pattern) > 500:
        return 0
    try:
        return 1 if re.search(pattern, str(value)) else 0
    except re.error:
        return 0


class ArchiveQueryError(ValueError):
    """Raised for an invalid read-only archive query."""

_SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS guilds (
    id          INTEGER PRIMARY KEY,
    name        TEXT,
    icon        TEXT,
    raw_json    TEXT,
    first_seen  REAL NOT NULL,
    updated_ts  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS channels (
    id           INTEGER PRIMARY KEY,
    guild_id     INTEGER,
    name         TEXT,
    type         INTEGER,
    parent_id    INTEGER,
    position     INTEGER,
    is_thread    INTEGER NOT NULL DEFAULT 0,
    archived     INTEGER NOT NULL DEFAULT 0,
    raw_json     TEXT,
    first_seen   REAL NOT NULL,
    updated_ts   REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_channels_guild ON channels(guild_id);
CREATE INDEX IF NOT EXISTS idx_channels_parent ON channels(parent_id);

CREATE TABLE IF NOT EXISTS users (
    id             INTEGER PRIMARY KEY,
    name           TEXT,
    display_name   TEXT,
    avatar         TEXT,
    is_bot         INTEGER NOT NULL DEFAULT 0,
    raw_json       TEXT,
    first_seen     REAL NOT NULL,
    updated_ts     REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id                     INTEGER PRIMARY KEY,
    guild_id               INTEGER,
    channel_id             INTEGER NOT NULL,
    author_id              INTEGER,
    author_name            TEXT,
    author_display_name    TEXT,
    author_is_bot          INTEGER NOT NULL DEFAULT 0,
    content                TEXT NOT NULL DEFAULT '',
    search_text            TEXT NOT NULL DEFAULT '',
    created_at             TEXT,
    created_ts             REAL NOT NULL,
    edited_at              TEXT,
    edited_ts              REAL,
    message_type           INTEGER,
    reference_message_id   INTEGER,
    reference_channel_id   INTEGER,
    pinned                 INTEGER NOT NULL DEFAULT 0,
    has_attachments        INTEGER NOT NULL DEFAULT 0,
    has_embeds             INTEGER NOT NULL DEFAULT 0,
    raw_json               TEXT,
    first_seen             REAL NOT NULL,
    updated_ts             REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_channel_time
    ON messages(channel_id, created_ts, id);
CREATE INDEX IF NOT EXISTS idx_messages_guild_time
    ON messages(guild_id, created_ts, id);
CREATE INDEX IF NOT EXISTS idx_messages_author_time
    ON messages(author_id, created_ts, id);
CREATE INDEX IF NOT EXISTS idx_messages_created ON messages(created_ts, id);

CREATE TABLE IF NOT EXISTS deleted_messages (
    id          INTEGER PRIMARY KEY,
    channel_id  INTEGER,
    deleted_ts  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS attachments (
    id             TEXT PRIMARY KEY,
    message_id     INTEGER NOT NULL,
    guild_id       INTEGER,
    channel_id     INTEGER,
    filename       TEXT,
    content_type   TEXT,
    size_bytes     INTEGER,
    width          INTEGER,
    height         INTEGER,
    url            TEXT,
    proxy_url      TEXT,
    kind           TEXT NOT NULL DEFAULT 'attachment',
    sha256         TEXT,
    state          TEXT NOT NULL DEFAULT 'pending',
    error          TEXT,
    first_seen     REAL NOT NULL,
    updated_ts     REAL NOT NULL,
    FOREIGN KEY(message_id) REFERENCES messages(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_attachments_message ON attachments(message_id);
CREATE INDEX IF NOT EXISTS idx_attachments_state ON attachments(state);
CREATE INDEX IF NOT EXISTS idx_attachments_sha ON attachments(sha256);

CREATE TABLE IF NOT EXISTS blobs (
    sha256         TEXT PRIMARY KEY,
    size_bytes     INTEGER,
    content_type   TEXT,
    filename       TEXT,
    local_path     TEXT,
    remote_path    TEXT,
    state          TEXT NOT NULL DEFAULT 'local',  -- local|remote
    ref_count      INTEGER NOT NULL DEFAULT 0,
    first_seen     REAL NOT NULL,
    uploaded_ts    REAL,
    last_access_ts REAL
);
CREATE INDEX IF NOT EXISTS idx_blobs_state ON blobs(state);

CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    search_text,
    content='messages',
    content_rowid='id',
    tokenize='trigram'
);

CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
    INSERT INTO messages_fts(rowid, search_text) VALUES (new.id, new.search_text);
END;
CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, search_text)
        VALUES('delete', old.id, old.search_text);
END;
CREATE TRIGGER IF NOT EXISTS messages_au AFTER UPDATE OF search_text ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, search_text)
        VALUES('delete', old.id, old.search_text);
    INSERT INTO messages_fts(rowid, search_text) VALUES (new.id, new.search_text);
END;

CREATE TABLE IF NOT EXISTS backfill_state (
    channel_id      INTEGER PRIMARY KEY,
    guild_id        INTEGER,
    name            TEXT,
    kind            TEXT,             -- channel|thread
    status          TEXT NOT NULL DEFAULT 'pending',
    cursor          INTEGER NOT NULL DEFAULT 0,
    messages_seen   INTEGER NOT NULL DEFAULT 0,
    messages_added  INTEGER NOT NULL DEFAULT 0,
    attachments     INTEGER NOT NULL DEFAULT 0,
    last_run        REAL,
    error           TEXT
);
"""


def _safe_filename(name: str | None, fallback: str = "file") -> str:
    name = (name or fallback).replace("\\", "_").replace("/", "_")
    name = re.sub(r"[\x00-\x1f\x7f]", "_", name).strip().strip(".")
    name = name[:180]
    return name or fallback


def _iso_ts(value: str | None) -> float | None:
    if not value:
        return None
    try:
        from datetime import datetime
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def _search_text(message: dict) -> str:
    parts: list[str] = []
    content = message.get("content") or ""
    if content:
        parts.append(content)
    for e in message.get("embeds") or []:
        for key in ("title", "description", "url"):
            if e.get(key):
                parts.append(str(e[key]))
        a = e.get("author") or {}
        if a.get("name"):
            parts.append(str(a["name"]))
        f = e.get("footer") or {}
        if f.get("text"):
            parts.append(str(f["text"]))
        for field in e.get("fields") or []:
            if field.get("name"):
                parts.append(str(field["name"]))
            if field.get("value"):
                parts.append(str(field["value"]))
    for a in message.get("attachments") or []:
        if a.get("filename"):
            parts.append(str(a["filename"]))
    for s in message.get("stickers") or []:
        if s.get("name"):
            parts.append(str(s["name"]))
    return "\n".join(parts)


class ArchiveStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(str(self.path), timeout=30,
                                    check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute("PRAGMA busy_timeout=5000")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.create_function("regexp", 2, _regexp, deterministic=True)
        self.conn.executescript(_SCHEMA)
        self.conn.execute(
            "INSERT INTO meta(key, value) VALUES('schema_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(SCHEMA_VERSION),),
        )
        self.conn.commit()

    def close(self) -> None:
        with self._lock:
            self.conn.close()

    def commit(self) -> None:
        with self._lock:
            self.conn.commit()

    # -- metadata ---------------------------------------------------------
    def get_meta(self, key: str, default=None):
        with self._lock:
            row = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
            return row["value"] if row else default

    def set_meta(self, key: str, value) -> None:
        with self._lock:
            self.conn.execute(
                "INSERT INTO meta(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(key), str(value)),
            )

    # -- entities ---------------------------------------------------------
    def upsert_guild(self, guild: dict) -> None:
        now = time.time()
        gid = int(guild["id"])
        with self._lock:
            self.conn.execute(
                """INSERT INTO guilds(id,name,icon,raw_json,first_seen,updated_ts)
                   VALUES(?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET
                     name=excluded.name, icon=excluded.icon,
                     raw_json=excluded.raw_json, updated_ts=excluded.updated_ts""",
                (gid, guild.get("name"), guild.get("icon"),
                 json.dumps(guild, ensure_ascii=False), now, now),
            )

    def upsert_user(self, user: dict | None) -> None:
        if not user:
            return
        now = time.time()
        uid = int(user["id"])
        with self._lock:
            self.conn.execute(
                """INSERT INTO users(id,name,display_name,avatar,is_bot,raw_json,first_seen,updated_ts)
                   VALUES(?,?,?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET
                     name=excluded.name, display_name=excluded.display_name,
                     avatar=excluded.avatar, is_bot=excluded.is_bot,
                     raw_json=excluded.raw_json, updated_ts=excluded.updated_ts""",
                (uid, user.get("username") or user.get("name"),
                 user.get("global_name") or user.get("display_name") or user.get("username"),
                 user.get("avatar"), 1 if user.get("bot") else 0,
                 json.dumps(user, ensure_ascii=False), now, now),
            )

    def upsert_channel(self, channel: dict, guild_id: int | None = None,
                       is_thread: bool = False, archived: bool = False) -> None:
        now = time.time()
        cid = int(channel["id"])
        gid = guild_id or channel.get("guild_id")
        parent = channel.get("parent_id")
        if parent is None:
            meta = channel.get("thread_metadata") or {}
            if meta:
                parent = channel.get("parent_id")
        with self._lock:
            self.conn.execute(
                """INSERT INTO channels(id,guild_id,name,type,parent_id,position,
                        is_thread,archived,raw_json,first_seen,updated_ts)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET
                     guild_id=excluded.guild_id, name=excluded.name, type=excluded.type,
                     parent_id=excluded.parent_id, position=excluded.position,
                     is_thread=excluded.is_thread, archived=excluded.archived,
                     raw_json=excluded.raw_json, updated_ts=excluded.updated_ts""",
                (cid, gid, channel.get("name"), channel.get("type"), parent,
                 channel.get("position"), 1 if is_thread else 0,
                 1 if archived else 0,
                 json.dumps(channel, ensure_ascii=False), now, now),
            )

    # -- messages ---------------------------------------------------------
    def upsert_message(self, message: dict, guild_id: int | None = None,
                       channel_id: int | None = None, commit: bool = True) -> dict:
        now = time.time()
        mid = int(message["id"])
        cid = int(channel_id or message.get("channel_id"))
        gid = guild_id if guild_id is not None else message.get("guild_id")
        author = message.get("author") or {}
        created_at = message.get("timestamp") or message.get("created_at")
        created_ts = _iso_ts(created_at) or 0.0
        edited_at = message.get("edited_timestamp") or message.get("edited_at")
        ref = message.get("message_reference") or {}
        ref_id = ref.get("message_id")
        ref_ch = ref.get("channel_id")
        # reply_to is the normalized shape used by the platform payload
        reply_to = message.get("reply_to") or {}
        if reply_to:
            ref_id = ref_id or reply_to.get("message_id")
            ref_ch = ref_ch or reply_to.get("channel_id")
        attachments = message.get("attachments") or []
        embeds = message.get("embeds") or []
        stickers = message.get("stickers") or []
        content = message.get("content") or ""
        search = _search_text(message)

        incoming_edited_ts = _iso_ts(edited_at) or 0.0
        with self._lock:
            if self.conn.execute(
                    "SELECT 1 FROM deleted_messages WHERE id=?", (mid,)
            ).fetchone():
                return {"id": mid, "inserted": False, "deleted": True,
                        "attachments": [], "channel_id": cid, "guild_id": gid,
                        "released_blobs": []}
            existing = self.conn.execute(
                "SELECT id, edited_ts FROM messages WHERE id=?", (mid,)
            ).fetchone()
            # Live edits/backfill can race. Never let an older version overwrite
            # a newer edit already present in the archive.
            if (existing and (existing["edited_ts"] or 0) > incoming_edited_ts):
                return {"id": mid, "inserted": False, "stale": True,
                        "attachments": [], "channel_id": cid, "guild_id": gid,
                        "released_blobs": []}
            self.conn.execute(
                """INSERT INTO messages(
                       id,guild_id,channel_id,author_id,author_name,author_display_name,
                       author_is_bot,content,search_text,created_at,created_ts,edited_at,
                       edited_ts,message_type,reference_message_id,reference_channel_id,
                       pinned,has_attachments,has_embeds,raw_json,first_seen,updated_ts)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET
                       guild_id=excluded.guild_id, channel_id=excluded.channel_id,
                       author_id=excluded.author_id, author_name=excluded.author_name,
                       author_display_name=excluded.author_display_name,
                       author_is_bot=excluded.author_is_bot,
                       content=excluded.content, search_text=excluded.search_text,
                       created_at=excluded.created_at, created_ts=excluded.created_ts,
                       edited_at=excluded.edited_at, edited_ts=excluded.edited_ts,
                       message_type=excluded.message_type,
                       reference_message_id=excluded.reference_message_id,
                       reference_channel_id=excluded.reference_channel_id,
                       pinned=excluded.pinned, has_attachments=excluded.has_attachments,
                       has_embeds=excluded.has_embeds, raw_json=excluded.raw_json,
                       updated_ts=excluded.updated_ts""",
                (mid, gid, cid, int(author["id"]) if author.get("id") else None,
                 author.get("username") or author.get("name"),
                 author.get("global_name") or author.get("display_name"),
                 1 if author.get("bot") else 0, content, search, created_at,
                 created_ts, edited_at, incoming_edited_ts or None,
                 message.get("type"), int(ref_id) if ref_id else None,
                 int(ref_ch) if ref_ch else None, 1 if message.get("pinned") else 0,
                 1 if attachments else 0, 1 if embeds else 0,
                 json.dumps(message, ensure_ascii=False), now, now),
            )
            self.upsert_user(author)
            for a in attachments:
                self._upsert_attachment(a, mid, gid, cid, now)
            released = self._remove_stale_attachments(
                mid, {str(a.get("id")) for a in attachments}, now)
            if commit:
                self.conn.commit()
        return {"id": mid, "inserted": existing is None,
                "attachments": attachments, "channel_id": cid, "guild_id": gid,
                "released_blobs": released}

    def _upsert_attachment(self, a: dict, message_id: int, guild_id, channel_id,
                           now: float) -> None:
        aid = str(a.get("id"))
        if not aid or aid == "None":
            return
        self.conn.execute(
            """INSERT INTO attachments(
                   id,message_id,guild_id,channel_id,filename,content_type,size_bytes,
                   width,height,url,proxy_url,kind,first_seen,updated_ts)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET
                   message_id=excluded.message_id, guild_id=excluded.guild_id,
                   channel_id=excluded.channel_id, filename=excluded.filename,
                   content_type=excluded.content_type, size_bytes=excluded.size_bytes,
                   width=excluded.width, height=excluded.height,
                   url=excluded.url, proxy_url=excluded.proxy_url,
                   kind=excluded.kind, updated_ts=excluded.updated_ts""",
            (aid, message_id, guild_id, channel_id, a.get("filename"),
             a.get("content_type"), a.get("size"),
             a.get("width"), a.get("height"), a.get("url"), a.get("proxy_url"),
             a.get("kind") or "attachment", now, now),
        )

    def upsert_attachment_dict(self, a: dict, message_id: int, guild_id,
                               channel_id, commit: bool = False) -> None:
        now = time.time()
        with self._lock:
            self._upsert_attachment(a, message_id, guild_id, channel_id, now)
            if commit:
                self.conn.commit()

    def set_attachment_url(self, attachment_id: str, url: str) -> None:
        with self._lock:
            self.conn.execute(
                "UPDATE attachments SET url=?, updated_ts=? WHERE id=?",
                (url, time.time(), attachment_id),
            )
            self.conn.commit()

    def _remove_stale_attachments(self, message_id: int, keep_ids: set[str],
                                  now: float) -> list[dict]:
        released: list[dict] = []
        rows = self.conn.execute(
            "SELECT id, sha256 FROM attachments WHERE message_id=?", (message_id,)
        ).fetchall()
        for r in rows:
            if r["id"] not in keep_ids:
                if r["sha256"]:
                    released.extend(self._release_sha(r["sha256"], now))
                self.conn.execute("DELETE FROM attachments WHERE id=?", (r["id"],))
        return released

    def delete_message(self, message_id: int, commit: bool = True) -> list[dict]:
        """Delete a message and release its blob references. Returns blobs whose
        last reference was removed, with their remote paths, so the caller can
        delete the bytes from Nextcloud too."""
        now = time.time()
        released: list[dict] = []
        with self._lock:
            row = self.conn.execute(
                "SELECT channel_id FROM messages WHERE id=?", (message_id,)
            ).fetchone()
            self.conn.execute(
                "INSERT OR IGNORE INTO deleted_messages(id,channel_id,deleted_ts) "
                "VALUES(?,?,?)",
                (message_id, row["channel_id"] if row else None, now),
            )
            rows = self.conn.execute(
                "SELECT sha256 FROM attachments WHERE message_id=?", (message_id,)
            ).fetchall()
            for r in rows:
                if r["sha256"]:
                    released.extend(self._release_sha(r["sha256"], now))
            self.conn.execute("DELETE FROM messages WHERE id=?", (message_id,))
            if commit:
                self.conn.commit()
        return released

    def _release_sha(self, sha: str | None, now: float) -> list[dict]:
        if not sha:
            return []
        row = self.conn.execute(
            "SELECT ref_count, remote_path FROM blobs WHERE sha256=?", (sha,)
        ).fetchone()
        if not row:
            return []
        new_count = int(row["ref_count"]) - 1
        if new_count <= 0:
            self.conn.execute("DELETE FROM blobs WHERE sha256=?", (sha,))
            return [{"sha256": sha, "remote_path": row["remote_path"]}]
        self.conn.execute("UPDATE blobs SET ref_count=? WHERE sha256=?", (new_count, sha))
        return []

    # -- attachments / blobs ---------------------------------------------
    def pending_attachments(self, limit: int = 100) -> list[sqlite3.Row]:
        with self._lock:
            return self.conn.execute(
                """SELECT * FROM attachments
                   WHERE state='pending' AND url IS NOT NULL
                   ORDER BY first_seen LIMIT ?""", (limit,),
            ).fetchall()

    def get_attachment(self, attachment_id: str) -> sqlite3.Row | None:
        with self._lock:
            return self.conn.execute(
                "SELECT * FROM attachments WHERE id=?", (attachment_id,)
            ).fetchone()

    def get_blob(self, sha: str) -> sqlite3.Row | None:
        with self._lock:
            return self.conn.execute("SELECT * FROM blobs WHERE sha256=?", (sha,)).fetchone()

    def blob_path(self, sha: str, filename: str | None) -> str:
        name = _safe_filename(filename)
        return f"attachments/{sha[:2]}/{sha[2:4]}/{sha}/{name}"

    def set_attachment_error(self, attachment_id: str, error: str) -> None:
        with self._lock:
            self.conn.execute(
                "UPDATE attachments SET state='error', error=?, updated_ts=? WHERE id=?",
                (str(error)[:2000], time.time(), attachment_id),
            )
            self.conn.commit()

    def mark_attachment_downloaded(self, attachment_id: str, sha: str,
                                   size: int, content_type: str | None,
                                   local_path: str | None, remote_path: str | None = None,
                                   filename: str | None = None) -> None:
        """Link an attachment to a blob. Increments refcount only on first link."""
        now = time.time()
        with self._lock:
            att = self.conn.execute(
                "SELECT sha256, filename FROM attachments WHERE id=?", (attachment_id,)
            ).fetchone()
            old = att["sha256"] if att else None
            if old and old != sha:
                self._release_sha(old, now)
            blob = self.conn.execute("SELECT * FROM blobs WHERE sha256=?", (sha,)).fetchone()
            if blob is None:
                self.conn.execute(
                    """INSERT INTO blobs(sha256,size_bytes,content_type,filename,
                           local_path,remote_path,state,ref_count,first_seen,last_access_ts)
                       VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    (sha, size, content_type, _safe_filename(filename or (att["filename"] if att else None)),
                     local_path, remote_path,
                     "remote" if remote_path else "local", 1, now, now),
                )
            else:
                if old != sha:
                    self.conn.execute(
                        "UPDATE blobs SET ref_count=ref_count+1, last_access_ts=? WHERE sha256=?",
                        (now, sha),
                    )
                if local_path and not blob["local_path"]:
                    self.conn.execute("UPDATE blobs SET local_path=? WHERE sha256=?", (local_path, sha))
                if remote_path and not blob["remote_path"]:
                    self.conn.execute("UPDATE blobs SET remote_path=?, state='remote' WHERE sha256=?", (remote_path, sha))
            self.conn.execute(
                """UPDATE attachments SET sha256=?, state='ready', error=NULL,
                       content_type=COALESCE(content_type, ?), size_bytes=COALESCE(size_bytes, ?),
                       updated_ts=? WHERE id=?""",
                (sha, content_type, size, now, attachment_id),
            )
            self.conn.commit()

    def mark_blob_uploaded(self, sha: str, remote_path: str) -> None:
        now = time.time()
        with self._lock:
            self.conn.execute(
                "UPDATE blobs SET remote_path=?, state='remote', uploaded_ts=? WHERE sha256=?",
                (remote_path, now, sha),
            )
            self.conn.commit()

    def set_blob_local_path(self, sha: str, local_path: str | None) -> None:
        with self._lock:
            self.conn.execute("UPDATE blobs SET local_path=? WHERE sha256=?", (local_path, sha))
            self.conn.commit()

    def blobs_pending_upload(self, limit: int = 100) -> list[sqlite3.Row]:
        with self._lock:
            return self.conn.execute(
                """SELECT * FROM blobs WHERE state='local' AND local_path IS NOT NULL
                   ORDER BY first_seen LIMIT ?""", (limit,),
            ).fetchall()

    def blobs_for_sha(self, sha: str) -> list[sqlite3.Row]:
        with self._lock:
            return self.conn.execute(
                "SELECT * FROM attachments WHERE sha256=?", (sha,)
            ).fetchall()

    # -- backfill checkpoints --------------------------------------------
    def backfill_rows(self) -> list[sqlite3.Row]:
        with self._lock:
            return self.conn.execute("SELECT * FROM backfill_state ORDER BY guild_id, name").fetchall()

    def get_backfill(self, channel_id: int) -> sqlite3.Row | None:
        with self._lock:
            return self.conn.execute(
                "SELECT * FROM backfill_state WHERE channel_id=?", (channel_id,)
            ).fetchone()

    def update_backfill(self, channel_id: int, *, guild_id=None, name=None,
                        kind=None, status=None, cursor=None, messages_seen=None,
                        messages_added=None, attachments=None, error=None) -> None:
        now = time.time()
        with self._lock:
            row = self.conn.execute(
                "SELECT channel_id FROM backfill_state WHERE channel_id=?", (channel_id,)
            ).fetchone()
            if row is None:
                self.conn.execute(
                    """INSERT INTO backfill_state(channel_id,guild_id,name,kind,status,
                           cursor,messages_seen,messages_added,attachments,last_run,error)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                    (channel_id, guild_id, name, kind, status or "pending",
                     cursor or 0, messages_seen or 0, messages_added or 0,
                     attachments or 0, now, error),
                )
            else:
                sets = ["last_run=?"]
                vals = [now]
                for key, val in (("guild_id", guild_id), ("name", name),
                                 ("kind", kind), ("status", status),
                                 ("cursor", cursor), ("messages_seen", messages_seen),
                                 ("messages_added", messages_added),
                                 ("attachments", attachments), ("error", error)):
                    if val is not None:
                        sets.append(f"{key}=?")
                        vals.append(val)
                vals.append(channel_id)
                self.conn.execute(
                    f"UPDATE backfill_state SET {', '.join(sets)} WHERE channel_id=?",
                    vals,
                )
            self.conn.commit()

    def message_ids_for_channel(self, channel_id: int, max_id: int | None,
                                before_ts: float) -> list[int]:
        """IDs that existed before a reconcile scan started, for delete detection.
        Rows first seen or touched during the scan are excluded so a concurrent
        live event cannot be mistaken for a deleted message."""
        with self._lock:
            if max_id is None:
                rows = self.conn.execute(
                    "SELECT id FROM messages WHERE channel_id=? "
                    "AND first_seen < ? AND updated_ts < ?",
                    (int(channel_id), float(before_ts), float(before_ts)),
                ).fetchall()
            else:
                rows = self.conn.execute(
                    "SELECT id FROM messages WHERE channel_id=? AND id <= ? "
                    "AND first_seen < ? AND updated_ts < ?",
                    (int(channel_id), int(max_id), float(before_ts),
                     float(before_ts)),
                ).fetchall()
        return [int(r["id"]) for r in rows]

    # -- read-only explorer / query engine --------------------------------
    # Field definitions used by the visual query builder and by future
    # project-facing archive queries. Keep this whitelist here so both callers
    # share one implementation and one set of SQL rules.
    _QUERY_FIELDS = {
        "id": ("m.id", "id", False, None),
        "content": ("m.content", "text", False, None),
        "search_text": ("m.search_text", "text", False, None),
        "author_id": ("m.author_id", "id", False, None),
        "author_name": ("m.author_name", "text", False, None),
        "author_display_name": ("m.author_display_name", "text", False, None),
        "author_is_bot": ("m.author_is_bot", "bool", False, None),
        "channel_id": ("m.channel_id", "id", False, None),
        "channel_name": ("c.name", "text", False, None),
        "guild_id": ("m.guild_id", "id", False, None),
        "guild_name": ("g.name", "text", False, None),
        "created_ts": ("m.created_ts", "date", False, None),
        "created_at": ("m.created_at", "text", False, None),
        "edited_ts": ("m.edited_ts", "date", False, None),
        "edited_at": ("m.edited_at", "text", False, None),
        "message_type": ("m.message_type", "number", False, None),
        "reference_message_id": ("m.reference_message_id", "id", False, None),
        "reference_channel_id": ("m.reference_channel_id", "id", False, None),
        "pinned": ("m.pinned", "bool", False, None),
        "has_attachments": ("m.has_attachments", "bool", False, None),
        "has_embeds": ("m.has_embeds", "bool", False, None),
        "content_length": ("LENGTH(m.content)", "number", False, None),
        "attachment_count": (
            "(SELECT COUNT(*) FROM attachments a WHERE a.message_id=m.id)",
            "number", False, None),
        "attachment_filename": ("a.filename", "text", True, "filename"),
        "attachment_content_type": ("a.content_type", "text", True, "content_type"),
        "attachment_size_bytes": ("a.size_bytes", "number", True, "size_bytes"),
        "attachment_kind": ("a.kind", "text", True, "kind"),
        "attachment_state": ("a.state", "text", True, "state"),
    }
    _QUERY_FIELD_ALIASES = {
        "attachment_name": "attachment_filename",
        "attachment_type": "attachment_content_type",
        "attachment_size": "attachment_size_bytes",
    }
    _QUERY_OPS = {
        "contains", "not_contains", "equals", "not_equals", "starts_with",
        "ends_with", "in", "not_in", "gt", "gte", "lt", "lte", "between",
        "is_empty", "is_not_empty", "is_true", "is_false", "is_null",
        "not_null", "regex",
    }

    @staticmethod
    def _query_arg(value, kind: str):
        if kind == "date":
            if isinstance(value, (int, float)):
                return float(value)
            text = str(value).strip()
            try:
                return float(text)
            except ValueError:
                ts = _iso_ts(text)
                if ts is None:
                    raise ArchiveQueryError(f"invalid date value: {value!r}")
                return ts
        if kind in ("id", "number"):
            # IDs are 64-bit Discord snowflakes: never round-trip them through
            # float, which would silently lose precision.
            if isinstance(value, bool):
                return int(value)
            if isinstance(value, int):
                return value
            text = str(value).strip()
            try:
                return int(text)
            except ValueError:
                try:
                    return int(float(text))
                except (TypeError, ValueError):
                    raise ArchiveQueryError(f"invalid number/id value: {value!r}")
        if kind == "bool":
            return 1 if value in (True, 1, "1", "true", "yes") else 0
        return "" if value is None else str(value)

    @staticmethod
    def _like_value(value) -> str:
        escaped = (str(value).replace("\\", "\\\\")
                   .replace("%", "\\%").replace("_", "\\_"))
        return f"%{escaped}%"

    def _query_cmp(self, expr: str, op: str, value, kind: str,
                   field: str) -> tuple[str, list]:
        op = op.lower()
        if op not in self._QUERY_OPS:
            raise ArchiveQueryError(f"unsupported operator: {op}")
        if op == "is_null":
            return f"{expr} IS NULL", []
        if op == "not_null":
            return f"{expr} IS NOT NULL", []
        if op == "is_empty":
            return f"({expr} IS NULL OR {expr}='')", []
        if op == "is_not_empty":
            return f"({expr} IS NOT NULL AND {expr}<>'')", []
        if op == "is_true":
            return f"{expr}=1", []
        if op == "is_false":
            return f"{expr}=0", []
        if op in ("in", "not_in"):
            vals = value if isinstance(value, (list, tuple)) else str(value).split(",")
            vals = [v for v in vals if str(v).strip() != ""]
            if not vals:
                return ("1=0" if op == "in" else "1=1"), []
            args = [self._query_arg(v, kind) for v in vals]
            mark = ",".join("?" for _ in args)
            neg = "NOT " if op == "not_in" else ""
            return f"{expr} {neg}IN ({mark})", args
        if op == "between":
            vals = value if isinstance(value, (list, tuple)) else str(value).split(",")
            if len(vals) != 2:
                raise ArchiveQueryError("between needs exactly two values")
            args = [self._query_arg(v, kind) for v in vals]
            return f"{expr} BETWEEN ? AND ?", args
        if op == "contains":
            # The trigram FTS index makes long substring searches much faster.
            text = str(value)
            if field == "search_text" and len(text) >= 3:
                match = self._fts_query(text)
                return ("m.id IN (SELECT rowid FROM messages_fts "
                        "WHERE messages_fts MATCH ?)"), [match]
            return f"{expr} LIKE ? ESCAPE '\\'", [self._like_value(value)]
        if op == "not_contains":
            text = str(value)
            if field == "search_text" and len(text) >= 3:
                match = self._fts_query(text)
                return ("m.id NOT IN (SELECT rowid FROM messages_fts "
                        "WHERE messages_fts MATCH ?)"), [match]
            return f"{expr} NOT LIKE ? ESCAPE '\\'", [self._like_value(value)]
        if op == "starts_with":
            escaped = (str(value).replace("\\", "\\\\")
                       .replace("%", "\\%").replace("_", "\\_"))
            return f"{expr} LIKE ? ESCAPE '\\'", [f"{escaped}%"]
        if op == "ends_with":
            escaped = (str(value).replace("\\", "\\\\")
                       .replace("%", "\\%").replace("_", "\\_"))
            return f"{expr} LIKE ? ESCAPE '\\'", [f"%{escaped}"]
        if op in ("equals", "not_equals", "gt", "gte", "lt", "lte", "regex"):
            arg = self._query_arg(value, kind)
            sql_op = {
                "equals": "=", "not_equals": "!=", "gt": ">", "gte": ">=",
                "lt": "<", "lte": "<=", "regex": "REGEXP",
            }[op]
            return f"{expr} {sql_op} ?", [arg]
        raise ArchiveQueryError(f"unsupported operator: {op}")

    def _compile_filter(self, node, depth: int = 0) -> tuple[str, list]:
        if depth > 8:
            raise ArchiveQueryError("query is nested too deeply")
        if not isinstance(node, dict):
            raise ArchiveQueryError("filter must be an object")
        kind = node.get("kind")
        if kind == "group" or (kind is None and "children" in node):
            op = str(node.get("op") or "AND").upper()
            if op not in ("AND", "OR", "NOT"):
                raise ArchiveQueryError(f"unsupported group operator: {op}")
            children = node.get("children") or []
            if not isinstance(children, list):
                raise ArchiveQueryError("group children must be a list")
            if not children:
                return "", []
            parts, args = [], []
            for child in children:
                sql, child_args = self._compile_filter(child, depth + 1)
                if sql:
                    parts.append(sql)
                    args.extend(child_args)
            if not parts:
                return "", []
            joined = " AND ".join(parts)
            if op == "NOT":
                return f"NOT ({joined})", args
            return f"({joined})" if op == "AND" else f"({' OR '.join(parts)})", args
        if kind not in (None, "condition"):
            raise ArchiveQueryError(f"unsupported filter kind: {kind}")

        field = str(node.get("field") or "").strip()
        field = self._QUERY_FIELD_ALIASES.get(field, field)
        spec = self._QUERY_FIELDS.get(field)
        if spec is None:
            raise ArchiveQueryError(f"unknown field: {field}")
        expr, value_kind, is_attachment, attachment_col = spec
        op = str(node.get("op") or "contains").lower()
        value = node.get("value")
        if not is_attachment:
            return self._query_cmp(expr, op, value, value_kind, field)
        # Attachment fields mean "at least one attachment matches".
        inner_sql, args = self._query_cmp(
            f"a.{attachment_col}", op, value, value_kind, field)
        return (f"EXISTS (SELECT 1 FROM attachments a "
                f"WHERE a.message_id=m.id AND {inner_sql})"), args

    def _query_where(self, filter_ast=None, *, guild_id=None, channel_id=None,
                     author_id=None, before=None, after=None,
                     include_bots=True) -> tuple[str, list]:
        where: list[str] = []
        args: list = []
        if guild_id:
            where.append("m.guild_id=?")
            args.append(int(guild_id))
        if channel_id:
            where.append("m.channel_id=?")
            args.append(int(channel_id))
        if author_id:
            where.append("m.author_id=?")
            args.append(int(author_id))
        if after:
            where.append("m.created_ts >= ?")
            args.append(float(after))
        if before:
            where.append("m.created_ts <= ?")
            args.append(float(before))
        if not include_bots:
            where.append("m.author_is_bot=0")
        if filter_ast:
            sql, filter_args = self._compile_filter(filter_ast)
            if sql:
                where.append(sql)
                args.extend(filter_args)
        return ((" WHERE " + " AND ".join(where)) if where else ""), args

    @staticmethod
    def _message_select() -> str:
        return ("""
            SELECT m.*, c.name AS channel_name, c.is_thread AS channel_is_thread,
                   g.name AS guild_name
            FROM messages m
            LEFT JOIN channels c ON c.id=m.channel_id
            LEFT JOIN guilds g ON g.id=m.guild_id
        """)

    def query_messages(self, filter_ast=None, *, guild_id=None, channel_id=None,
                       author_id=None, before=None, after=None, limit=50,
                       cursor=None, order="desc", include_bots=True,
                       include_total=True) -> dict:
        """Run a read-only message query. Shared by the web API and (later) the
        worker query bridge, so project-facing archive search uses this exact
        path too."""
        limit = max(1, min(int(limit), 200))
        order = "asc" if str(order).lower() == "asc" else "desc"
        sql_where, args = self._query_where(
            filter_ast, guild_id=guild_id, channel_id=channel_id,
            author_id=author_id, before=before, after=after,
            include_bots=include_bots)
        page_where, page_args = sql_where, list(args)
        if cursor:
            try:
                cts, cid = str(cursor).split(":", 1)
                cmp = ">" if order == "asc" else "<"
                page_where += (f" AND (m.created_ts, m.id) {cmp} (?, ?)"
                               if page_where else
                               f" WHERE (m.created_ts, m.id) {cmp} (?, ?)")
                page_args.extend([float(cts), int(cid)])
            except (TypeError, ValueError):
                raise ArchiveQueryError("invalid cursor")
        direction = order.upper()
        sql = (self._message_select() + page_where +
               f" ORDER BY m.created_ts {direction}, m.id {direction} LIMIT ?")
        page_args.append(limit + 1)
        with self._lock:
            rows = self.conn.execute(sql, page_args).fetchall()
            total = None
            if include_total:
                count_sql = ("SELECT COUNT(*) FROM messages m "
                             "LEFT JOIN channels c ON c.id=m.channel_id "
                             "LEFT JOIN guilds g ON g.id=m.guild_id" + sql_where)
                total = int(self.conn.execute(count_sql, args).fetchone()[0])
        has_more = len(rows) > limit
        rows = rows[:limit]
        items = [dict(r) for r in rows]
        next_cursor = None
        if has_more and items:
            last = items[-1]
            next_cursor = f"{last['created_ts']}:{last['id']}"
        return {"items": items, "next_cursor": next_cursor,
                "has_more": has_more, "total": total, "order": order}

    def get_messages(self, message_ids, channel_id=None) -> list[dict]:
        ids = [int(x) for x in message_ids if x is not None]
        if not ids:
            return []
        marks = ",".join("?" for _ in ids)
        where = f" WHERE m.id IN ({marks})"
        args = list(ids)
        if channel_id:
            where += " AND m.channel_id=?"
            args.append(int(channel_id))
        with self._lock:
            rows = self.conn.execute(
                self._message_select() + where +
                " ORDER BY m.created_ts ASC, m.id ASC", args).fetchall()
        return [dict(r) for r in rows]

    def get_message(self, message_id, channel_id=None) -> dict | None:
        rows = self.get_messages([message_id], channel_id=channel_id)
        return rows[0] if rows else None

    def message_context(self, message_id, *, channel_id=None, before=20,
                        after=20) -> dict | None:
        anchor = self.get_message(message_id, channel_id=channel_id)
        if not anchor:
            return None
        cid = int(anchor["channel_id"])
        before = max(0, min(int(before), 100))
        after = max(0, min(int(after), 100))
        with self._lock:
            older = self.conn.execute(
                self._message_select() +
                " WHERE m.channel_id=? AND (m.created_ts,m.id) < (?,?)"
                " ORDER BY m.created_ts DESC, m.id DESC LIMIT ?",
                (cid, anchor["created_ts"], anchor["id"], before + 1)).fetchall()
            newer = self.conn.execute(
                self._message_select() +
                " WHERE m.channel_id=? AND (m.created_ts,m.id) > (?,?)"
                " ORDER BY m.created_ts ASC, m.id ASC LIMIT ?",
                (cid, anchor["created_ts"], anchor["id"], after + 1)).fetchall()
        has_older = len(older) > before
        has_newer = len(newer) > after
        older = older[:before]
        newer = newer[:after]
        items = [dict(r) for r in reversed(older)] + [anchor] + [dict(r) for r in newer]
        return {"anchor_id": str(anchor["id"]), "items": items,
                "has_older": has_older, "has_newer": has_newer}

    def attachments_for_messages(self, message_ids) -> dict[str, list[dict]]:
        ids = [int(x) for x in message_ids if x is not None]
        if not ids:
            return {}
        marks = ",".join("?" for _ in ids)
        with self._lock:
            rows = self.conn.execute(
                f"SELECT * FROM attachments WHERE message_id IN ({marks})"
                " ORDER BY message_id, first_seen, id", ids).fetchall()
        out: dict[str, list[dict]] = {}
        for row in rows:
            d = dict(row)
            out.setdefault(str(d["message_id"]), []).append(d)
        return out

    def guilds(self) -> list[dict]:
        with self._lock:
            rows = self.conn.execute(
                """SELECT g.id, g.name, g.icon,
                          (SELECT COUNT(*) FROM channels c WHERE c.guild_id=g.id)
                              AS channel_count,
                          (SELECT COUNT(*) FROM messages m WHERE m.guild_id=g.id)
                              AS message_count,
                          (SELECT MAX(m.created_ts) FROM messages m
                           WHERE m.guild_id=g.id) AS last_ts
                   FROM guilds g ORDER BY message_count DESC, g.name""").fetchall()
        return [dict(r) for r in rows]

    def channels(self, guild_id=None) -> list[dict]:
        where, args = "", []
        if guild_id:
            where, args = " WHERE c.guild_id=?", [int(guild_id)]
        with self._lock:
            rows = self.conn.execute(
                """SELECT c.id, c.guild_id, c.name, c.type, c.parent_id,
                          c.position, c.is_thread, c.archived,
                          (SELECT COUNT(*) FROM messages m WHERE m.channel_id=c.id)
                              AS message_count,
                          (SELECT MAX(m.created_ts) FROM messages m
                           WHERE m.channel_id=c.id) AS last_ts
                   FROM channels c""" + where +
                " ORDER BY c.guild_id, c.position, c.name", args).fetchall()
        return [dict(r) for r in rows]

    def users(self, guild_id=None, q=None, limit=50) -> list[dict]:
        where, args = [], []
        if guild_id:
            where.append("m.guild_id=?")
            args.append(int(guild_id))
        if q:
            where.append("(m.author_name LIKE ? ESCAPE '\\' OR "
                         "m.author_display_name LIKE ? ESCAPE '\\')")
            like = self._like_value(q)
            args.extend([like, like])
        sql_where = (" WHERE " + " AND ".join(where)) if where else ""
        with self._lock:
            rows = self.conn.execute(
                "SELECT m.author_id AS id, MAX(m.author_name) AS name, "
                "MAX(m.author_display_name) AS display_name, "
                "MAX(m.author_is_bot) AS is_bot, COUNT(*) AS message_count, "
                "MAX(m.created_ts) AS last_ts FROM messages m" + sql_where +
                (" AND " if sql_where else " WHERE ") + "m.author_id IS NOT NULL "
                "GROUP BY m.author_id ORDER BY message_count DESC LIMIT ?",
                args + [max(1, min(int(limit), 200))]).fetchall()
        return [dict(r) for r in rows]

    def heatmap(self, *, guild_id=None, channel_id=None, after=None, before=None,
                bucket_seconds=3600, tz_offset_minutes=0,
                include_bots=True) -> dict:
        bucket_seconds = 86400 if int(bucket_seconds) == 86400 else 3600
        offset = int(tz_offset_minutes) * 60
        where, args = [], []
        if guild_id:
            where.append("m.guild_id=?")
            args.append(int(guild_id))
        if channel_id:
            where.append("m.channel_id=?")
            args.append(int(channel_id))
        if after:
            where.append("m.created_ts >= ?")
            args.append(float(after))
        if before:
            where.append("m.created_ts <= ?")
            args.append(float(before))
        if not include_bots:
            where.append("m.author_is_bot=0")
        sql_where = (" WHERE " + " AND ".join(where)) if where else ""
        with self._lock:
            rows = self.conn.execute(
                "SELECT CAST((m.created_ts + ?) / ? AS INTEGER) AS b, "
                "COUNT(*) AS messages, COUNT(DISTINCT m.author_id) AS authors, "
                "COALESCE(SUM(LENGTH(m.content)),0) AS chars, "
                "COALESCE(SUM(m.has_attachments),0) AS attachments "
                "FROM messages m" + sql_where +
                " GROUP BY b ORDER BY b",
                [offset, bucket_seconds] + args).fetchall()
        items = []
        for row in rows:
            d = dict(row)
            d["bucket_start"] = int(d["b"]) * bucket_seconds - offset
            d["heat"] = int(d["messages"]) + 12 * max(int(d["authors"]) - 1, 0)
            d.pop("b", None)
            items.append(d)
        return {"bucket_seconds": bucket_seconds, "items": items,
                "tz_offset_minutes": int(tz_offset_minutes)}

    # -- search / stats ---------------------------------------------------
    @staticmethod
    def _fts_query(query: str) -> str:
        # Keep FTS syntax predictable: treat user input as literal terms/phrases.
        q = query.strip()
        if not q:
            return q
        if q.startswith('"') and q.endswith('"'):
            return q
        return '"' + q.replace('"', '""') + '"'

    def search(self, query: str, *, guild_id=None, channel_id=None, author_id=None,
               before=None, after=None, limit=50, cursor=None,
               include_bots=True) -> dict:
        limit = max(1, min(int(limit), 200))
        where = []
        args: list = []
        q = query.strip()
        if q:
            # FTS5 trigram needs >= 3 characters. Short terms (common in
            # Chinese, e.g. 上海) fall back to a substring scan; at friend-server
            # scale this is still fast. Multi-term queries are ANDed, like
            # Discord's default search behavior.
            terms = [t for t in q.split() if t]
            use_like = any(len(t) < 3 for t in terms) or len(q) < 3
            if use_like:
                for term in terms:
                    escaped = (term.replace("\\", "\\\\")
                               .replace("%", "\\%").replace("_", "\\_"))
                    where.append("m.search_text LIKE ? ESCAPE '\\'")
                    args.append(f"%{escaped}%")
            else:
                match = " AND ".join(self._fts_query(t) for t in terms)
                where.append("m.id IN (SELECT rowid FROM messages_fts WHERE messages_fts MATCH ?)")
                args.append(match)
        if guild_id:
            where.append("m.guild_id=?")
            args.append(int(guild_id))
        if channel_id:
            where.append("m.channel_id=?")
            args.append(int(channel_id))
        if author_id:
            where.append("m.author_id=?")
            args.append(int(author_id))
        if before:
            where.append("m.created_ts < ?")
            args.append(float(before))
        if after:
            where.append("m.created_ts > ?")
            args.append(float(after))
        if not include_bots:
            where.append("m.author_is_bot=0")
        if cursor:
            # cursor is "created_ts:id"
            try:
                cts, cid = str(cursor).split(":", 1)
                where.append("(m.created_ts, m.id) < (?, ?)")
                args.extend([float(cts), int(cid)])
            except Exception:
                pass
        sql_where = (" WHERE " + " AND ".join(where)) if where else ""
        sql = (f"SELECT m.* FROM messages m{sql_where} "
               f"ORDER BY m.created_ts DESC, m.id DESC LIMIT ?")
        args.append(limit + 1)
        with self._lock:
            rows = self.conn.execute(sql, args).fetchall()
        has_more = len(rows) > limit
        rows = rows[:limit]
        items = [dict(r) for r in rows]
        next_cursor = None
        if has_more and items:
            last = items[-1]
            next_cursor = f"{last['created_ts']}:{last['id']}"
        return {"items": items, "next_cursor": next_cursor}

    def stats(self, guild_id=None) -> dict:
        with self._lock:
            one = lambda sql, args=(): self.conn.execute(sql, args).fetchone()[0]
            if not guild_id:
                return {
                    "messages": one("SELECT COUNT(*) FROM messages"),
                    "channels": one("SELECT COUNT(*) FROM channels"),
                    "threads": one("SELECT COUNT(*) FROM channels WHERE is_thread=1"),
                    "users": one("SELECT COUNT(*) FROM users"),
                    "attachments": one("SELECT COUNT(*) FROM attachments"),
                    "blobs": one("SELECT COUNT(*) FROM blobs"),
                    "blob_bytes": one("SELECT COALESCE(SUM(size_bytes),0) FROM blobs"),
                    "remote_blobs": one("SELECT COUNT(*) FROM blobs WHERE state='remote'"),
                    "local_blobs": one("SELECT COUNT(*) FROM blobs WHERE state='local'"),
                    "oldest_ts": one("SELECT COALESCE(MIN(created_ts),0) FROM messages"),
                    "newest_ts": one("SELECT COALESCE(MAX(created_ts),0) FROM messages"),
                }
            gid = int(guild_id)
            sha_sql = ("SELECT DISTINCT sha256 FROM attachments "
                       "WHERE guild_id=? AND sha256 IS NOT NULL")
            return {
                "messages": one("SELECT COUNT(*) FROM messages WHERE guild_id=?", (gid,)),
                "channels": one("SELECT COUNT(*) FROM channels WHERE guild_id=?", (gid,)),
                "threads": one("SELECT COUNT(*) FROM channels WHERE guild_id=? AND is_thread=1", (gid,)),
                "users": one("SELECT COUNT(DISTINCT author_id) FROM messages WHERE guild_id=?", (gid,)),
                "attachments": one("SELECT COUNT(*) FROM attachments WHERE guild_id=?", (gid,)),
                "blobs": one(f"SELECT COUNT(*) FROM ({sha_sql})", (gid,)),
                "blob_bytes": one("SELECT COALESCE(SUM(size_bytes),0) FROM blobs "
                                  f"WHERE sha256 IN ({sha_sql})", (gid,)),
                "remote_blobs": one("SELECT COUNT(*) FROM blobs WHERE state='remote' "
                                    f"AND sha256 IN ({sha_sql})", (gid,)),
                "local_blobs": one("SELECT COUNT(*) FROM blobs WHERE state='local' "
                                   f"AND sha256 IN ({sha_sql})", (gid,)),
                "oldest_ts": one("SELECT COALESCE(MIN(created_ts),0) FROM messages WHERE guild_id=?", (gid,)),
                "newest_ts": one("SELECT COALESCE(MAX(created_ts),0) FROM messages WHERE guild_id=?", (gid,)),
            }
