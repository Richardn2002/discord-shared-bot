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

    def stats(self) -> dict:
        with self._lock:
            one = lambda sql: self.conn.execute(sql).fetchone()[0]
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
