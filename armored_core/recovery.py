from __future__ import annotations
from .database import Database
from .models import State
from .pipeline import Pipeline
from .services import Publisher, StudioService, VisionService
from .storage import Storage

class Recovery:
    def __init__(self, db: Database, storage: Storage, vision: VisionService, studio: StudioService, publisher: Publisher):
        self.db, self.storage = db, storage
        self.pipeline = Pipeline(db, storage, vision, studio, publisher)
        self.publisher = publisher

    def reconcile(self, item_id: int) -> None:
        item = self.db.get(item_id)
        if item.state == State.PUBLISHED:
            self.pipeline.cleanup(item_id); return
        self.db.transition(item_id, State.RECOVERY, "startup-recovery")
        item = self.db.get(item_id)
        if self.publisher.is_published(item):
            self.db.transition(item_id, State.PUBLISHED, "publisher-confirms-existing")
            self.pipeline.cleanup(item_id); return
        if item.result_path and item.result_path.exists():
            self.db.transition(item_id, State.PUBLISHING, "result-exists")
        elif item.working_path and item.working_path.exists() and item.affiliate_name:
            self.db.transition(item_id, State.STUDIO, "working-exists")
        else:
            self.db.transition(item_id, State.VISION, "rebuild-from-original")
        self.pipeline.run(item_id)
