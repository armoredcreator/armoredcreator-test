import tempfile, unittest
from pathlib import Path
from armored_core.database import Database
from armored_core.models import State
from armored_core.pipeline import Pipeline
from armored_core.services import PublicationResult, SyncService, VisionResult
from armored_core.storage import Storage

class Vision:
    def identify(self,item): return VisionResult("produto-final","https://example.invalid/a")
class Studio:
    def __init__(self,s): self.s=s
    def process(self,item):
        w=self.s.working(item.item_id); w.write_bytes(item.original_path.read_bytes())
        r=self.s.result(item.item_id,item.affiliate_name); r.write_bytes(w.read_bytes()); return r
class Publisher:
    def __init__(self): self.count=0; self.ids=set()
    def is_published(self,item): return item.item_id in self.ids
    def publish(self,item):
        self.count+=1; self.ids.add(item.item_id); return PublicationResult(True,str(self.count))

class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.td=tempfile.TemporaryDirectory(); root=Path(self.td.name)
        self.s=Storage(root); self.db=Database(self.s.database/"armoredcreator.db")
        src=root/"source.mp4"; src.write_bytes(b"VIDEO")
        self.item=SyncService(self.db,self.s).ingest(src,"msg-1"); self.pub=Publisher()
    def tearDown(self): self.db.close(); self.td.cleanup()
    def test_happy_path_keeps_only_original(self):
        Pipeline(self.db,self.s,Vision(),Studio(self.s),self.pub).run(self.item)
        row=self.db.get(self.item)
        self.assertEqual(row.state,State.PUBLISHED); self.assertEqual(row.original_path.read_bytes(),b"VIDEO")
        self.assertEqual([p.name for p in row.workspace.iterdir()],[row.original_path.name]); self.assertEqual(self.pub.count,1)
    def test_second_run_does_not_republish(self):
        p=Pipeline(self.db,self.s,Vision(),Studio(self.s),self.pub); p.run(self.item); p.run(self.item)
        self.assertEqual(self.pub.count,1)

if __name__=="__main__": unittest.main()
