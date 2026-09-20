from __future__ import annotations
import sqlite3
from pathlib import Path
from .models import Item, State

class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self._init()

    def _init(self) -> None:
        self.conn.executescript("""
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS items (
            id INTEGER PRIMARY KEY,
            telegram_message_id TEXT NOT NULL UNIQUE,
            source_id TEXT NOT NULL DEFAULT 'telegram',
            topic_id INTEGER,
            topic_name TEXT,
            original_url TEXT,
            state TEXT NOT NULL,
            original_path TEXT NOT NULL,
            original_sha256 TEXT,
            working_path TEXT,
            result_path TEXT,
            affiliate_name TEXT,
            affiliate_url TEXT,
            attempts INTEGER NOT NULL DEFAULT 0,
            recovery_count INTEGER NOT NULL DEFAULT 0,
            cleanup_completed INTEGER NOT NULL DEFAULT 0,
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
        self.conn.commit()
        self._migrate_columns()

    def _migrate_columns(self) -> None:
        existing = {r[1] for r in self.conn.execute("PRAGMA table_info(items)").fetchall()}
        migrations = [
            ("source_id", "ALTER TABLE items ADD COLUMN source_id TEXT NOT NULL DEFAULT 'telegram'"),
            ("topic_id", "ALTER TABLE items ADD COLUMN topic_id INTEGER"),
            ("topic_name", "ALTER TABLE items ADD COLUMN topic_name TEXT"),
            ("original_url", "ALTER TABLE items ADD COLUMN original_url TEXT"),
            ("original_sha256", "ALTER TABLE items ADD COLUMN original_sha256 TEXT"),
            ("attempts", "ALTER TABLE items ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0"),
            ("recovery_count", "ALTER TABLE items ADD COLUMN recovery_count INTEGER NOT NULL DEFAULT 0"),
            ("cleanup_completed", "ALTER TABLE items ADD COLUMN cleanup_completed INTEGER NOT NULL DEFAULT 0"),
        ]
        for name, sql in migrations:
            if name not in existing:
                self.conn.execute(sql)
        self.conn.commit()

    def reserve_item(
        self,
        telegram_message_id: str,
        source_id: str = "telegram",
        topic_id: int | None = None,
        topic_name: str | None = None,
        original_url: str | None = None,
    ) -> int:
        cur = self.conn.execute(
            "INSERT INTO items (telegram_message_id,source_id,topic_id,topic_name,original_url,state,original_path) VALUES (?,?,?,?,?,?,?)",
            (
                telegram_message_id, source_id, topic_id, topic_name, original_url,
                State.RECEIVED.value, "",
            ),
        )
        item_id = int(cur.lastrowid)
        self.conn.execute(
            "INSERT INTO state_events (item_id,new_state,reason) VALUES (?,?,?)",
            (item_id, State.RECEIVED.value, "ingest-reserved"),
        )
        return item_id

    def finalize_original_path(self, item_id: int, path: Path, sha256: str) -> None:
        self.conn.execute(
            "UPDATE items SET original_path=?, original_sha256=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (str(path), sha256, item_id),
        )
        self.conn.commit()

    def rollback_ingest(self) -> None:
        self.conn.rollback()

    def create_item(
        self, telegram_message_id: str, original_path: Path,
        source_id: str = "telegram", topic_id: int | None = None,
        topic_name: str | None = None, original_url: str | None = None,
    ) -> int:
        cur = self.conn.execute(
            "INSERT INTO items (telegram_message_id,source_id,topic_id,topic_name,original_url,state,original_path) VALUES (?,?,?,?,?,?,?)",
            (telegram_message_id, source_id, topic_id, topic_name, original_url,
             State.RECEIVED.value, str(original_path)),
        )
        item_id = int(cur.lastrowid)
        self.conn.execute(
            "INSERT INTO state_events (item_id,new_state,reason) VALUES (?,?,?)",
            (item_id, State.RECEIVED.value, "ingest-reserved"),
        )
        self.conn.commit()
        return item_id

    def get(self, item_id: int) -> Item:
        row = self.conn.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
        if row is None:
            raise KeyError(item_id)
        return Item(
            row["id"], row["telegram_message_id"], State(row["state"]),
            Path(row["original_path"]).parent, Path(row["original_path"]),
            Path(row["working_path"]) if row["working_path"] else None,
            Path(row["result_path"]) if row["result_path"] else None,
            row["affiliate_name"], row["affiliate_url"],
            row["source_id"], row["original_url"], row["topic_id"], row["topic_name"],
            row["original_sha256"], row["attempts"], row["recovery_count"], bool(row["cleanup_completed"]),
        )

    def record_attempt(self, item_id: int) -> None:
        self.conn.execute(
            "UPDATE items SET attempts=attempts+1, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (item_id,),
        )
        self.conn.commit()

    def record_recovery(self, item_id: int) -> None:
        self.conn.execute(
            "UPDATE items SET recovery_count=recovery_count+1, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (item_id,),
        )
        self.conn.commit()

    def transition(self, item_id: int, new_state: State, reason: str = "") -> None:
        old = self.get(item_id).state
        self.conn.execute(
            "UPDATE items SET state=?, last_error=NULL, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (new_state.value, item_id),
        )
        self.conn.execute(
            "INSERT INTO state_events (item_id,old_state,new_state,reason) VALUES (?,?,?,?)",
            (item_id, old.value, new_state.value, reason),
        )
        self.conn.commit()

    def set_vision(self, item_id: int, affiliate_name: str, affiliate_url: str) -> None:
        self.conn.execute(
            "UPDATE items SET affiliate_name=?, affiliate_url=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (affiliate_name, affiliate_url, item_id),
        )
        self.conn.commit()

    def set_working(self, item_id: int, path: Path) -> None:
        self.conn.execute(
            "UPDATE items SET working_path=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (str(path), item_id),
        )
        self.conn.commit()

    def set_result(self, item_id: int, path: Path) -> None:
        self.conn.execute(
            "UPDATE items SET result_path=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (str(path), item_id),
        )
        self.conn.commit()

    def fail(self, item_id: int, error: str) -> None:
        self.conn.execute(
            "UPDATE items SET state=?, last_error=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (State.FAILED.value, error, item_id),
        )
        self.conn.execute(
            "INSERT INTO state_events (item_id,new_state,reason) VALUES (?,?,?)",
            (item_id, State.FAILED.value, error),
        )
        self.conn.commit()

    def mark_cleanup_completed(self, item_id: int) -> None:
        self.conn.execute(
            "UPDATE items SET cleanup_completed=1, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (item_id,),
        )
        self.conn.commit()

    def publication_started(self, item_id: int) -> None:
        self.conn.execute(
            "INSERT INTO publications(item_id,idempotency_key) VALUES(?,?) "
            "ON CONFLICT(item_id) DO UPDATE SET updated_at=CURRENT_TIMESTAMP",
            (item_id, f"armoredcreator:content:{self.get(item_id).telegram_message_id}"),
        )
        self.conn.commit()

    def publication_confirmed(self, item_id: int, message_id: str) -> None:
        self.conn.execute(
            "UPDATE publications SET published_message_id=?, confirmed=1, updated_at=CURRENT_TIMESTAMP WHERE item_id=?",
            (message_id, item_id),
        )
        self.conn.commit()

    def publication(self, item_id: int):
        return self.conn.execute(
            "SELECT * FROM publications WHERE item_id=?", (item_id,)
        ).fetchone()

    def close(self) -> None:
        self.conn.close()
