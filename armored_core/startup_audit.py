from __future__ import annotations

import logging

from .models import State


class StartupReconciler:
    """Deterministic startup inventory/reconciliation before Sync."""

    def __init__(self, db, storage, publisher=None):
        self.db = db
        self.storage = storage
        self.publisher = publisher
        self.log = logging.getLogger(__name__)

    def run(self) -> dict:
        rows = self.db.conn.execute(
            "SELECT content_id,state,original_path,working_path,result_path,"
            "cleanup_completed FROM items ORDER BY created_at,content_id"
        ).fetchall()
        db_ids = {str(row["content_id"]) for row in rows}
        summary = {
            "items": len(rows), "published": 0, "pending": 0, "failed": 0,
            "clean": 0, "storage_workspaces": 0, "orphans": [],
            "publication_confirmed": 0, "publication_ambiguous": 0,
        }

        self.log.info("[STARTUP][AUDIT] Iniciando varredura antes do Sync")

        for row in rows:
            item_id = str(row["content_id"])
            state = str(row["state"])
            workspace = self.storage.videos / item_id
            files = sorted(p.name for p in workspace.iterdir() if p.is_file()) if workspace.is_dir() else []

            if workspace.is_dir():
                summary["storage_workspaces"] += 1
            if state == State.PUBLISHED.value and bool(row["cleanup_completed"]):
                summary["published"] += 1
                summary["clean"] += 1
            elif state == State.FAILED.value:
                summary["failed"] += 1
            else:
                summary["pending"] += 1

            publication = self.db.publication(item_id)
            if publication:
                if publication["confirmed"] and publication["published_message_id"]:
                    pub_state = f"CONFIRMED#{publication['published_message_id']}"
                else:
                    self._reconcile_publication(item_id)
                    publication = self.db.publication(item_id)
                    if publication and publication["confirmed"] and publication["published_message_id"]:
                        pub_state = f"CONFIRMED#{publication['published_message_id']}"
                    else:
                        pub_state = "AMBIGUOUS"

                if pub_state.startswith("CONFIRMED#"):
                    summary["publication_confirmed"] += 1
                else:
                    summary["publication_ambiguous"] += 1
            else:
                pub_state = "NO_RECORD"

            self.log.info(
                "[STARTUP][ITEM] id=%s state=%s publication=%s cleanup=%s files=%s",
                item_id, state, pub_state,
                "OK" if row["cleanup_completed"] else "PENDENTE",
                ",".join(files) if files else "-",
            )

        if self.storage.videos.is_dir():
            for workspace in sorted(self.storage.videos.iterdir()):
                if workspace.is_dir() and workspace.name not in db_ids:
                    summary["orphans"].append(workspace.name)
                    self.log.warning(
                        "[STARTUP][ORPHAN] storage/videos/%s sem registro SQLite",
                        workspace.name,
                    )

        self.log.info(
            "[STARTUP][AUDIT] concluída: itens=%s publicados=%s pendentes=%s "
            "falhos=%s limpos=%s workspaces=%s órfãos=%s publicações_confirmadas=%s ambíguas=%s",
            summary["items"], summary["published"], summary["pending"],
            summary["failed"], summary["clean"], summary["storage_workspaces"],
            len(summary["orphans"]), summary["publication_confirmed"],
            summary["publication_ambiguous"],
        )
        return summary

    def _reconcile_publication(self, item_id: str) -> None:
        if self.publisher is None:
            return
        item = self.db.get(item_id)
        try:
            result = self.publisher.check_publication(item)
        except Exception as exc:
            self.log.exception(
                "[STARTUP][PUBLICATION] id=%s erro ao verificar: %s",
                item_id, exc,
            )
            return

        value = getattr(result, "value", str(result))
        if value == "CONFIRMED":
            publication = self.db.publication(item_id)
            message_id = publication["published_message_id"] if publication else None
            if message_id:
                self.db.publication_confirmed(item_id, str(message_id))
                self.log.info(
                    "[STARTUP][PUBLICATION] id=%s CONFIRMED message_id=%s",
                    item_id, message_id,
                )
        elif value == "ABSENT":
            self.log.info("[STARTUP][PUBLICATION] id=%s ABSENT", item_id)
        else:
            self.log.warning("[STARTUP][PUBLICATION] id=%s UNKNOWN", item_id)
    