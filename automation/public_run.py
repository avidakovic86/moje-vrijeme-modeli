"""Public data producer. Only weather archives can leave this runner."""
import json,os
from pathlib import Path
from bundle import make_bundle,unpack_bundle,verify_site
from github_artifacts import GitHub,NAME
from public_quota import check
from restore import restore
from run import execute

def reserve_storage(api,new_bytes,budget):
    artifacts=[a for a in api.artifacts() if not a['expired']]
    owned=[]
    for a in artifacts:
        if NAME.fullmatch(a['name']):
            try:api.run(a['workflow_run']['id']);owned.append(a)
            except ValueError:pass
    keep=max((a['id'] for a in owned),default=None)
    removed=set()
    # Candidate is already completely verified before pruning. Keep the last
    # successful bundle during upload, so a failed upload cannot erase it.
    for a in owned:
        if a['id']!=keep:
            api.request(f'/repos/avidakovic86/moje-vrijeme-modeli/actions/artifacts/{a["id"]}',method='DELETE');removed.add(a['id'])
    retained=sum(a['size_in_bytes'] for a in artifacts if a['id'] not in removed)
    if retained+new_bytes+200_000>budget:
        raise ValueError('Artifact storage exceeds reviewed free allowance; last valid bundle retained')
    return {'retainedBytes':retained,'nextBundleBytes':new_bytes,'deletedOlderBundleIds':sorted(removed)}

def produce():
    api=GitHub();review=check(json.loads(os.environ['PUBLIC_QUOTA_REVIEW_JSON']),api.repository(),os.environ)
    selection=json.loads(Path('.build/selection.json').read_text());site=Path('.build/site')
    if selection['seed']:
        artifact,_=api.select(selection['seed']['workflow_run']['id'])
        if artifact['id']!=selection['seed']['id']:raise ValueError('Producer seed changed')
        archive=api.download(artifact,'.build/input');unpack_bundle(archive,site)
    else:
        site.mkdir(parents=True,exist_ok=True);restore(site,include_previous=False)
    previous={}
    if (site/'maps/provenance.json').exists():previous=json.loads((site/'maps/provenance.json').read_text()).get('models',{})
    report=execute(site,Path('.build/ingest'),['ecmwf','gefs','icon'])
    if not report['complete']:raise ValueError('No complete compatible three-model set; previous artifact remains valid')
    verify_site(site)
    if not report['changed'] and selection['seed']:
        output(False);return
    for id,m in report['models'].items():
        if not m['updated'] and previous.get(id):m['sourceAudit']=previous[id].get('sourceAudit',[])
    Path('.build/ingest/ingest-report.json').write_text(json.dumps(report,indent=2))
    result=make_bundle(site,'.build/output/forecast-bundle.tar.gz','.build/ingest/ingest-report.json')
    # Independently parse the actual transfer archive, including every field SHA.
    unpack_bundle('.build/output/forecast-bundle.tar.gz','.build/transfer-check')
    storage=reserve_storage(api,result['bytes'],review['artifactBudgetBytes'])
    name='forecast-v1-'+'-'.join(result['runs'][id] for id in ('ecmwf','gefs','icon'))
    print(json.dumps({'verified':True,'bundle':result,'storage':storage,'privateCodePresent':False}))
    output(True,name)

def output(changed,name=''):
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'],'a') as f:f.write('changed='+str(changed).lower()+'\nartifact_name='+name+'\n')

if __name__=='__main__':produce()
