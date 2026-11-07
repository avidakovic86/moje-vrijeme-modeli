"""Small API/HEAD probe; no GRIB download or private repository checkout."""
import json,os
from pathlib import Path
from discover import discover
from github_artifacts import GitHub,NAME

def probe(api):
    selected=api.latest()
    catalog=None
    if selected:
        artifact,run=selected
        ids=NAME.fullmatch(artifact['name']).groups()
        catalog={'models':{k:{'runId':v} for k,v in zip(('ecmwf','gefs','icon'),ids)}}
    result=discover(catalog)
    return {'hasCandidate':result['hasCandidate'],'probe':result,'seed':selected[0] if selected else None}

if __name__=='__main__':
    result=probe(GitHub());Path('.build').mkdir(exist_ok=True)
    Path('.build/selection.json').write_text(json.dumps(result))
    print(json.dumps({'hasCandidate':result['hasCandidate'],'probe':result['probe'],'seedArtifactId':result['seed']['id'] if result['seed'] else None}))
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'],'a') as f:f.write('has_candidate='+str(result['hasCandidate']).lower()+'\n')
