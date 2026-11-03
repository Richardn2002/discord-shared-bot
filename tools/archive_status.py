#!/usr/bin/env python3
"""Print archive statistics / data-quality checks from the local SQLite DB."""
from __future__ import annotations

import argparse
import sqlite3
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def bar(n: int, max_n: int, width: int = 40) -> str:
    if max_n <= 0:
        return ""
    return "█" * max(1 if n else 0, round(n / max_n * width))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "archive" / "archive.db"))
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--monthly", action="store_true")
    ap.add_argument("--daily", action="store_true")
    args = ap.parse_args()

    c = sqlite3.connect(args.db)
    c.row_factory = sqlite3.Row
    one = lambda sql: c.execute(sql).fetchone()[0]

    print("== summary ==")
    for label, sql in [
        ("messages", "select count(*) from messages"),
        ("channels", "select count(*) from channels where is_thread=0"),
        ("threads", "select count(*) from channels where is_thread=1"),
        ("users", "select count(*) from users"),
        ("attachments", "select count(*) from attachments"),
        ("  ready", "select count(*) from attachments where state='ready'"),
        ("  pending", "select count(*) from attachments where state='pending'"),
        ("  error", "select count(*) from attachments where state='error'"),
        ("blobs remote", "select count(*) from blobs where state='remote'"),
        ("blob bytes", "select coalesce(sum(size_bytes),0) from blobs"),
        ("oldest", "select min(created_at) from messages"),
        ("newest", "select max(created_at) from messages"),
    ]:
        print(f"{label:14} {one(sql)}")

    print("\n== top channels/threads ==")
    for r in c.execute("""select ch.name, ch.is_thread, count(m.id) n
        from messages m join channels ch on ch.id=m.channel_id
        group by m.channel_id order by n desc limit ?""", (args.top,)):
        kind = "thread" if r["is_thread"] else "channel"
        print(f"{r['n']:7}  {kind:7}  {r['name']}")

    print("\n== authors ==")
    for r in c.execute("""select author_name, author_is_bot, count(*) n
        from messages group by author_id order by n desc limit ?""", (args.top,)):
        print(f"{r['n']:7}  bot={r['author_is_bot']}  {r['author_name']}")

    if args.monthly:
        print("\n== monthly ==")
        rows = c.execute("""select substr(created_at,1,7) k, count(*) n
            from messages group by k order by k""").fetchall()
        mx = max((r["n"] for r in rows), default=0)
        for r in rows:
            print(f"{r['k']} {r['n']:7} {bar(r['n'], mx)}")

    if args.daily:
        print("\n== daily (last 60 days) ==")
        rows = c.execute("""select substr(created_at,1,10) k, count(*) n
            from messages group by k order by k desc limit 60""").fetchall()
        mx = max((r["n"] for r in rows), default=0)
        for r in reversed(rows):
            print(f"{r['k']} {r['n']:5} {bar(r['n'], mx)}")

    print("\n== backfill ==")
    for r in c.execute("select status,count(*) n from backfill_state group by status"):
        print(f"{r['status']:10} {r['n']}")
    errs = c.execute("select name,error from backfill_state where status='error' limit 10").fetchall()
    for r in errs:
        print("  error:", r["name"], r["error"])

    print("\n== attachment errors ==")
    for r in c.execute("""select filename,error from attachments
        where state='error' order by updated_ts desc limit 20"""):
        print(f"  {r['filename']}: {r['error']}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
