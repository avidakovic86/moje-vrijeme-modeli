"""Data-only transfer contract. Never imports or executes application code."""
import argparse, copy, datetime as dt, hashlib, io, json, math, re, shutil, tarfile, tempfile
from pathlib import Path
from grids import atomic, tile_layout, utc, validate

MODELS = ('ecmwf', 'gefs', 'icon')
MAX_ARCHIVE = 200_000_000
MAX_UNPACKED = 600_000_000
MAX_FILES = 400
PATH = re.compile(r'maps/(?:catalog\.json|provenance\.json|data/(?:ecmwf|gefs|icon)/(?:current\.json|[0-9]{10}/(?:manifest\.json|pack-[0-9]{2}\.bin)))\Z')

def expected_steps(model, run):
    hour = int(run[-2:])
    if model == 'ecmwf':
        return list(range(0,145,3)) + (list(range(150,361,6)) if hour % 12 == 0 else [])
    if model == 'gefs':
        return list(range(0,241,3)) + list(range(246,361,6))
    return list(range(79)) + list(range(81,121,3)) if hour % 6 == 0 else list(range(52))

def metadata(m, model):
    run = m.get('runId', '')
    if not re.fullmatch('[0-9]{10}', run) or m.get('model') != model or m.get('run') != utc(run):
        raise ValueError('Model/run identity mismatch')
    product = 'deterministic' if model == 'icon' else 'ensemble-mean'
    if m.get('product') != product or m.get('label') != {'ecmwf':'ECMWF ENS','gefs':'GFS ENS','icon':'ICON-EU'}[model]:
        raise ValueError('Wrong product or deterministic/ensemble substitution')
    members = m.get('members')
    if type(members) is not int or (model=='ecmwf' and members!=50) or (model=='icon' and members!=1) or (model=='gefs' and not 2<=members<=100):
        raise ValueError('Wrong ensemble membership')
    if [f['step'] for f in m['frames']] != expected_steps(model, run) or m.get('nativeSteps') != expected_steps(model, run):
        raise ValueError('Incomplete native forecast horizon')
    g = m['grid']
    if any(type(g[k]) is not int or g[k]<2 for k in ('width','height')) or g['width']*g['height']>1_100_000:
        raise ValueError('Grid dimensions exceed renderer contract')
    for k in ('west','east','north','south','dx','dy'):
        if type(g[k]) not in (int,float) or not math.isfinite(g[k]):
            raise ValueError('Invalid grid coordinate')
    if not -180<=g['west']<g['east']<=180 or not -85<g['south']<g['north']<85 or g['dx']<=0 or g['dy']<=0:
        raise ValueError('Invalid grid extent')
    tiles, stride = tile_layout(g)
    if m['tiles'] != tiles or m['frameStride'] != stride or type(m['packGroupSize']) is not int or not 1<=m['packGroupSize']<=4:
        raise ValueError('Unexpected tile layout')
    if m.get('fields') != ['temperature_850hPa','pressure_msl'] or m.get('complete') is not True:
        raise ValueError('Wrong fields/incomplete model')
    return m

