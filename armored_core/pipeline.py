from __future__ import annotations
from pathlib import Path
import logging
import shutil
from .database import Database
from .models import PublicationCheck, State
from .services import Publisher, StudioService, VisionService, VisionUnresolvedError, PublicationUnknownError
from .storage import Storage

class Pipeline:
    def __init__(self, db: Database, storage: Storage, vision: VisionService, studio: StudioService, publisher: Publisher):
        self.db, self.storage = db, storage
        self.vision, self.studio, self.publisher = vision, studio, publisher
        self.log = logging.getLogger(__name__)
        self._shutdown_checker = lambda: False

    def set_shutdown_checker(self, checker) -> None:
        """Attach the Coordinator shutdown signal without coupling layers."""
        self._shutdown_checker = checker

    def run(self, item_id: str) -> None:
        item = self.db.get(item_id)
        self.log.info("[PIPELINE][ITEM %s] início state=%s", item.content_id, item.state.value)
        if item.state == State.PUBLISHED:
            if not item.cleanup_completed:
                self.cleanup(item_id)
            return
        try:
            self.db.record_attempt(item_id)
            if not item.original_path.is_file():
                raise FileNotFoundError(f"immutable-original-missing: {item.original_path}")
            if item.state == State.RECEIVED:
                self.db.transition(item_id, State.VISION, "pipeline-start")
            elif item.state == State.RECOVERY:
                # RECOVERY may be entered after Telegram publication becomes
                # ambiguous. If a durable result already exists, publication
                # recovery must NEVER rebuild Vision/Studio/RVC.
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
                    self.db.transition(item_id, State.PUBLISHING, "recovery-resume-publication")
                elif item.working_path and item.working_path.is_file() and item.affiliate_name:
                    self.db.transition(item_id, State.STUDIO, "recovery-resume-studio")
                elif item.affiliate_name:
                    self.db.transition(item_id, State.STUDIO, "recovery-rebuild-working")
                else:
                    self.db.transition(item_id, State.VISION, "recovery-rebuild-vision")
            item = self.db.get(item_id)
            if item.state == State.WAITING_VISION:
                return
            if item.state == State.FAILED:
                raise RuntimeError("FAILED item requires deterministic recovery before pipeline.run")
            if item.state == State.VISION:
                self.log.info("[PIPELINE][ITEM %s] VISION iniciando", item.content_id)
                try:
                    v = self.vision.identify(item)
                except VisionUnresolvedError as exc:
                    self.db.mark_vision_waiting(item_id, str(exc))
                    return
                self.db.set_vision(
                    item_id,
                    v.affiliate_name,
                    v.affiliate_url,
                    affiliate_urls=getattr(v, "affiliate_urls", ()),
                    publication_caption=getattr(v, "publication_caption", None),
                )
                self.log.info("[PIPELINE][ITEM %s] VISION concluída", item_id)
                self.db.transition(item_id, State.STUDIO, "vision-complete")
            item = self.db.get(item_id)
            if item.state == State.STUDIO:
                self.log.info("[PIPELINE][ITEM %s] STUDIO/RVC iniciando", item.content_id)
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
                self.log.info("[PIPELINE][ITEM %s] STUDIO/RVC concluído result=%s", item_id, studio.result_path)
                self.db.transition(item_id, State.PUBLISHING, "studio-complete")
            item = self.db.get(item_id)
            if item.state == State.PUBLISHING:
                self.log.info("[PIPELINE][ITEM %s] HUB/PUBLICAÇÃO iniciando", item.content_id)
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
                self.log.info("[PIPELINE][ITEM %s] PUBLICADO confirmado; cleanup iniciando", item_id)
                self.cleanup(item_id)
                self.log.info("[PIPELINE][ITEM %s] FINALIZADO PUBLISHED+cleanup", item_id)
        except Exception as exc:
            current = self.db.get(item_id)
            if current.state == State.PUBLISHED:
                raise
            if self._shutdown_checker():
                # Ctrl+C may interrupt a child process and surface as a normal
                # RuntimeError. Preserve the durable in-flight state instead of
                # converting an operator interruption into terminal FAILED.
                self.log.warning(
                    "[PIPELINE][ITEM %s] shutdown solicitado; preservando state=%s para recovery: %s",
                    item_id, current.state.value, exc,
                )
                raise KeyboardInterrupt from exc
            self.db.fail(item_id, f"{type(exc).__name__}: {exc}")
            self.log.error("[PIPELINE][ITEM %s] ERRO state=%s: %s", item_id, current.state.value, exc)
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
