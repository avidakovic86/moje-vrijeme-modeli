"""Storage bookkeeping tests: no API or real forecast downloads."""
import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'automation'))
from public_run import reserve_storage

class API:
    def __init__(self,foreign=0):
        self.items=[{'id':i,'expired':False,'name':f'forecast-v1-202611060{i}-202611060{i}-202611060{i}','size_in_bytes':100,'workflow_run':{'id':i}} for i in [1,2]]
        if foreign:self.items.append({'id':3,'expired':False,'name':'unrelated-owner-artifact','size_in_bytes':foreign})
        self.deleted=[]
    def artifacts(self):return self.items
    def run(self,id):return {'id':id}
    def request(self,path,method):self.deleted.append(int(path.rsplit('/',1)[1]))

class RetentionTests(unittest.TestCase):
    def test_pruning_keeps_last_valid_and_never_deletes_unrelated(self):
        api=API();result=reserve_storage(api,100,201000)
        self.assertEqual(api.deleted,[1]);self.assertEqual(result['retainedBytes'],100)
    def test_insufficient_storage_blocks_new_upload_preserving_latest(self):
        api=API(foreign=1000000)
        with self.assertRaises(ValueError):reserve_storage(api,100,201000)
        self.assertNotIn(2,api.deleted);self.assertNotIn(3,api.deleted)

if __name__=='__main__':unittest.main()
