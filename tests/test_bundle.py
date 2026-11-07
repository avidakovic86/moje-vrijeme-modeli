"""Small SYNTHETIC fixtures are isolated tests, never shipped forecasts."""
import datetime as dt,hashlib,io,json,os,sys,tarfile,tempfile,unittest,urllib.error,zipfile
from pathlib import Path
from unittest.mock import patch
import numpy as np
import yaml
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'automation'))
from bundle import make_bundle,unpack_bundle,verify_site,expected_steps,metadata,MAX_ARCHIVE
from grids import Builder,atomic
from github_artifacts import GitHub,REPO,REPO_ID,WORKFLOW
from public_quota import check
from sources import steps_for

G=dict(width=9,height=7,west=10,east=12,north=46,south=44.5,dx=.25,dy=.25)
def site_fixture(root,run='2026110700'):
    root=Path(root);catalog={'schema':1,'models':{}}
    for model in ('ecmwf','gefs','icon'):
        b=Builder(root/'maps/data',model,run,expected_steps(model,run),G,{'provider':'TEST ONLY — never weather'},1 if model=='icon' else 50,'test fixture')
        for step in b.steps:b.append(step,np.full((7,9),-5+step/100),np.full((7,9),1015+step/100))
        m=b.finish();atomic(root/f'maps/data/{model}/current.json',json.dumps(m).encode())
        catalog['models'][model]={'runId':run,'run':m['run'],'manifest':f'data/{model}/{run}/manifest.json'}
    atomic(root/'maps/catalog.json',json.dumps(catalog).encode());return root

class BundleTests(unittest.TestCase):
    def setUp(self):self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
    def tearDown(self):self.temp.cleanup()
    def test_roundtrip_exact_field_hashes_and_no_application_files(self):
        source=site_fixture(self.root/'source');(source/'index.html').write_text('PRIVATE APP MUST NEVER ENTER DATA')
        archive=self.root/'data.tar.gz';make_bundle(source,archive);target=self.root/'target';unpack_bundle(archive,target)
        self.assertFalse((target/'index.html').exists())
        for model in ('ecmwf','gefs','icon'):
            a=json.loads((source/f'maps/data/{model}/current.json').read_text());b=json.loads((target/f'maps/data/{model}/current.json').read_text());self.assertEqual(a['frames'],b['frames'])
    def test_hostile_paths_links_and_duplicate_paths_rejected(self):
        for name,kind in [('../index.html','file'),('index.html','file'),('maps/data/ecmwf/current.json','link'),('maps/catalog.json','duplicate')]:
            archive=self.root/'hostile.tar.gz'
            with tarfile.open(archive,'w:gz') as tar:
                info=tarfile.TarInfo(name);data=b'{}';info.size=2
                if kind=='link':info.type=tarfile.SYMTYPE;info.linkname='../../index.html';info.size=0;tar.addfile(info)
                else:
                    tar.addfile(info,io.BytesIO(data))
                    if kind=='duplicate':tar.addfile(info,io.BytesIO(data))
            with self.assertRaises(ValueError):unpack_bundle(archive,self.root/'rejected')
            self.assertFalse((self.root/'rejected').exists())
    def test_wrong_mean_and_missing_native_horizon_rejected(self):
        site=site_fixture(self.root/'site');m=json.loads((site/'maps/data/icon/current.json').read_text());m['members']=40
        with self.assertRaises(ValueError):metadata(m,'icon')
        m=json.loads((site/'maps/data/gefs/current.json').read_text());m['frames'].pop()
        with self.assertRaises(ValueError):metadata(m,'gefs')
    def test_transfer_steps_match_official_source_contract(self):
        for model in ('ecmwf','gefs','icon'):
            for hour in ('00','03','06','12','18','21'):
                run='20261107'+hour;self.assertEqual(expected_steps(model,run),steps_for(model,run))

