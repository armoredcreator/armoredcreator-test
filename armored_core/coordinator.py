from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .database import Database
from .models import State
from .pipeline import Pipeline
from .recovery import Recovery
from .services import SyncService
from .storage import Storage


class Coordinator:
    """Single composition root for the isolated ArmoredCreator pipeline."""

    def __init__(self, db, storage, vision, studio, publisher, source=None):
        self.db=db
        self.storage=storage
        self.sync=SyncService(db,storage)
        self.pipeline=Pipeline(db,storage,vision,studio,publisher)
        self.recovery=Recovery(db,storage,vision,studio,publisher)
        self.source=source

    @classmethod
    def build(cls, root: Path | None=None, bindings: Any | None=None):
        storage=Storage(root)
        db=Database(storage.database/"armoredcreator.db")
        if bindings is None:
            from ArmoredHub.service import ArmoredHub
            from ArmoredStudio.service import ArmoredStudio
            from ArmoredVision.service import ArmoredVision
            from ArmoredSync.service import LocalSource, TelegramReader, TelegramSource
            vision=ArmoredVision()
            studio=ArmoredStudio(storage.root)
            publisher=ArmoredHub(storage.root)
            if os.getenv("ARMORED_REAL_TELEGRAM", "0") == "1":
                api_id = os.getenv("TELEGRAM_API_ID")
                api_hash = os.getenv("TELEGRAM_API_HASH")
                if not api_id or not api_hash:
                    raise RuntimeError("TELEGRAM_API_ID e TELEGRAM_API_HASH são obrigatórios")
                reader = TelegramReader(storage.root, int(api_id), api_hash)
                source = TelegramSource(storage.root, reader)
            else:
                source=LocalSource(storage.root/"input")
            return cls(db,storage,vision,studio,publisher,source)
        return cls(db,storage,bindings.vision,bindings.studio,bindings.publisher,bindings.source)

    def ingest_once(self):
        if self.source is None:
            raise RuntimeError("Sync source não configurado")
        message=self.source.fetch_next()
        if message is None:
            return None
        return self.sync.ingest(
            Path(message.source_path),
            str(message.telegram_message_id),
            getattr(message,"source_id","telegram"),
            getattr(message,"topic_id",None),
            getattr(message,"topic_name",None),
            getattr(message,"original_url",None),
        )

    def run(self,item_id:int)->None:
        self.pipeline.run(item_id)

    def recover(self,item_id:int)->None:
        self.recovery.reconcile(item_id)

    def close(self)->None:
        self.db.close()

    def process_next(self):
        item_id=self.ingest_once()
        if item_id is None: return None
        self.run(item_id)
        return item_id

    def recover_pending(self):
        states=(
            State.RECEIVED.value,State.VISION.value,State.STUDIO.value,
            State.PUBLISHING.value,State.RECOVERY.value,State.FAILED.value,
        )
        placeholders=",".join("?" for _ in states)
        rows=self.db.conn.execute(
            f"SELECT id FROM items WHERE state IN ({placeholders}) ORDER BY id",
            states,
        ).fetchall()
        recovered=[]
        for row in rows:
            item_id=int(row["id"])
            self.recover(item_id)
            recovered.append(item_id)
        return recovered
