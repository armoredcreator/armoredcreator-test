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
                # A confirmed publication is only terminal if its external
                # identity is also durable. Without the real Telegram ID we
                # cannot safely verify it later.
                message_id = pub["published_message_id"]
                if not message_id:
                    raise RuntimeError("confirmed-publication-without-real-message-id")
                self.db.transition(item_id, State.PUBLISHED, "db-publication-already-confirmed")
                self.pipeline.cleanup(item_id)
                return

            check = self.pipeline.publisher.check_publication(item)
            if check == PublicationCheck.UNKNOWN:
                raise RuntimeError("publication-check-uncertain-recovery-stopped")
            if check == PublicationCheck.CONFIRMED:
                refreshed = self.db.publication(item_id)
                message_id = refreshed["published_message_id"] if refreshed else None
                if not message_id:
                    raise RuntimeError("telegram-confirmed-without-real-message-id")
                self.db.publication_confirmed(item_id, str(message_id))
                self.db.transition(item_id, State.PUBLISHED, "publisher-confirms-existing")
                self.pipeline.cleanup(item_id)
                return

        # A DB path is a durable claim, not proof that the artifact still exists.
        # If the previous synthetic WORKING artifact was removed, clear the stale
        # path so Studio deterministically falls back to the immutable ORIGINAL.
        working = item.working_path
        if working is not None and not working.is_file():
            self.db.set_working(item_id, None)
            working = None
        working = working or self.storage.working(item_id)

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
