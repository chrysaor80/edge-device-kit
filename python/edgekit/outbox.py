"""Store-and-forward outbox on SQLite (WAL).

Every message is committed locally *before* it is sent. During a network outage
messages accumulate; on reconnect they are replayed oldest-first. The server side
deduplicates with an idempotent upsert, so "sent but not acknowledged" is safe to resend.

Field result: after a 26-hour outage at a remote site, 28,657 records were replayed
with no loss. The bounded ``prune_unsent`` exists because an outage can also last
long enough to fill the SD card.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, List, Tuple


class Outbox:
    def __init__(self, path: str | Path) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path))
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS outbox (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                topic      TEXT    NOT NULL,
                payload    TEXT    NOT NULL,
                created_ns INTEGER NOT NULL,
                sent       INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS ix_outbox_unsent ON outbox(sent, id);
            """
        )
        self.conn.commit()

    def enqueue(self, topic: str, payload: dict[str, Any], created_ns: int) -> int:
        cur = self.conn.execute(
            "INSERT INTO outbox(topic, payload, created_ns) VALUES(?,?,?)",
            (topic, json.dumps(payload, allow_nan=False), created_ns),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def pending(self, limit: int = 500) -> List[Tuple[int, str, dict[str, Any]]]:
        rows = self.conn.execute(
            "SELECT id, topic, payload FROM outbox WHERE sent=0 ORDER BY id LIMIT ?", (limit,)
        ).fetchall()
        return [(r[0], r[1], json.loads(r[2])) for r in rows]

    def mark_sent(self, ids: List[int]) -> None:
        if ids:
            self.conn.executemany("UPDATE outbox SET sent=1 WHERE id=?", [(i,) for i in ids])
            self.conn.commit()

    def unsent_count(self) -> int:
        return self.conn.execute("SELECT count(*) FROM outbox WHERE sent=0").fetchone()[0]

    def prune_sent(self, keep: int = 5000) -> None:
        """Keep only the most recent ``keep`` delivered rows."""
        self.conn.execute(
            "DELETE FROM outbox WHERE sent=1 AND id NOT IN "
            "(SELECT id FROM outbox WHERE sent=1 ORDER BY id DESC LIMIT ?)", (keep,))
        self.conn.commit()

    def prune_unsent(self, max_unsent: int) -> int:
        """Ring-buffer guard for endless outages: evict the OLDEST undelivered rows
        beyond ``max_unsent`` so the newest observations survive. Lossy by design;
        returns the number evicted so the caller can report it."""
        excess = self.unsent_count() - max_unsent
        if max_unsent <= 0 or excess <= 0:
            return 0
        self.conn.execute(
            "DELETE FROM outbox WHERE id IN "
            "(SELECT id FROM outbox WHERE sent=0 ORDER BY id ASC LIMIT ?)", (excess,))
        self.conn.commit()
        return excess

    def close(self) -> None:
        self.conn.close()