def verify_site(site):
    site = Path(site)
    catalog = json.loads((site/'maps/catalog.json').read_text())
    if catalog.get('schema') != 1 or set(catalog.get('models', {})) != set(MODELS):
        raise ValueError('All three independent models are required')
    result = {}
    for model in MODELS:
        m = metadata(json.loads((site/f'maps/data/{model}/current.json').read_text()), model)
        entry = catalog['models'][model]
        if entry['runId'] != m['runId'] or entry['manifest'] != f'data/{model}/{m["runId"]}/manifest.json':
            raise ValueError('Catalog/run path mismatch')
        directory = site/f'maps/data/{model}/{m["runId"]}'
        on_disk = json.loads((directory/'manifest.json').read_text())
        if on_disk != m:
            raise ValueError('Immutable/current manifests differ')
        expected = {'manifest.json'} | {f'pack-{i:02}.bin' for i in range((len(m['frames'])+m['packGroupSize']-1)//m['packGroupSize'])}
        if {p.name for p in directory.iterdir()} != expected:
            raise ValueError('Missing or unexpected model files')
        if any(p.stat().st_size>4_000_000 for p in directory.iterdir()):
            raise ValueError('Asset exceeds existing Worker pack limit')
        result[model] = validate(directory, m)
    return result

def make_bundle(site, output, report=None):
    site, output = Path(site), Path(output)
    verify_site(site)
    catalog = copy.deepcopy(json.loads((site/'maps/catalog.json').read_text()))
    output.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(output, 'w:gz', compresslevel=6) as archive:
        def add(name, data):
            if not PATH.fullmatch(name):
                raise ValueError('Non-data file cannot enter a public bundle')
            info = tarfile.TarInfo(name); info.size = len(data); info.mode = 0o644
            archive.addfile(info, io.BytesIO(data))
        for model in MODELS:
            catalog['models'][model].pop('previousRunId', None)
            m = json.loads((site/f'maps/data/{model}/current.json').read_text())
            m.pop('previousRunId', None)
            raw = json.dumps(m, separators=(',',':')).encode()
            add(f'maps/data/{model}/current.json', raw)
            add(f'maps/data/{model}/{m["runId"]}/manifest.json', raw)
            for file in sorted((site/f'maps/data/{model}/{m["runId"]}').glob('pack-*.bin')):
                add(f'maps/data/{model}/{m["runId"]}/{file.name}', file.read_bytes())
        add('maps/catalog.json', json.dumps(catalog,separators=(',',':')).encode())
        if report:
            add('maps/provenance.json', Path(report).read_bytes())
    if output.stat().st_size>MAX_ARCHIVE:
        output.unlink()
        raise ValueError('Complete data exceeds the reviewed artifact budget; do not degrade data')
    return dict(bytes=output.stat().st_size, sha256=hashlib.sha256(output.read_bytes()).hexdigest(),
                runs={id:catalog['models'][id]['runId'] for id in MODELS})

def unpack_bundle(archive, destination):
    archive, destination = Path(archive), Path(destination)
    if archive.stat().st_size>MAX_ARCHIVE:
        raise ValueError('Oversized input archive')
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = Path(tempfile.mkdtemp(prefix='verified-data-', dir=destination.parent))
    try:
        total=0; seen=set()
        with tarfile.open(archive, 'r|gz') as stream:
            for member in stream:
                if not member.isfile() or not PATH.fullmatch(member.name) or member.name in seen:
                    raise ValueError('Archive contains duplicate, link, executable or non-data path')
                if not 0<=member.size<=4_000_000:
                    raise ValueError('Oversized archive member')
                seen.add(member.name);total+=member.size
                if len(seen)>MAX_FILES or total>MAX_UNPACKED:
                    raise ValueError('Archive extraction budget exceeded')
                data=stream.extractfile(member).read(member.size+1)
                if len(data)!=member.size:
                    raise ValueError('Truncated member')
                atomic(temp/member.name,data)
        result=verify_site(temp)
        expected={'maps/catalog.json','maps/provenance.json'}
        for model in MODELS:
            m=json.loads((temp/f'maps/data/{model}/current.json').read_text())
            expected.add(f'maps/data/{model}/current.json')
            expected|={str(p.relative_to(temp)) for p in (temp/f'maps/data/{model}/{m["runId"]}').iterdir()}
        if seen-expected:
            raise ValueError('Archive contains unused or extra model runs')
        if destination.exists():
            raise ValueError('Refusing to overwrite a verified input directory')
        shutil.move(str(temp),str(destination))
        return result
    finally:
        if temp.exists():shutil.rmtree(temp)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--verify',type=Path,required=True)
    print(json.dumps(verify_site(p.parse_args().verify),indent=2))
