from __future__ import annotations

from .database import Database
from .models import PublicationCheck, State
from .services import Publisher, StudioService, VisionService
from .storage import Storage


class Pipeline:
    def __init__(self, db: Database, storage: Storage, vision: VisionService, studio: StudioService, publisher: Publisher, lease_seconds: int = 300):
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        self.db, self.storage = db, storage
        self.vision, self.studio, self.publisher = vision, studio, publisher
        self.lease_seconds = lease_seconds

    def run(self, item_id: int, worker_id: str = "pipeline") -> None:
        if self.db.get(item_id).state == State.PUBLISHED:
            self.cleanup(item_id)
            return
        if not self.db.claim(item_id, worker_id, self.lease_seconds):
            raise RuntimeError("item-already-claimed")
        try:
            self.run_claimed(item_id, worker_id)
        finally:
            self.db.release(item_id, worker_id)

    def run_claimed(self, item_id: int, worker_id: str | None = None) -> None:
        item = self.db.get(item_id)
        if worker_id is not None and not self.db.renew_claim(item_id, worker_id):
            raise RuntimeError("claim-lost")
        if item.state == State.PUBLISHED:
            self.cleanup(item_id)
            return

        try:
            if not self.db.original_intact(item_id):
                original = item.original_path
                if original is None or not original.exists():
                    raise FileNotFoundError("immutable-original-missing")
                raise IOError("immutable-original-integrity-failed")

            if item.state in (State.RECEIVED, State.RECOVERY):
                if worker_id is not None and not self.db.renew_claim(item_id, worker_id):
                    raise RuntimeError("claim-lost")
                self.db.transition(item_id, State.VISION, "pipeline-start")

            item = self.db.get(item_id)
            if item.state == State.FAILED:
                raise RuntimeError("FAILED item requires deterministic recovery before pipeline.run")

            if item.state == State.VISION:
                if worker_id is not None and not self.db.renew_claim(item_id, worker_id):
                    raise RuntimeError("claim-lost")
                v = self.vision.identify(item)
                self.db.set_vision(item_id, v.affiliate_name, v.affiliate_url)
                self.db.transition(item_id, State.STUDIO, "vision-complete")

            item = self.db.get(item_id)
            if item.state == State.STUDIO:
                if not item.affiliate_name:
                    raise RuntimeError("studio-requires-affiliate-metadata")
                if worker_id is not None and not self.db.renew_claim(item_id, worker_id):
                    raise RuntimeError("claim-lost")
                studio = self.studio.process(item)
                if not studio.working_path.is_file() or not studio.result_path.is_file():
                    raise FileNotFoundError("studio-did-not-produce-required-files")
                self.db.set_working(item_id, studio.working_path)
                self.db.set_result(item_id, studio.result_path)
                self.db.transition(item_id, State.PUBLISHING, "studio-complete")

            item = self.db.get(item_id)
            if item.state == State.PUBLISHING:
                if not item.result_path or not item.result_path.is_file():
                    raise FileNotFoundError("publication-result-missing")
                self.db.publication_started(item_id)

                if worker_id is not None and not self.db.renew_claim(item_id, worker_id):
                    raise RuntimeError("claim-lost")
                check = self.publisher.check_publication(item)
                if check == PublicationCheck.UNKNOWN:
                    raise RuntimeError("publication-check-uncertain-refusing-to-publish")
                if check == PublicationCheck.ABSENT:
                    if worker_id is not None and not self.db.renew_claim(item_id, worker_id):
                        raise RuntimeError("claim-lost")
                    result = self.publisher.publish(item)
                    if not result.confirmed:
                        raise RuntimeError("publication-not-confirmed")
                    self.db.publication_confirmed(
                        item_id, result.message_id or f"published-{item_id}"
                    )
                else:
                    pub = self.db.publication(item_id)
                    self.db.publication_confirmed(
                        item_id,
                        (pub["published_message_id"] if pub else None) or f"existing-{item_id}",
                    )

                self.db.transition(item_id, State.PUBLISHED, "publication-confirmed")
                self.cleanup(item_id)

        except Exception as exc:
            can_fail = worker_id is None or self.db.is_claimed_by(item_id, worker_id)
            if can_fail and self.db.get(item_id).state != State.PUBLISHED:
                self.db.fail(item_id, f"{type(exc).__name__}: {exc}")
            raise

    def cleanup(self, item_id: int) -> None:
        item = self.db.get(item_id)
        if item.state != State.PUBLISHED:
            raise RuntimeError("cleanup-is-allowed-only-after-PUBLISHED")
        for path in (item.working_path, item.result_path):
            if path and path.exists():
                path.unlink()
