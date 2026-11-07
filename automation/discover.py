"""Cheap stdlib-only probe. Full field/member validation belongs to run.py."""
import datetime as dt,json,urllib.request,urllib.error,os
TEST='https://moje-vrijeme-test.pages.dev'
def ready(url):
 try:
  with urllib.request.urlopen(urllib.request.Request(url,method='HEAD'),timeout=15) as r:return r.status==200
 except (urllib.error.URLError,TimeoutError):return False

def discover(catalog=None):
 if catalog is None:
  try:
   with urllib.request.urlopen(TEST+'/maps/catalog.json',timeout=15) as r:catalog=json.load(r)
  except (urllib.error.URLError,ValueError):return {'hasCandidate':True,'reason':'bootstrap: no verified TEST catalog'}
 now=dt.datetime.now(dt.timezone.utc).replace(minute=0,second=0,microsecond=0);new=[]
 for model in ['ecmwf','gefs','icon']:
  interval=3 if model=='icon' else 6;n=now-dt.timedelta(hours=now.hour%interval);current=catalog.get('models',{}).get(model,{}).get('runId','')
  for i in range(8):
   d=n-dt.timedelta(hours=i*interval);run=d.strftime('%Y%m%d%H')
   if run<=current:break
   if model=='ecmwf':
    last=360 if d.hour%12==0 else 144;urls=[f'https://data.ecmwf.int/forecasts/{run[:8]}/{run[-2:]}z/ifs/0p25/enfo/{run}0000-{last}h-enfo-ef.index']
   elif model=='gefs':urls=[f'https://noaa-gefs-pds.s3.amazonaws.com/gefs.{run[:8]}/{run[-2:]}/atmos/pgrb2ap5/geavg.t{run[-2:]}z.pgrb2a.0p50.f360.idx']
   else:
    stamp=d.strftime('%Y-%m-%dT%H:00');last=120 if d.hour%6==0 else 51;urls=[f'https://opendata.dwd.de/weather/nwp/v1/m/icon-eu/p/{var}/{level}r/{stamp}/s/PT{last:03}H00M.grib2' for var,level in [('T','lvt1/100/lv1/85000/'),('PMSL','')]]
   if all(ready(u) for u in urls):new.append({'model':model,'candidateRun':run});break
 return {'hasCandidate':bool(new),'candidates':new,'note':'HEAD probes only; never a completeness/publication guarantee'}
if __name__=='__main__':
 result=discover();print(json.dumps(result));out=os.environ.get('GITHUB_OUTPUT')
 if out:
  with open(out,'a') as f:f.write('has_candidate='+str(result['hasCandidate']).lower()+'\n')
