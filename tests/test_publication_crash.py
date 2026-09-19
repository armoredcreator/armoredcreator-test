import tempfile, unittest
from pathlib import Path
from armored_core.database import Database
from armored_core.models import State
from armored_core.pipeline import Pipeline
from armored_core.recovery import Recovery
from armored_core.services import PublicationResult, SyncService, VisionResult
from armored_core.storage import Storage

class V:
    def identify(self,item): return VisionResult("final-link","https://example.invalid")
class S:
    def __init__(self,s): self.s=s
    def process(self,item):
        w=self.s.working(item.item_id); w.write_bytes(item.original_path.read_bytes())
        r=self.s.result(item.item_id,item.affiliate_name); r.write_bytes(w.read_bytes()); return r

class PublishThenCrash:
    def __init__(self): self.published=set(); self.calls=0
    def is_published(self,item): return item.item_id in self.published
    def publish(self,item):
        self.calls += 1
        self.published.add(item.item_id)
        raise RuntimeError("crash-after-external-publication")

class SafePublisher(PublishThenCrash):
    def publish(self,item):
        self.calls += 1; self.published.add(item.item_id)
        return PublicationResult(True, f"telegram-{item.item_id}")

class PublicationCrashTests(unittest.TestCase):
    def test_recovery_does_not_duplicate_after_external_publish(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td); st=Storage(root); db=Database(st.database/"db.sqlite")
            src=root/"source.mp4"; src.write_bytes(b"VIDEO")
            item=SyncService(db,st).ingest(src,"telegram-1")
            publisher=PublishThenCrash()
            with self.assertRaises(RuntimeError):
                Pipeline(db,st,V(),S(st),publisher).run(item)
            self.assertEqual(db.get(item).state,State.FAILED)
            Recovery(db,st,V(),S(st),publisher).reconcile(item)
            row=db.get(item)
            self.assertEqual(row.state,State.PUBLISHED)
            self.assertEqual(publisher.calls,1)
            self.assertTrue(row.original_path.exists())
            self.assertEqual([p.name for p in row.workspace.iterdir()],[row.original_path.name])
            db.close()

if __name__=="__main__": unittest.main()
