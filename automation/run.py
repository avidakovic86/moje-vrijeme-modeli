#!/usr/bin/env python3
"""Discover -> preflight -> ingest -> verify -> atomically promote. Never deploys."""
import concurrent.futures as cf
import argparse, datetime as dt, json, os, shutil, sys, time
from pathlib import Path
from grids import Builder,atomic,validate
from sources import HTTP,Source,NotReady,candidates
ROOT=Path(__file__).resolve().parents[1]

def load_current(site,model):
 p=site/'maps/data'/model/'current.json'
 if not p.exists():return None
 m=json.loads(p.read_text())
 if model=='icon' and (m.get('product')!='deterministic' or m.get('label')!='ICON-EU'):return None
 validate(p.parent/m['runId'],m);return m

def promote(site,directory,m):
 validate(directory,m)
 parent=site/'maps/data'/m['model'];parent.mkdir(parents=True,exist_ok=True);dest=parent/m['runId'];old=load_current(site,m['model'])
 if dest.exists():
  # Run paths are immutable. Never overwrite data which may be in a client's cache.
  existing=json.loads((dest/'manifest.json').read_text());validate(dest,existing)
  if existing['frames']!=m['frames']:raise ValueError('Refusing mutation of an already published run path')
 else:shutil.move(str(directory),str(dest))
 if old and old['runId']!=m['runId']:m['previousRunId']=old['runId']
 atomic(dest/'manifest.json',json.dumps(m,separators=(',',':')).encode());atomic(parent/'current.json',json.dumps(m,separators=(',',':')).encode())
 # Keep current + one previous valid run. Pruning happens only in a local staging tree.
 keep={m['runId'],m.get('previousRunId')}
 for p in parent.iterdir():
  if p.is_dir() and p.name.isdigit() and len(p.name)==10 and p.name not in keep:shutil.rmtree(p)
 return m

def execute(site,work,models,forced=None,now=None,workers=3):
 now=now or dt.datetime.now(dt.timezone.utc);site=Path(site);work=Path(work);work.mkdir(parents=True,exist_ok=True);http=HTTP();report=dict(startedAt=now.isoformat(),models={},changed=False,complete=False)
 for model in models:
  started=time.monotonic();before=http.bytes;source=Source(model,http,work/'geometry');current=load_current(site,model);events=[];updated=False
  trials=[forced[model]] if forced and model in forced else candidates(now,3 if model=='icon' else 6)
  for run in trials:
   if current and run<=current['runId']:break
   try:
    plan=source.preflight(run);print('READY',model,run,'steps',len(plan['steps']),'estimated bytes',plan['estimatedFieldBytes'],flush=True)
   except NotReady as e:events.append(dict(run=run,state='not-ready',reason=str(e)));continue
   candidate=work/'candidate'/model/run
   if candidate.exists():shutil.rmtree(candidate)
   builder=None
   try:
    if model=='icon':source.prepare_native(run)
    def ordered_fields():
     # Bounded window: at most three complete input frames in memory/network.
     with cf.ThreadPoolExecutor(max_workers=workers) as pool:
      pending={i:pool.submit(source.fields,run,step) for i,step in enumerate(plan['steps'][:workers])}
      for i,step in enumerate(plan['steps']):
       value=pending.pop(i).result()
       if i+workers<len(plan['steps']):pending[i+workers]=pool.submit(source.fields,run,plan['steps'][i+workers])
       yield step,value
    for step,(t,p,grid,members) in ordered_fields():
     if builder is None:
      kind='DWD ICON-EU deterministic native v1' if model=='icon' else 'NOAA published GEFS ensemble mean' if model=='gefs' else 'ECMWF IFS mean of all 50 publicly distributed perturbations (control not distributed in ef)'
      metadata=dict(provider={'icon':'DWD','ecmwf':'ECMWF','gefs':'NOAA'}[model],licence='CC BY 4.0' if model!='gefs' else 'US government open data',kind=kind,nativeGrid=getattr(source,'native',None))
      builder=Builder(work/'candidate',model,run,plan['steps'],grid,metadata,members,kind)
     if grid!=builder.grid:raise ValueError('Native grid changes within model run')
     builder.append(step,t,p);print('FRAME',model,run,step,'bytes',http.bytes-before,flush=True)
    m=builder.finish();m['validation']=validate(candidate,m);m['source']['forecastGribProofCount']=len(source.audit)
    atomic(candidate/'manifest.json',json.dumps(m,separators=(',',':')).encode());validate(candidate,m)
    current=promote(site,candidate,m);report['changed']=True;updated=True;events.append(dict(run=run,state='promoted',steps=len(plan['steps']),estimatedBytes=plan['estimatedFieldBytes']));break
   except Exception as e:
    events.append(dict(run=run,state='failed-validation',reason=str(e)));print('KEEP PREVIOUS',model,repr(e),flush=True)
    if candidate.exists():shutil.rmtree(candidate)
    break
  report['models'][model]=dict(currentRun=current['runId'] if current else None,updated=updated,elapsedSeconds=round(time.monotonic()-started,3),downloadBytes=http.bytes-before,events=events,sourceAudit=source.audit)
  atomic(work/'ingest-report.json',json.dumps(report,indent=2).encode())
 # The catalog is written only if every requested model has a valid compatible run.
 active={}
 for model in ['ecmwf','gefs','icon']:
  m=load_current(site,model)
  if m:active[model]=dict(runId=m['runId'],run=m['run'],product=m.get('product','ensemble-mean'),manifest=f'data/{model}/{m["runId"]}/manifest.json',previousRunId=m.get('previousRunId'),publishedAt=m.get('publishedAt'),staleAfterHours=m.get('staleAfterHours',18),source=m.get('source'))
 report['complete']=len(active)==3;report['downloadBytes']=http.bytes;report['httpRequests']=http.requests;report['finishedAt']=dt.datetime.now(dt.timezone.utc).isoformat()
 if report['complete']:
  catalog=dict(schema=1,generatedAt=report['finishedAt'],models=active);atomic(site/'maps/catalog.json',json.dumps(catalog,separators=(',',':')).encode())
 atomic(work/'ingest-report.json',json.dumps(report,indent=2).encode());return report

if __name__=='__main__':
 ap=argparse.ArgumentParser();ap.add_argument('--site',type=Path,default=ROOT);ap.add_argument('--work',type=Path,default=ROOT/'.build');ap.add_argument('--models',default='ecmwf,gefs,icon');ap.add_argument('--workers',type=int,choices=[1,2,3],default=3);ap.add_argument('--run',action='append',default=[],help='Optional explicit model=YYYYMMDDHH for reproducible verification; ordinary schedule discovers runs');args=ap.parse_args();forced=dict(x.split('=') for x in args.run)
 result=execute(args.site,args.work,args.models.split(','),forced,workers=args.workers);print(json.dumps({k:v for k,v in result.items() if k!='models'},indent=2));sys.exit(0 if result['complete'] else 2)
