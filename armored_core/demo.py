from pathlib import Path
import tempfile
from .database import Database
from .pipeline import Pipeline
from .services import PublicationResult, SyncService, VisionResult
from .storage import Storage

class DemoVision:
    def identify(self, item): return VisionResult("finaldomeulinknovo", "https://example.invalid/affiliate")

class DemoStudio:
    def __init__(self, storage): self.storage=storage
    def process(self, item):
        w=self.storage.working(item.item_id); w.write_bytes(item.original_path.read_bytes())
        r=self.storage.result(item.item_id, item.affiliate_name or "final"); r.write_bytes(w.read_bytes())
        return r

class DemoPublisher:
    def __init__(self): self.published={}
    def is_published(self,item): return item.item_id in self.published
    def publish(self,item):
        mid=f"telegram-{item.item_id}"; self.published[item.item_id]=mid
        return PublicationResult(True,mid)

def main():
    with tempfile.TemporaryDirectory() as td:
        root=Path(td); storage=Storage(root); db=Database(storage.database/"armoredcreator.db")
        source=root/"source.mp4"; source.write_bytes(b"ORIGINAL")
        item=SyncService(db,storage).ingest(source,"telegram-550")
        pub=DemoPublisher(); Pipeline(db,storage,DemoVision(),DemoStudio(storage),pub).run(item)
        row=db.get(item)
        assert row.state.value=="PUBLISHED" and row.original_path.read_bytes()==b"ORIGINAL"
        assert [p.name for p in row.workspace.iterdir()]==[row.original_path.name]
        print(f"PASS item={item} state={row.state.value} workspace={row.workspace}")
        db.close()

if __name__=="__main__": main()
