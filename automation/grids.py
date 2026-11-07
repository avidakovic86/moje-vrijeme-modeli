"""Immutable schema-2 native-spacing map packs; all hashes verified before commit."""
from pathlib import Path
import datetime as dt, hashlib, json, math, os
import numpy as np

def digest(b): return hashlib.sha256(b).hexdigest()
def atomic(path,data):
 path=Path(path);path.parent.mkdir(parents=True,exist_ok=True);tmp=path.with_name(path.name+'.tmp');tmp.write_bytes(data);os.replace(tmp,path)
def utc(run,step=0):return (dt.datetime.strptime(run,'%Y%m%d%H').replace(tzinfo=dt.timezone.utc)+dt.timedelta(hours=step)).isoformat().replace('+00:00','Z')
def encode(t,p):
 if t.shape!=p.shape or not np.isfinite(t).all() or not np.isfinite(p).all():raise ValueError('Missing or mismatched T850/MSLP samples')
 if t.min() < -100 or t.max()>70 or p.min()<800 or p.max()>1100:raise ValueError('Implausible meteorological units/range')
 return np.stack([np.rint(t*100),np.rint(p*10)]).astype('<i2')
def tile_layout(g):
 tiles=[];offset=0;w,h=g['width'],g['height']
 for row in range(2):
  y0=round(row*(h-1)/2);y1=round((row+1)*(h-1)/2)
  for col in range(4):
   x0=round(col*(w-1)/4);x1=round((col+1)*(w-1)/4);tw,th=x1-x0+1,y1-y0+1;size=tw*th*4
   tiles.append(dict(id=len(tiles),x=x0,y=y0,width=tw,height=th,offset=offset,bytes=size));offset+=size
 return tiles,offset
class Builder:
 def __init__(self,directory,model,run,steps,grid,source,members,mean_method):
  self.path=Path(directory)/model/run;self.path.mkdir(parents=True,exist_ok=True);self.grid=grid;self.tiles,self.stride=tile_layout(grid);self.group=max(1,min(4,3_800_000//self.stride));self.frames=[];self.pack=bytearray();self.steps=steps
  self.meta=dict(schema=2,model=model,label={'ecmwf':'ECMWF ENS','gefs':'GFS ENS','icon':'ICON-EU'}[model],run=utc(run),runId=run,grid=grid,members=members,meanMethod=mean_method,fields=['temperature_850hPa','pressure_msl'],encoding='planar int16 LE; T850 C x100; MSLP hPa x10',complete=True,nativeSteps=steps,frames=self.frames,tiles=self.tiles,frameStride=self.stride,packGroupSize=self.group,coverage={k:grid[k] for k in ['west','east','south','north']},source=source,sourcePrecision=dict(temperatureC=.01,mslpHpa=.1),storedTest=False,product='deterministic' if model=='icon' else 'ensemble-mean',staleAfterHours={'ecmwf':18,'gefs':18,'icon':10}[model],publishedAt=None,packHashes={})
 def append(self,step,t,p):
  i=len(self.frames)
  if step!=self.steps[i]:raise ValueError('Unexpected or out-of-order forecast step')
  a=encode(t,p);raw=a.tobytes();hashes=[]
  for tile in self.tiles:
   b=a[:,tile['y']:tile['y']+tile['height'],tile['x']:tile['x']+tile['width']].copy().tobytes();hashes.append(digest(b));self.pack.extend(b)
  self.frames.append(dict(step=step,validTime=utc(self.meta['runId'],step),bytes=len(raw),sha256=digest(raw),tileHashes=hashes))
  if (i+1)%self.group==0 or i==len(self.steps)-1:
   name=f'pack-{i//self.group:02}.bin';b=bytes(self.pack);atomic(self.path/name,b);self.meta['packHashes'][name]=digest(b);self.pack.clear()
 def finish(self):
  if len(self.frames)!=len(self.steps):raise ValueError('Refusing partial model run')
  self.meta['publishedAt']=dt.datetime.now(dt.timezone.utc).isoformat().replace('+00:00','Z');atomic(self.path/'manifest.json',json.dumps(self.meta,separators=(',',':')).encode());validate(self.path,self.meta);return self.meta

def validate(directory,m=None):
 directory=Path(directory);m=m or json.loads((directory/'manifest.json').read_text());g=m['grid'];w,h=g['width'],g['height'];n=w*h
 names=[x.get('name') if isinstance(x,dict) else x for x in m.get('fields',[])]
 if m.get('complete') is not True or names!=['temperature_850hPa','pressure_msl'] or not m.get('frames'):raise ValueError('Incomplete manifest')
 if m.get('product') and m.get('nativeSteps') and [f['step'] for f in m['frames']]!=m['nativeSteps']:raise ValueError('Missing native forecast steps')
 if abs((g['east']-g['west'])/(w-1)-g['dx'])>1e-8 or abs((g['north']-g['south'])/(h-1)-g['dy'])>1e-8:raise ValueError('Grid alignment')
 last=-1;checks=0;packs={}
 for i,f in enumerate(m['frames']):
  if f['step']<=last or f['validTime']!=utc(m['runId'],f['step']):raise ValueError('Run/time mismatch')
  last=f['step'];name=f'pack-{i//m["packGroupSize"]:02}.bin'
  if name not in packs:
   b=(directory/name).read_bytes()
   if len(b)>25*1024*1024 or m.get('packHashes',{}).get(name) not in (None,digest(b)):raise ValueError('Pack hash/size mismatch')
   packs={name:b}
  b=packs[name];a=np.zeros((2,h,w),dtype='<i2');seen=np.zeros((h,w),bool)
  for tile in m['tiles']:
   offset=(i%m['packGroupSize'])*m['frameStride']+tile['offset'];raw=b[offset:offset+tile['bytes']]
   if len(raw)!=tile['bytes'] or digest(raw)!=f['tileHashes'][tile['id']]:raise ValueError('Missing/corrupt tile')
   values=np.frombuffer(raw,dtype='<i2').reshape(2,tile['height'],tile['width']);area=a[:,tile['y']:tile['y']+tile['height'],tile['x']:tile['x']+tile['width']];s=seen[tile['y']:tile['y']+tile['height'],tile['x']:tile['x']+tile['width']]
   if not np.array_equal(area[:,s],values[:,s]):raise ValueError('Tile edge mismatch')
   area[:]=values;s[:]=True;checks+=1
  if not seen.all() or digest(a.tobytes())!=f['sha256']:raise ValueError('Incomplete full field')
  if a[0].min()< -10000 or a[0].max()>7000 or a[1].min()<8000 or a[1].max()>11000:raise ValueError('Meteorological range/units')
 return dict(frames=len(m['frames']),tileHashes=checks,run=m['run'],passed=True)
