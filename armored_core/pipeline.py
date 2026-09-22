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

                # Create the durable publication intent BEFORE any Telegram
                # reconciliation. A missing DB row is never evidence of
                # absence in the external system.
                self.db.publication_started(item_id)

                # Production ArmoredHub owns the complete idempotent decision
                # through publish_once(). Test publishers keep the legacy
                # contract so existing deterministic tests remain valid.
                publish_once = getattr(self.publisher, "publish_once", None)
                if callable(publish_once):
                    try:
                        result = publish_once(item)
                    except PublicationUnknownError as exc:
                        self.db.transition(item_id, State.RECOVERY, str(exc))
                        return

                    if not result.confirmed:
                        raise RuntimeError("publication-not-confirmed")
                    if not result.message_id:
                        raise RuntimeError("confirmed-publication-without-message-id")
                    self.db.publication_confirmed(item_id, str(result.message_id))
                else:
                    check = self.publisher.check_publication(item)

                    # UNKNOWN is an external side-effect uncertainty. It is
                    # never FAILED and never permits blind republishing.
                    if check == PublicationCheck.UNKNOWN:
                        self.db.transition(
                            item_id,
                            State.RECOVERY,
                            "publication-check-uncertain-refusing-to-publish",
                        )
                        return

                    if check == PublicationCheck.ABSENT:
                        try:
                            result = self.publisher.publish(item)
                        except PublicationUnknownError as exc:
                            self.db.transition(item_id, State.RECOVERY, str(exc))
                            return

                        if not result.confirmed:
                            raise RuntimeError("publication-not-confirmed")
                        if not result.message_id:
                            raise RuntimeError("confirmed-publication-without-message-id")
                        self.db.publication_confirmed(item_id, str(result.message_id))
                    else:
                        pub = self.db.publication(item_id)
                        message_id = pub["published_message_id"] if pub else None
                        if not message_id:
                            self.db.transition(
                                item_id,
                                State.RECOVERY,
                                "confirmed-publication-without-real-message-id",
                            )
                            return
                        self.db.publication_confirmed(item_id, str(message_id))

                self.db.transition(item_id, State.PUBLISHED, "publication-confirmed")
                self.cleanup(item_id)
        except Exception as exc:
            current = self.db.get(item_id)
            if current.state == State.PUBLISHED:
                raise
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
