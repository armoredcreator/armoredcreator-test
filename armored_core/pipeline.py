from __future__ import annotations
from .database import Database
from .models import State
from .services import Publisher, StudioService, VisionService
from .storage import Storage

class Pipeline:
    def __init__(self, db: Database, storage: Storage, vision: VisionService, studio: StudioService, publisher: Publisher):
        self.db, self.storage = db, storage
        self.vision, self.studio, self.publisher = vision, studio, publisher

    def run(self, item_id: int) -> None:
        item = self.db.get(item_id)
        if item.state == State.PUBLISHED:
            self.cleanup(item_id); return
        try:
            if item.state in (State.RECEIVED, State.RECOVERY, State.FAILED):
                self.db.transition(item_id, State.VISION, "pipeline-start")
            item = self.db.get(item_id)
            if item.state == State.VISION:
                v = self.vision.identify(item)
                self.db.set_vision(item_id, v.affiliate_name, v.affiliate_url)
                self.db.transition(item_id, State.STUDIO, "vision-complete")
            item = self.db.get(item_id)
            if item.state == State.STUDIO:
                result = self.studio.process(item)
                self.db.set_result(item_id, result)
                self.db.transition(item_id, State.PUBLISHING, "studio-complete")
            item = self.db.get(item_id)
            if item.state == State.PUBLISHING:
                self.db.publication_started(item_id)
                if not self.publisher.is_published(item):
                    result = self.publisher.publish(item)
                    if not result.confirmed: raise RuntimeError("publication not confirmed")
                    self.db.publication_confirmed(item_id, result.message_id or f"published-{item_id}")
                else:
                    pub = self.db.publication(item_id)
                    self.db.publication_confirmed(item_id, (pub["published_message_id"] if pub else None) or f"existing-{item_id}")
                self.db.transition(item_id, State.PUBLISHED, "publication-confirmed")
                self.cleanup(item_id)
        except Exception as exc:
            self.db.fail(item_id, f"{type(exc).__name__}: {exc}")
            raise

    def cleanup(self, item_id: int) -> None:
        item = self.db.get(item_id)
        if item.state != State.PUBLISHED: raise RuntimeError("cleanup is allowed only after PUBLISHED")
        for path in (item.working_path, item.result_path):
            if path and path.exists(): path.unlink()
