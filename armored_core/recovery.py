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
            self.pipeline.cleanup(item_id)
            return

        if not item.original_path.is_file():
            raise FileNotFoundError(
                f"cannot-recover-without-immutable-original: {item.original_path}"
            )

        self.db.transition(item_id, State.RECOVERY, "startup-recovery")
        item = self.db.get(item_id)

        # Only publication-stage recovery asks the external publisher.
        pub = self.db.publication(item_id)
        if item.state in (State.RECOVERY, State.PUBLISHING) and pub:
            if pub["confirmed"]:
                self.db.transition(item_id, State.PUBLISHED, "db-publication-already-confirmed")
                self.pipeline.cleanup(item_id)
                return

            if self.publisher.is_published(item):
                self.db.publication_confirmed(
                    item_id,
                    pub["published_message_id"] or f"existing-{item_id}",
                )
                self.db.transition(item_id, State.PUBLISHED, "publisher-confirms-existing")
                self.pipeline.cleanup(item_id)
                return

        # Reconcile durable files, not the previous state alone.
        if item.result_path and item.result_path.is_file():
            self.db.transition(item_id, State.PUBLISHING, "durable-result-exists")
        elif item.working_path and item.working_path.is_file() and item.affiliate_name:
            self.db.transition(item_id, State.STUDIO, "durable-working-exists")
        elif item.affiliate_name:
            self.db.transition(item_id, State.STUDIO, "rebuild-working-from-original")
        else:
            self.db.transition(item_id, State.VISION, "rebuild-vision-from-original")

        self.pipeline.run(item_id)
