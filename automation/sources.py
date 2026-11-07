"""Bounded official open-data clients. No point API, no deterministic ENS fallback."""
import concurrent.futures as cf, datetime as dt, hashlib, json, re, threading, time, urllib.request, urllib.error
from pathlib import Path
import numpy as np
import eccodes as ec

class NotReady(Exception):pass
class HTTP:
 def __init__(self,budget=5_000_000_000):self.budget=budget;self.bytes=0;self.requests=0;self.lock=threading.Lock()
 def get(self,url,span=None,limit=8_000_000):
  headers={'User-Agent':'MojeVrijeme TEST ingestion/1.0 (bounded, at most 3 field connections)'}
  if span:headers['Range']='bytes='+span
  for retry in range(3):
   try:
    with urllib.request.urlopen(urllib.request.Request(url,headers=headers),timeout=100) as r:
     if span and r.status!=206:raise ValueError('Server ignored Range; refusing full GRIB')
     b=r.read(limit+1);h=dict(r.headers)
    if len(b)>limit:raise ValueError('HTTP response exceeds bounded size')
    with self.lock:
     self.bytes+=len(b);self.requests+=1
     if self.bytes>self.budget:raise ValueError('Network budget exceeded')
    return b,h
   except urllib.error.HTTPError as e:
    if e.code in (404,403):raise NotReady(f'{e.code}: {url}') from e
    if retry==2:raise
    time.sleep(min(15,int(e.headers.get('Retry-After',2*(retry+1)))))
   except (TimeoutError,urllib.error.URLError):
    if retry==2:raise
    time.sleep(2*(retry+1))
 def head(self,url):
  try:
   with urllib.request.urlopen(urllib.request.Request(url,method='HEAD'),timeout=30) as r:return int(r.headers.get('Content-Length',0))
  except urllib.error.HTTPError as e:raise NotReady(str(e)) from e

