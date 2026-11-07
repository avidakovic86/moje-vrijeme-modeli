"""Restore immutable last-good data from TEST; commit only a fully verified tree."""
import argparse,json,shutil,tempfile,urllib.request,urllib.error
from pathlib import Path
from grids import atomic,validate
TEST='https://moje-vrijeme-test.pages.dev'
def restore(site,base=TEST,include_previous=True):
 if base.rstrip('/')!=TEST:raise ValueError('Only the existing TEST origin may supply state')
 site=Path(site);result={}
 for model in ['ecmwf','gefs','icon']:
  temp=Path(tempfile.mkdtemp(prefix='restore-',dir=site.parent))
  try:
   with urllib.request.urlopen(base+f'/maps/data/{model}/current.json',timeout=30) as r:m=json.load(r)
   if m.get('model')!=model or not str(m.get('runId','')).isdigit() or len(m['runId'])!=10:raise ValueError('Bad remote manifest')
   if model=='icon' and m.get('product')!='deterministic':result[model]='legacy EPS quarantined, not an ICON-EU fallback';continue
   if not include_previous:m.pop('previousRunId',None)
   for run in filter(None,[m['runId'],m.get('previousRunId')]):
    if not run.isdigit() or len(run)!=10:raise ValueError('Bad run path')
    if run==m['runId']:meta=m
    else:
     with urllib.request.urlopen(base+f'/maps/data/{model}/{run}/manifest.json',timeout=30) as r:meta=json.load(r)
    if meta['model']!=model or meta['runId']!=run:raise ValueError('Run identity mismatch')
    path=temp/run;path.mkdir(parents=True,exist_ok=True);count=(len(meta['frames'])+meta['packGroupSize']-1)//meta['packGroupSize']
    for i in range(count):
     filename=f'pack-{i:02}.bin'
     with urllib.request.urlopen(base+f'/maps/data/{model}/{run}/'+filename,timeout=60) as r:b=r.read(25*1024*1024+1)
     if len(b)>25*1024*1024:raise ValueError('Oversized asset')
     atomic(path/filename,b)
    atomic(path/'manifest.json',json.dumps(meta,separators=(',',':')).encode());validate(path,meta)
   parent=site/'maps/data'/model;parent.mkdir(parents=True,exist_ok=True)
   for p in temp.iterdir():
    dest=parent/p.name
    if dest.exists():
     existing=json.loads((dest/'manifest.json').read_text());validate(dest,existing)
     remote=json.loads((p/'manifest.json').read_text())
     if existing['frames']!=remote['frames']:raise ValueError('Immutable TEST run path changed')
    else:shutil.move(str(p),str(dest))
   atomic(parent/'current.json',json.dumps(m,separators=(',',':')).encode());result[model]='restored verified '+m['runId']
  except (urllib.error.URLError,ValueError,KeyError,OSError) as e:result[model]='remote unavailable; bundled fallback retained: '+str(e)
  finally:shutil.rmtree(temp,ignore_errors=True)
 return result
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--site',type=Path,required=True);a=p.parse_args();print(json.dumps(restore(a.site),indent=2))
