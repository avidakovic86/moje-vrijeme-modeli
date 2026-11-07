"""Synthetic arrays below are test-only fixtures, never application forecasts."""
import sys,tempfile,unittest,json,copy,datetime as dt
from pathlib import Path
from unittest.mock import patch
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'automation'))
from grids import Builder,validate,atomic
from run import promote,execute,load_current
from sources import NotReady,steps_for,Source
G=dict(width=9,height=7,west=10,east=12,north=46,south=44.5,dx=.25,dy=.25)
T=np.linspace(-5,5,63).reshape(7,9);P=np.full((7,9),1015.)
def fixture(root,model,run):
 b=Builder(root,model,run,[0,3],G,{'provider':'TEST FIXTURE — not weather'},1 if model=='icon' else 50,'test-only')
 b.append(0,T,P);b.append(3,T+1,P+1);return b.path,b.finish()
class PipelineTests(unittest.TestCase):
 def setUp(self):self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
 def tearDown(self):self.temp.cleanup()
 def test_corruption_and_missing_pack_rejected(self):
  path,m=fixture(self.root,'gefs','2026010100');self.assertTrue(validate(path,m)['passed']);p=path/'pack-00.bin';raw=p.read_bytes();p.write_bytes(raw[:-1])
  with self.assertRaises(ValueError):validate(path,m)
  p.unlink()
  with self.assertRaises(OSError):validate(path,m)
 def test_missing_frame_and_mixed_times_rejected(self):
  path,m=fixture(self.root,'ecmwf','2026010100');m['frames'].pop()
  with self.assertRaises(ValueError):validate(path,m)
  path,m=fixture(self.root/'again','ecmwf','2026010100');m['frames'][0]['validTime']='2026-01-02T00:00:00Z'
  with self.assertRaises(ValueError):validate(path,m)
 def test_atomic_pointer_keeps_previous_and_immutable_old_run(self):
  site=self.root/'site';a,ma=fixture(self.root/'a','icon','2026010100');promote(site,a,ma);before=(site/'maps/data/icon/current.json').read_bytes()
  b,mb=fixture(self.root/'b','icon','2026010106');(b/'pack-00.bin').write_bytes(b'broken')
  with self.assertRaises(ValueError):promote(site,b,mb)
  self.assertEqual(before,(site/'maps/data/icon/current.json').read_bytes())
  c,mc=fixture(self.root/'c','icon','2026010106');promote(site,c,mc);self.assertEqual(mc['previousRunId'],'2026010100');self.assertTrue(validate(site/'maps/data/icon/2026010100')['passed'])
 def test_no_model_deterministic_substitution(self):
  site=self.root/'site';path,m=fixture(self.root/'temp','icon','2026010100');m['product']='ensemble-mean';parent=site/'maps/data/icon';parent.mkdir(parents=True);atomic(parent/'current.json',json.dumps(m).encode());self.assertIsNone(load_current(site,'icon'))
 def test_incomplete_upstream_keeps_each_current_run(self):
  site=self.root/'site'
  for model in ['ecmwf','gefs','icon']:
   p,m=fixture(self.root/model,model,'2026010100');promote(site,p,m)
  before={m:(site/'maps/data'/m/'current.json').read_bytes() for m in ['ecmwf','gefs','icon']}
  with patch.object(Source,'preflight',side_effect=NotReady('provider still publishing')):
   r=execute(site,self.root/'work',['ecmwf','gefs','icon'],now=dt.datetime(2026,1,1,9,tzinfo=dt.timezone.utc))
  self.assertFalse(r['changed']);self.assertTrue(r['complete'])
  for model,data in before.items():self.assertEqual(data,(site/'maps/data'/model/'current.json').read_bytes())
 def test_field_decode_failure_cannot_promote_partial_run(self):
  site=self.root/'site';p,m=fixture(self.root/'old','gefs','2026010100');promote(site,p,m);before=(site/'maps/data/gefs/current.json').read_bytes()
  plan=dict(steps=[0,3],estimatedFieldBytes=100)
  def values(run,step):
   if step==3:raise ValueError('wrong GRIB run/units')
   return T,P,G,30
  with patch.object(Source,'preflight',return_value=plan),patch.object(Source,'fields',side_effect=values):execute(site,self.root/'work',['gefs'],forced={'gefs':'2026010106'},workers=1)
  self.assertEqual(before,(site/'maps/data/gefs/current.json').read_bytes())
 def test_failed_remote_restore_cannot_overwrite_valid_bundle(self):
  import io,urllib.error
  import restore
  site=self.root/'site';site.mkdir();p,m=fixture(self.root/'old','gefs','2026010100');promote(site,p,m);before=(site/'maps/data/gefs/current.json').read_bytes()
  _,new=fixture(self.root/'remote','gefs','2026010106')
  def response(url,**kw):
   if '/gefs/current.json' in url:return io.BytesIO(json.dumps(new).encode())
   if '/gefs/2026010106/pack-' in url:return io.BytesIO(b'truncated test fixture')
   raise urllib.error.URLError('test outage')
  with patch.object(restore.urllib.request,'urlopen',side_effect=response):restore.restore(site)
  self.assertEqual(before,(site/'maps/data/gefs/current.json').read_bytes());self.assertTrue(validate(site/'maps/data/gefs/2026010100')['passed'])
 def test_post_november_dwd_source_never_uses_legacy(self):
  source=Source('icon',None,self.root)
  self.assertIn('/weather/nwp/v1/m/icon-eu/p/T/lvt1/100/lv1/85000/r/2026-12-01T00:00/s/',source.dwd_base('2026120100','T'))
 def test_native_horizons_not_invented(self):
  self.assertEqual(steps_for('icon','2026110703'),list(range(52)));self.assertEqual(steps_for('icon','2026110700')[-1],120);self.assertEqual(steps_for('ecmwf','2026110618')[-1],144);self.assertEqual(steps_for('ecmwf','2026110700')[-1],360)
 def test_native_listing_requires_entire_actual_horizon(self):
  def listing(steps):return b'\n'.join(f'<a href="PT{s:03}H00M.grib2">file</a> date 100'.encode() for s in steps)
  class H:
   def get(self,url):return listing(self.steps),{}
  h=H();h.steps=range(52);source=Source('icon',h,self.root)
  self.assertEqual(source.preflight('2026110703')['steps'],list(range(52)))
  for steps in [range(31),range(51),range(53)]:
   h.steps=steps
   with self.assertRaises(NotReady):source.preflight('2026110703')
if __name__=='__main__':unittest.main()