def candidates(now,interval,days=2):
 n=now.replace(minute=0,second=0,microsecond=0);n-=dt.timedelta(hours=n.hour%interval)
 return [(n-dt.timedelta(hours=i*interval)).strftime('%Y%m%d%H') for i in range(days*24//interval)]
def steps_for(model,run):
 hour=int(run[-2:])
 if model=='ecmwf':return list(range(0,145,3))+(list(range(150,361,6)) if hour%12==0 else [])
 if model=='gefs':return list(range(0,241,3))+list(range(246,361,6))
 # Native v1 intermediate cycles extend to 51h (the old 30h assumption
 # would incorrectly publish only a subset as a complete model run).
 return list(range(0,79))+list(range(81,121,3)) if hour%6==0 else list(range(52))
def split_gribs(parts):
 for part in parts:
  pos=0
  while pos<len(part):
   if part[pos:pos+4]!=b'GRIB':raise ValueError('Invalid GRIB framing')
   size=int.from_bytes(part[pos+8:pos+16],'big')
   if size<20 or pos+size>len(part):raise ValueError('Truncated GRIB')
   yield part[pos:pos+size];pos+=size

def unpack_ranges(raw,headers):
 ct=next((v for k,v in headers.items() if k.lower()=='content-type'),'')
 if 'multipart/byteranges' not in ct:return [raw]
 boundary=re.search(r'boundary="?([^";\s]+)',ct)[1].encode();parts=[]
 for part in raw.split(b'--'+boundary)[1:]:
  if part.startswith(b'--'):break
  head,body=part.lstrip(b'\r\n').split(b'\r\n\r\n',1);m=re.search(rb'Content-Range: bytes (\d+)-(\d+)/',head,re.I)
  if not m:raise ValueError('Missing multipart byte range')
  size=int(m[2])-int(m[1])+1
  if len(body)<size:raise ValueError('Truncated multipart range')
  parts.append(body[:size])
 return parts

def regular(g,bbox):
 if ec.codes_get(g,'gridType')!='regular_ll':raise ValueError('Not a regular source grid')
 w,h=int(ec.codes_get(g,'Ni')),int(ec.codes_get(g,'Nj'));dx=ec.codes_get(g,'iDirectionIncrementInDegrees');dy=ec.codes_get(g,'jDirectionIncrementInDegrees')
 if ec.codes_get(g,'alternativeRowScanning'):raise ValueError('Unsupported source scan')
 xs=(ec.codes_get(g,'longitudeOfFirstGridPointInDegrees')+np.arange(w)*dx*(-1 if ec.codes_get(g,'iScansNegatively') else 1)+180)%360-180
 ys=ec.codes_get(g,'latitudeOfFirstGridPointInDegrees')+np.arange(h)*dy*(1 if ec.codes_get(g,'jScansPositively') else -1)
 ix=np.where((xs>=bbox[0])&(xs<=bbox[2]))[0];iy=np.where((ys>=bbox[1])&(ys<=bbox[3]))[0];ix=ix[np.argsort(xs[ix])];iy=iy[np.argsort(-ys[iy])]
 a=ec.codes_get_values(g).reshape(h,w)[np.ix_(iy,ix)]
 grid=dict(width=len(ix),height=len(iy),west=float(xs[ix[0]]),east=float(xs[ix[-1]]),north=float(ys[iy[0]]),south=float(ys[iy[-1]]),dx=dx,dy=dy,rowOrder='north-to-south')
 return a,grid

def check(g,run,step,kind):
 ec.codes_set(g,'stepUnits',1)
 if str(ec.codes_get(g,'dataDate'))+f'{ec.codes_get(g,"dataTime")//100:02}'!=run or int(ec.codes_get(g,'endStep'))!=step:raise ValueError('GRIB run/valid time mismatch')
 short=ec.codes_get(g,'shortName');lev=ec.codes_get(g,'typeOfLevel');level=ec.codes_get(g,'level');units=ec.codes_get(g,'units')
 if short=='t' and lev=='isobaricInhPa' and level==850 and units=='K':return 't'
 if short in ['msl','prmsl'] and lev=='meanSea' and units=='Pa':return 'p'
 raise ValueError(f'Unexpected variable/level/units {short} {lev} {level} {units}')

class Source:
 def __init__(self,model,http,cache):self.model=model;self.http=http;self.cache=Path(cache);self.index={};self.mapping=None;self.uuid=None;self.grid=None;self.audit=[]
 def ecm_base(self,run,step):return f'https://data.ecmwf.int/forecasts/{run[:8]}/{run[-2:]}z/ifs/0p25/enfo/{run}0000-{step}h-enfo-ef'
 def gefs_base(self,run,step):return f'https://noaa-gefs-pds.s3.amazonaws.com/gefs.{run[:8]}/{run[-2:]}/atmos/pgrb2ap5/geavg.t{run[-2:]}z.pgrb2a.0p50.f{step:03}'
 def dwd_base(self,run,variable):
  stamp=dt.datetime.strptime(run,'%Y%m%d%H').strftime('%Y-%m-%dT%H:00');level='lvt1/100/lv1/85000/' if variable=='T' else ''
  return f'https://opendata.dwd.de/weather/nwp/v1/m/icon-eu/p/{variable}/{level}r/{stamp}/s/'
 def preflight(self,run):
  steps=steps_for(self.model,run);index={};total=0
  if self.model=='icon':
   for variable in ['T','PMSL']:
    url=self.dwd_base(run,variable);b,_=self.http.get(url);files={int(s):int(size) for s,size in re.findall(rb'href="PT(\d+)H00M.grib2"[^\n]*?\s(\d+)\s*$',b,re.M)}
    if any(s not in files for s in steps):raise NotReady('DWD run missing native T850/MSLP steps')
    if set(files)!=set(steps):raise NotReady('DWD native time-step/horizon contract changed; refusing to label a subset complete')
    total+=sum(files[s] for s in steps)
  elif self.model=='ecmwf':
   # Probe the final index first: don't enumerate/download half a run.
   order=[steps[-1]]+steps[:-1]
   for step in order:
    b,_=self.http.get(self.ecm_base(run,step)+'.index',limit=10_000_000);records=[json.loads(l) for l in b.splitlines()]
    r=[v for v in records if v['param']=='msl' or v['param']=='t' and str(v.get('levelist'))=='850'];t=[v for v in r if v['param']=='t'];p=[v for v in r if v['param']=='msl']
    ids=lambda a:{int(v['number']) for v in a}
    if len(t)!=50 or len(p)!=50 or ids(t)!=set(range(1,51)) or ids(p)!=ids(t) or any(v['type']!='pf' for v in r):raise NotReady('Incomplete 50-member IFS ENS fields')
    index[step]=r;total+=sum(v['_length'] for v in r)
  else:
   for step in [steps[-1]]+steps[:-1]:
    b,_=self.http.get(self.gefs_base(run,step)+'.idx');lines=b.decode().splitlines();r=[]
    for i,line in enumerate(lines):
     if ':TMP:850 mb:' not in line and ':PRMSL:mean sea level:' not in line:continue
     if 'ens mean' not in line:raise NotReady('Not a GEFS mean product')
     start=int(line.split(':')[1]);end=int(lines[i+1].split(':')[1])-1 if i+1<len(lines) else self.http.head(self.gefs_base(run,step))-1;
     if end<start:raise NotReady('Invalid GEFS message size')
     r.append((start,end,line))
    if len(r)!=2:raise NotReady('Incomplete GEFS fields')
    index[step]=r;total+=sum(b-a+1 for a,b,l in r)
  if total>4_500_000_000:raise ValueError('Model download exceeds preflight 4.5 GB budget')
  self.index=index;return dict(model=self.model,run=run,steps=steps,estimatedFieldBytes=total)
 def prepare_native(self,run):
  from scipy.spatial import cKDTree,ConvexHull
  coords=[];ids=[]
  for variable in ['CLAT','CLON']:
   b,_=self.http.get(self.dwd_base(run,variable)+'PT000H00M.grib2',limit=15_000_000);g=ec.codes_new_from_message(b)
   try:
    a=ec.codes_get_values(g);units=ec.codes_get(g,'units');a=np.degrees(a) if units=='rad' else a
    if units!='rad' and not units.lower().startswith('degree'):raise ValueError('Unexpected coordinate units')
    coords.append(a);ids.append(ec.codes_get(g,'uuidOfHGrid'))
   finally:ec.codes_release(g)
  if ids[0]!=ids[1]:raise ValueError('Native coordinate grids disagree')
  lat,lon=coords;self.uuid=ids[0];w,h=1377,657;xs=np.linspace(-23.5,62.5,w);ys=np.linspace(70.5,29.5,h);xx,yy=np.meshgrid(xs,ys);q=np.column_stack([xx.ravel(),yy.ravel()]);points=np.column_stack([lon,lat]);hull=ConvexHull(points)
  # The old official regular output extent is strictly INSIDE the native mesh.
  # Refuse any exterior point instead of extrapolating across a changed footprint.
  inside=np.ones(len(q),bool)
  for eq in hull.equations:inside &= q@eq[:2]+eq[2] <= 1e-7
  if not inside.all():raise ValueError('Native footprint no longer covers configured region; keep previous run')
  rad=np.pi/180
  xyz=lambda lo,la:np.column_stack([np.cos(la*rad)*np.cos(lo*rad),np.cos(la*rad)*np.sin(lo*rad),np.sin(la*rad)])
  dist,idx=cKDTree(xyz(lon,lat)).query(xyz(q[:,0],q[:,1]));maxkm=float(dist.max()*6371.229)
  if maxkm>12:raise ValueError('Gap in native model coverage; no filling allowed')
  self.mapping=idx;self.grid=dict(width=w,height=h,west=-23.5,east=62.5,north=70.5,south=29.5,dx=.0625,dy=.0625,rowOrder='north-to-south');self.native=dict(gridUUID=self.uuid,points=len(lat),maxSamplingDistanceKm=maxkm,delivery='nearest actual native cells; 0.0625 degree display grid; no extra model resolution',source='DWD v1 native distribution; independent of retiring legacy feed')
 def fields(self,run,step):
  if self.model=='icon':
   if self.mapping is None:self.prepare_native(run)
   messages=[];urls=[]
   for variable in ['T','PMSL']:
    url=self.dwd_base(run,variable)+f'PT{step:03}H00M.grib2';b,_=self.http.get(url,limit=10_000_000);messages.append(b);urls.append(url)
  elif self.model=='gefs':
   url=self.gefs_base(run,step);messages=[];urls=[url]
   for a,b,line in self.index[step]:
    raw,_=self.http.get(url,f'{a}-{b}',limit=b-a+1);messages.extend(split_gribs([raw]))
  else:
   url=self.ecm_base(run,step)+'.grib2';spans=sorted((r['_offset'],r['_offset']+r['_length']-1) for r in self.index[step]);merged=[]
   for a,b in spans:
    if merged and a==merged[-1][1]+1:merged[-1][1]=b
    else:merged.append([a,b])
   span=','.join(f'{a}-{b}' for a,b in merged);limit=sum(b-a+1 for a,b in merged)+1024*len(merged)
   raw,headers=self.http.get(url,span,limit=limit);messages=list(split_gribs(unpack_ranges(raw,headers)));urls=[url]
  sums={};counts={};ids={'t':set(),'p':set()};grid=None;sourcehash=hashlib.sha256()
  for message in messages:
   sourcehash.update(message);g=ec.codes_new_from_message(message)
   try:
    key=check(g,run,step,self.model)
    if self.model=='icon':
     if ec.codes_get(g,'uuidOfHGrid')!=self.uuid:raise ValueError('DWD grid changed within run')
     a=ec.codes_get_values(g)[self.mapping].reshape(self.grid['height'],self.grid['width']);gg=self.grid;members=1
    else:
     a,gg=regular(g,[-40,25,90,78])
     if self.model=='ecmwf':
      number=int(ec.codes_get(g,'perturbationNumber'))
      if number in ids[key]:raise ValueError('Duplicate ENS member')
      ids[key].add(number);members=50
     else:
      if ec.codes_get(g,'derivedForecast')!=0:raise ValueError('Not ensemble mean')
      members=int(ec.codes_get(g,'numberOfForecastsInEnsemble'))
    if grid and gg!=grid:raise ValueError('Misaligned fields')
    grid=gg
    if not np.isfinite(a).all() or np.any(abs(a)>1e10):raise ValueError('Missing native values')
    sums[key]=sums.get(key,0)+a;counts[key]=counts.get(key,0)+1
   finally:ec.codes_release(g)
  expected=50 if self.model=='ecmwf' else 1
  if counts!={'t':expected,'p':expected}:raise ValueError('Incomplete T850/MSLP ensemble')
  if self.model=='ecmwf' and (ids['t']!=set(range(1,51)) or ids['p']!=ids['t']):raise ValueError('Missing IFS perturbations')
  t=sums['t']/expected-273.15;p=sums['p']/expected/100
  self.audit.append(dict(step=step,sourceUrls=urls,sourceGribSha256=sourcehash.hexdigest(),members=members,temperatureRangeC=[float(t.min()),float(t.max())],mslpRangeHpa=[float(p.min()),float(p.max())]))
  return t,p,grid,members
