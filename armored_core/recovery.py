from __future__ import annotations
from .database import Database
from .models import PublicationCheck, State
from .pipeline import Pipeline
from .services import Publisher, StudioService, VisionService
from .storage import Storage

class Recovery:
    def __init__(self, db: Database, storage: Storage, vision: VisionService, studio: StudioService, publisher: Publisher):
        self.db, self.storage = db, storage
        self.pipeline = Pipeline(db, storage, vision, studio, publisher)

    def reconcile(self, item_id: int) -> None:
        item = self.db.get(item_id)

        if item.state == State.PUBLISHED:
            self.pipeline.cleanup(item_id)
            return

        if not item.original_path.is_file():
            raise FileNotFoundError(
                f"cannot-recover-without-immutable-original: {item.original_path}"
            )

        self.db.record_recovery(item_id)
        self.db.transition(item_id, State.RECOVERY, "startup-recovery")
        item = self.db.get(item_id)

        pub = self.db.publication(item_id)
        if pub:
            if pub["confirmed"]:
                self.db.transition(item_id, State.PUBLISHED, "db-publication-already-confirmed")
                self.pipeline.cleanup(item_id)
                return

            check = self.pipeline.publisher.check_publication(item)
            if check == PublicationCheck.UNKNOWN:
                raise RuntimeError("publication-check-uncertain-recovery-stopped")
            if check == PublicationCheck.CONFIRMED:
                self.db.publication_confirmed(
                    item_id,
                    pub["published_message_id"] or f"existing-{item_id}",
                )
                self.db.transition(item_id, State.PUBLISHED, "publisher-confirms-existing")
                self.pipeline.cleanup(item_id)
                return

        working = item.working_path or self.storage.working(item_id)
        result = item.result_path
        if not result and item.affiliate_url:
            result = self.storage.result(
                item_id,
                item.affiliate_url,
                item.affiliate_name,
            )

        if result and result.is_file():
            if item.result_path is None:
                self.db.set_result(item_id, result)
            self.db.transition(item_id, State.PUBLISHING, "durable-result-exists")
        elif working.is_file() and item.affiliate_name:
            if item.working_path is None:
                self.db.set_working(item_id, working)
            self.db.transition(item_id, State.STUDIO, "durable-working-exists")
        elif item.affiliate_name:
            self.db.transition(item_id, State.STUDIO, "rebuild-working-from-original")
        else:
            self.db.transition(item_id, State.VISION, "rebuild-vision-from-original")

        self.pipeline.run(item_id)
