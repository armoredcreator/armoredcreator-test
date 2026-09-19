from armored_core.database import Database
from armored_core.models import State
from armored_core.pipeline import Pipeline
from armored_core.recovery import Recovery
from armored_core.services import Publisher, StudioService, VisionService
from armored_core.storage import Storage

class Coordinator:
    """Single sequential coordinator. It owns orchestration, not service internals."""
    def __init__(self, db: Database, storage: Storage, vision: VisionService, studio: StudioService, publisher: Publisher):
        self.pipeline=Pipeline(db,storage,vision,studio,publisher)
        self.recovery=Recovery(db,storage,vision,studio,publisher)

    def run(self, item_id: int) -> None:
        self.pipeline.run(item_id)

    def recover(self, item_id: int) -> None:
        self.recovery.reconcile(item_id)