class ProvenanceTests(unittest.TestCase):
    def run_fixture(self):
        return {'repository':{'id':REPO_ID},'head_repository':{'id':REPO_ID},'head_branch':'main','path':WORKFLOW,'event':'workflow_dispatch','status':'completed','conclusion':'success','head_sha':'a'*40}
    def test_fork_pr_failure_and_wrong_workflow_rejected(self):
        good=self.run_fixture()
        with patch.object(GitHub,'request',return_value=good):self.assertEqual(GitHub().run(123),good)
        for patch_value in [{'head_repository':{'id':1}},{'event':'pull_request_target'},{'head_branch':'attack'},{'path':'.github/workflows/other.yml'},{'conclusion':'failure'}]:
            with patch.object(GitHub,'request',return_value={**good,**patch_value}):
                with self.assertRaises(ValueError):GitHub().run(123)
    def test_private_repository_api_path_is_forbidden(self):
        with self.assertRaises(ValueError):GitHub().request('/repos/example-owner/private-app/contents')
    def test_authorization_never_follows_storage_redirect(self):
        data=io.BytesIO()
        with zipfile.ZipFile(data,'w') as z:z.writestr('forecast-bundle.tar.gz',b'isolated test bundle')
        raw=data.getvalue();artifact={'id':1,'digest':'sha256:'+hashlib.sha256(raw).hexdigest()};location='https://example.blob.core.windows.net/test?signed=TEST'
        redirect=urllib.error.HTTPError('https://api.github.com',302,'Found',{'Location':location},None)
        class Opener:
            def open(self,request,**kw):
                if request.get_header('Authorization')!='Bearer TEST_ONLY_TOKEN':raise AssertionError('Missing API auth')
                raise redirect
        with tempfile.TemporaryDirectory() as directory,patch('github_artifacts.urllib.request.build_opener',return_value=Opener()),patch('github_artifacts.urllib.request.urlopen',return_value=io.BytesIO(raw)) as download:
            result=GitHub('TEST_ONLY_TOKEN').download(artifact,directory)
            self.assertEqual(result.read_bytes(),b'isolated test bundle');self.assertEqual(download.call_args.args,(location,))
    def test_untrusted_redirect_rejected(self):
        for location in ['http://example.blob.core.windows.net/test','https://attacker.example/test']:
            redirect=urllib.error.HTTPError('https://api.github.com',302,'Found',{'Location':location},None)
            with tempfile.TemporaryDirectory() as directory,patch('github_artifacts.urllib.request.OpenerDirector.open',side_effect=redirect):
                with self.assertRaises(ValueError):GitHub('TEST').download({'id':1},directory)
    def test_wrong_digest_and_unexpected_zip_file_are_rejected(self):
        for filename,digest in [('index.html',None),('forecast-bundle.tar.gz','sha256:'+'0'*64)]:
            data=io.BytesIO()
            with zipfile.ZipFile(data,'w') as z:z.writestr(filename,b'isolated test')
            raw=data.getvalue();artifact={'id':1,'digest':digest or 'sha256:'+hashlib.sha256(raw).hexdigest()}
            redirect=urllib.error.HTTPError('https://api.github.com',302,'Found',{'Location':'https://example.blob.core.windows.net/test'},None)
            with tempfile.TemporaryDirectory() as directory,patch('github_artifacts.urllib.request.OpenerDirector.open',side_effect=redirect),patch('github_artifacts.urllib.request.urlopen',return_value=io.BytesIO(raw)):
                with self.assertRaises(ValueError):GitHub('TEST').download(artifact,directory)

class PublicPolicyTests(unittest.TestCase):
    def test_free_public_policy_fails_closed(self):
        config={'reviewedMonth':dt.datetime.now(dt.timezone.utc).strftime('%Y-%m'),'githubSpendingLimitUSD':0,'standardPublicRunnerReviewed':True,'artifactBudgetBytes':450_000_000,'retentionDays':1,'maxRetainedBundles':2}
        repo={'full_name':REPO,'private':False};env={'GITHUB_REPOSITORY':REPO,'GITHUB_REF':'refs/heads/main','GITHUB_EVENT_NAME':'workflow_dispatch'}
        self.assertTrue(check(config,repo,env)['approved'])
        self.assertTrue(check(config,repo,{**env,'GITHUB_EVENT_NAME':'schedule'})['approved'])
        for change in [{'artifactBudgetBytes':1},{'githubSpendingLimitUSD':1},{'retentionDays':30},{'reviewedMonth':'1900-01'}]:
            with self.assertRaises(ValueError):check({**config,**change},repo,env)
        with self.assertRaises(ValueError):check(config,{**repo,'private':True},env)
    def test_public_scheduled_workflow_has_no_private_access_or_paid_services(self):
        root=Path(__file__).resolve().parents[1];text=(root/'.github/workflows/models.yml').read_text();w=yaml.load(text,Loader=yaml.BaseLoader)
        self.assertEqual(set(w['on']),{'workflow_dispatch','schedule'});self.assertEqual(w['jobs']['process']['runs-on'],'ubuntu-latest')
        self.assertEqual(len(w['on']['schedule']),2)
        self.assertNotIn('secrets.',text);self.assertNotIn('CLOUDFLARE',text);self.assertNotIn('pull_request',text)
        for step in w['jobs']['process']['steps']:
            if 'uses' in step:self.assertRegex(step['uses'],r'@[a-f0-9]{40}\Z')
        paths=[p for p in root.rglob('*') if p.is_file() and '.git' not in p.parts and '.build' not in p.parts and '__pycache__' not in p.parts]
        self.assertFalse(any(p.suffix.lower() in ('.html','.css','.js','.png','.webp') for p in paths))
        self.assertEqual(MAX_ARCHIVE,200_000_000)

if __name__=='__main__':unittest.main()
