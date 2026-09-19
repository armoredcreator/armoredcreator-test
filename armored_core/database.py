from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

from .models import Item, State


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, timeout=5.0)
        self.conn.row_factory = sqlite3.Row
        self._init()

    def _init(self) -> None:
        self.conn.executescript("""
        PRAGMA journal_mode=WAL;
        PRAGMA busy_timeout=5000;
        CREATE TABLE IF NOT EXISTS items (
            id INTEGER PRIMARY KEY,
            telegram_message_id TEXT NOT NULL UNIQUE,
            state TEXT NOT NULL,
            original_path TEXT,
            original_size INTEGER,
            original_sha256 TEXT,
            working_path TEXT,
            result_path TEXT,
            affiliate_name TEXT,
            affiliate_url TEXT,
            claimed_by TEXT,
            claimed_at TEXT,
            last_error TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS state_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id INTEGER NOT NULL,
            old_state TEXT,
            new_state TEXT NOT NULL,
            reason TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS publications (
            item_id INTEGER PRIMARY KEY,
            idempotency_key TEXT NOT NULL UNIQUE,
            published_message_id TEXT,
            confirmed INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """)
        self._ensure_columns()
        self.conn.commit()

    def _ensure_columns(self) -> None:
        cols = {row["name"] for row in self.conn.execute("PRAGMA table_info(items)")}
        additions = {
            "original_size": "INTEGER",
            "original_sha256": "TEXT",
            "claimed_by": "TEXT",
            "claimed_at": "TEXT",
        }
        for name, definition in additions.items():
            if name not in cols:
                self.conn.execute(f"ALTER TABLE items ADD COLUMN {name} {definition}")

    @staticmethod
    def publication_key(item_id: int) -> str:
        return f"armoredcreator:item:{item_id}"

    def create_item(self, telegram_message_id: str) -> int:
        cur = self.conn.execute(
            "INSERT INTO items (telegram_message_id,state,original_path) VALUES (?,?,NULL)",
            (telegram_message_id, State.RECEIVED.value),
        )
        item_id = int(cur.lastrowid)
        self.conn.execute(
            "INSERT INTO state_events (item_id,new_state,reason) VALUES (?,?,?)",
            (item_id, State.RECEIVED.value, "ingest-created"),
        )
        self.conn.commit()
        return item_id

    def get(self, item_id: int) -> Item:
        row = self.conn.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
        if row is None:
            raise KeyError(item_id)
        original = Path(row["original_path"]) if row["original_path"] else None
        workspace = original.parent if original else self.path.parent.parent / "videos" / str(item_id)
        return Item(
            row["id"], row["telegram_message_id"], State(row["state"]),
            workspace, original,
            Path(row["working_path"]) if row["working_path"] else None,
            Path(row["result_path"]) if row["result_path"] else None,
            row["affiliate_name"], row["affiliate_url"],
        )

    def set_original(self, item_id: int, path: Path, size: int, sha256: str) -> None:
        self.conn.execute(
            "UPDATE items SET original_path=?, original_size=?, original_sha256=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (str(path), size, sha256, item_id),
        )
        self.conn.commit()

    def transition(self, item_id: int, new_state: State, reason: str = "") -> None:
        old = self.get(item_id).state
        allowed = {
            State.RECEIVED: {State.VISION, State.RECOVERY, State.FAILED},
            State.VISION: {State.STUDIO, State.RECOVERY, State.FAILED},
            State.STUDIO: {State.PUBLISHING, State.RECOVERY, State.FAILED},
            State.PUBLISHING: {State.PUBLISHED, State.RECOVERY, State.FAILED},
            State.PUBLISHED: set(),
            State.RECOVERY: {State.VISION, State.STUDIO, State.PUBLISHING, State.PUBLISHED, State.FAILED},
            State.FAILED: {State.RECOVERY},
        }
        if new_state not in allowed[old]:
            raise ValueError(f"invalid-state-transition:{old}->{new_state}")
        cur = self.conn.execute(
            "UPDATE items SET state=?, last_error=NULL, updated_at=CURRENT_TIMESTAMP WHERE id=? AND state=?",
            (new_state.value, item_id, old.value),
        )
        if cur.rowcount != 1:
            raise RuntimeError("concurrent-state-transition")
        self.conn.execute(
            "INSERT INTO state_events (item_id,old_state,new_state,reason) VALUES (?,?,?,?)",
            (item_id, old.value, new_state.value, reason),
        )
        self.conn.commit()

    def set_vision(self, item_id: int, affiliate_name: str, affiliate_url: str) -> None:
        self.conn.execute("UPDATE items SET affiliate_name=?, affiliate_url=?, updated_at=CURRENT_TIMESTAMP WHERE id=?", (affiliate_name, affiliate_url, item_id))
        self.conn.commit()

    def set_working(self, item_id: int, path: Path) -> None:
        self.conn.execute("UPDATE items SET working_path=?, updated_at=CURRENT_TIMESTAMP WHERE id=?", (str(path), item_id))
        self.conn.commit()

    def set_result(self, item_id: int, path: Path) -> None:
        self.conn.execute("UPDATE items SET result_path=?, updated_at=CURRENT_TIMESTAMP WHERE id=?", (str(path), item_id))
        self.conn.commit()

    def fail(self, item_id: int, error: str) -> None:
        old = self.get(item_id).state
        if old not in {State.RECEIVED, State.VISION, State.STUDIO, State.PUBLISHING, State.RECOVERY}:
            raise ValueError(f"invalid-failure-transition:{old}->FAILED")
        cur = self.conn.execute("UPDATE items SET state=?, last_error=?, updated_at=CURRENT_TIMESTAMP WHERE id=? AND state=?", (State.FAILED.value, error, item_id, old.value))
        if cur.rowcount != 1:
            raise RuntimeError("concurrent-failure-transition")
        self.conn.execute("INSERT INTO state_events (item_id,old_state,new_state,reason) VALUES (?,?,?,?)", (item_id, old.value, State.FAILED.value, error))
        self.conn.commit()

    def original_intact(self, item_id: int) -> bool:
        row = self.conn.execute("SELECT original_path, original_size, original_sha256 FROM items WHERE id=?", (item_id,)).fetchone()
        if row is None or not row["original_path"] or row["original_size"] is None or not row["original_sha256"]:
            return False
        path = Path(row["original_path"])
        if not path.is_file() or path.stat().st_size != row["original_size"]:
            return False
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest() == row["original_sha256"]

    def publication_started(self, item_id: int) -> None:
        self.conn.execute("INSERT INTO publications(item_id,idempotency_key) VALUES(?,?) ON CONFLICT(item_id) DO UPDATE SET updated_at=CURRENT_TIMESTAMP", (item_id, self.publication_key(item_id)))
        self.conn.commit()

    def publication_confirmed(self, item_id: int, message_id: str) -> None:
        self.conn.execute("UPDATE publications SET published_message_id=?, confirmed=1, updated_at=CURRENT_TIMESTAMP WHERE item_id=?", (message_id, item_id))
        self.conn.commit()

    def publication(self, item_id: int):
        return self.conn.execute("SELECT * FROM publications WHERE item_id=?", (item_id,)).fetchone()

    def claim(self, item_id: int, worker_id: str, lease_seconds: int = 300) -> bool:
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        cur = self.conn.execute(
            "UPDATE items SET claimed_by=?, claimed_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP "
            "WHERE id=? AND state != ? AND (claimed_by IS NULL OR claimed_by=? OR claimed_at IS NULL OR claimed_at < datetime('now', ?))",
            (worker_id, item_id, State.PUBLISHED.value, worker_id, "-" + str(lease_seconds) + " seconds"),
        )
        self.conn.commit()
        return cur.rowcount == 1

    def is_claimed_by(self, item_id: int, worker_id: str) -> bool:
        row = self.conn.execute(
            "SELECT claimed_by, state FROM items WHERE id=?", (item_id,)
        ).fetchone()
        return bool(row and row["claimed_by"] == worker_id and row["state"] != State.PUBLISHED.value)

    def renew_claim(self, item_id: int, worker_id: str) -> bool:
        cur = self.conn.execute("UPDATE items SET claimed_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP WHERE id=? AND claimed_by=? AND state != ?", (item_id, worker_id, State.PUBLISHED.value))
        self.conn.commit()
        return cur.rowcount == 1

    def backup_to(self, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        target = sqlite3.connect(destination)
        try:
            self.conn.backup(target)
            target.commit()
        finally:
            target.close()

    def release(self, item_id: int, worker_id: str) -> None:
        self.conn.execute("UPDATE items SET claimed_by=NULL, claimed_at=NULL, updated_at=CURRENT_TIMESTAMP WHERE id=? AND claimed_by=?", (item_id, worker_id))
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()
