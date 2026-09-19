import tempfile, unittest
from pathlib import Path
from armored_core.database import Database
from armored_core.models import State
from armored_core.pipeline import Pipeline
from armored_core.recovery import Recovery
from armored_core.services import PublicationResult, SyncService, VisionResult
from armored_core.storage import Storage

class Vision:
    def identify(self,item): return VisionResult("recover-final","https://example.invalid/a")
class Studio:
    def __init__(self,s): self.s=s
    def process(self,item):
        w=self.s.working(item.item_id); w.write_bytes(item.original_path.read_bytes())
        r=self.s.result(item.item_id,item.affiliate_name or "recover-final"); r.write_bytes(w.read_bytes()); return r
class CrashStudio(Studio):
    def process(self,item): raise RuntimeError("simulated studio crash")
class Publisher:
    def __init__(self): self.ids=set(); self.count=0
    def is_published(self,item): return item.item_id in self.ids
    def publish(self,item):
        self.count+=1; self.ids.add(item.item_id); return PublicationResult(True,str(self.count))

class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.td=tempfile.TemporaryDirectory(); root=Path(self.td.name)
        self.s=Storage(root); self.db=Database(self.s.database/"armoredcreator.db")
        src=root/"source.mp4"; src.write_bytes(b"VIDEO")
        self.item=SyncService(self.db,self.s).ingest(src,"msg"); self.pub=Publisher()
    def tearDown(self): self.db.close(); self.td.cleanup()
    def test_rebuilds_after_studio_crash(self):
        with self.assertRaises(RuntimeError):
            Pipeline(self.db,self.s,Vision(),CrashStudio(self.s),self.pub).run(self.item)
        self.assertEqual(self.db.get(self.item).state,State.FAILED)
        Recovery(self.db,self.s,Vision(),Studio(self.s),self.pub).reconcile(self.item)
        row=self.db.get(self.item)
        self.assertEqual(row.state,State.PUBLISHED); self.assertTrue(row.original_path.exists())
        self.assertEqual([p.name for p in row.workspace.iterdir()],[row.original_path.name])
    def test_cleanup_is_idempotent(self):
        Pipeline(self.db,self.s,Vision(),Studio(self.s),self.pub).run(self.item)
        Recovery(self.db,self.s,Vision(),Studio(self.s),self.pub).reconcile(self.item)
        self.assertTrue(self.db.get(self.item).original_path.exists())

if __name__=="__main__": unittest.main()
