from __future__ import annotations
from pathlib import Path
import shutil
from .database import Database
from .models import PublicationCheck, State
from .services import Publisher, StudioService, VisionService, VisionUnresolvedError, PublicationUnknownError
from .storage import Storage

class Pipeline:
    def __init__(self, db: Database, storage: Storage, vision: VisionService, studio: StudioService, publisher: Publisher):
        self.db, self.storage = db, storage
        self.vision, self.studio, self.publisher = vision, studio, publisher

    def run(self, item_id: str) -> None:
        item = self.db.get(item_id)
        if item.state == State.PUBLISHED:
            if not item.cleanup_completed:
                self.cleanup(item_id)
            return
        try:
            self.db.record_attempt(item_id)
            if not item.original_path.is_file():
                raise FileNotFoundError(f"immutable-original-missing: {item.original_path}")
            if item.state in (State.RECEIVED, State.RECOVERY):
                self.db.transition(item_id, State.VISION, "pipeline-start")
            item = self.db.get(item_id)
            if item.state == State.WAITING_VISION:
                return
            if item.state == State.FAILED:
                raise RuntimeError("FAILED item requires deterministic recovery before pipeline.run")
            if item.state == State.VISION:
                try:
                    v = self.vision.identify(item)
                except VisionUnresolvedError as exc:
                    self.db.mark_vision_waiting(item_id, str(exc))
                    return
                self.db.set_vision(item_id, v.affiliate_name, v.affiliate_url)
                self.db.transition(item_id, State.STUDIO, "vision-complete")
            item = self.db.get(item_id)
            if item.state == State.STUDIO:
                if not item.affiliate_name:
                    raise RuntimeError("studio-requires-affiliate-metadata")
                studio = self.studio.process(item)
                if studio.working_path is not None:
                    if not studio.working_path.is_file():
                        raise FileNotFoundError("studio-working-file-missing")
                    self.db.set_working(item_id, studio.working_path)
                if not studio.result_path.is_file():
                    raise FileNotFoundError("studio-result-file-missing")
                self.db.set_result(item_id, studio.result_path)
                self.db.transition(item_id, State.PUBLISHING, "studio-complete")
            item = self.db.get(item_id)
            if item.state == State.PUBLISHING:
                if not item.result_path or not item.result_path.is_file():
                    raise FileNotFoundError("publication-result-missing")
                check = self.publisher.check_publication(item)
                if check == PublicationCheck.UNKNOWN:
                    raise RuntimeError("publication-check-uncertain-refusing-to-publish")
                self.db.publication_started(item_id)
                if check == PublicationCheck.ABSENT:
                    try:
                        result = self.publisher.publish(item)
                    except PublicationUnknownError as exc:
                        # A Telegram timeout is an unresolved external side
                        # effect, not a terminal processing failure. Persist
                        # RECOVERY so Coordinator.recover_pending() can
                        # reconcile later without republishing blindly.
                        self.db.transition(item_id, State.RECOVERY, str(exc))
                        return
                    if not result.confirmed:
                        raise RuntimeError("publication-not-confirmed")
                    self.db.publication_confirmed(item_id, result.message_id or f"published-{item_id}")
                else:
                    pub = self.db.publication(item_id)
                    self.db.publication_confirmed(
                        item_id,
                        (pub["published_message_id"] if pub else None) or f"existing-{item_id}",
                    )
                self.db.transition(item_id, State.PUBLISHED, "publication-confirmed")
                self.cleanup(item_id)
        except Exception as exc:
            self.db.fail(item_id, f"{type(exc).__name__}: {exc}")
            raise

    def cleanup(self, item_id: int) -> None:
        item = self.db.get(item_id)
        if item.state != State.PUBLISHED:
            raise RuntimeError("cleanup-is-allowed-only-after-PUBLISHED")
        workspace = item.workspace.resolve()
        original = item.original_path.resolve()
        if workspace != original.parent.resolve():
            raise RuntimeError("cleanup-workspace-mismatch")
        if not workspace.is_dir():
            self.db.mark_cleanup_completed(item_id)
            return
        for path in workspace.iterdir():
            resolved = path.resolve()
            if resolved == original:
                continue
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
        self.db.mark_cleanup_completed(item_id)
