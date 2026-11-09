"""Read only weather artifacts from the trusted public producer workflow.

No API request to a private repository. Auth stays on api.github.com; signed
storage redirects are downloaded WITHOUT the GitHub Authorization header.
"""
import hashlib, json, os, re, urllib.error, urllib.parse, urllib.request, zipfile
from pathlib import Path

REPO='avidakovic86/moje-vrijeme-modeli'
REPO_ID=1449193559
WORKFLOW='.github/workflows/models.yml'
NAME=re.compile(r'forecast-v1-([0-9]{10})-([0-9]{10})-([0-9]{10})\Z')
MAX_DOWNLOAD=200_200_000

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None

class GitHub:
    def __init__(self, token=None):self.token=token or os.environ.get('MODELS_READ_TOKEN') or os.environ.get('GH_TOKEN')
    def request(self,path,method='GET'):
        if not path.startswith('/repos/'+REPO+'/') and path!='/repos/'+REPO:
            raise ValueError('Only the public model repository may be accessed')
        headers={'Accept':'application/vnd.github+json','X-GitHub-Api-Version':'2022-11-28','User-Agent':'MojeVrijeme-model-data'}
        if self.token:headers['Authorization']='Bearer '+self.token
        request=urllib.request.Request('https://api.github.com'+path,headers=headers,method=method)
        with urllib.request.urlopen(request,timeout=45) as response:
            raw=response.read(4_000_001)
        if len(raw)>4_000_000:raise ValueError('Oversized API metadata')
        return json.loads(raw) if raw else None
    def repository(self):
        repo=self.request('/repos/'+REPO)
        if repo.get('id')!=REPO_ID or repo.get('private') is not False:raise ValueError('Producer must be the expected PUBLIC repository')
        return repo
    def run(self,run_id):
        if not re.fullmatch('[1-9][0-9]{0,19}',str(run_id)):raise ValueError('A numeric producer run ID is required')
        run=self.request(f'/repos/{REPO}/actions/runs/{run_id}')
        if (run.get('repository',{}).get('id')!=REPO_ID or run.get('head_repository',{}).get('id')!=REPO_ID
            or run.get('head_branch')!='main' or run.get('path','').split('@')[0]!=WORKFLOW
            or run.get('event') not in ('workflow_dispatch','schedule','push') or run.get('status')!='completed'
            or run.get('conclusion')!='success' or not re.fullmatch('[a-f0-9]{40}',run.get('head_sha',''))):
            raise ValueError('Artifact must come from a successful trusted main-branch producer run, never a PR/fork')
        return run
    def artifacts(self,run_id=None):
        base=f'/repos/{REPO}/actions/'+(f'runs/{run_id}/artifacts' if run_id else 'artifacts')
        out=[]
        for page in range(1,11):
            values=self.request(base+f'?per_page=100&page={page}')['artifacts'];out.extend(values)
            if len(values)<100:return out
        raise ValueError('Unexpected artifact count; review retention before proceeding')
    def select(self,run_id):
        self.repository();run=self.run(run_id)
        artifacts=[a for a in self.artifacts(run_id) if NAME.fullmatch(a['name']) and not a['expired']]
        if len(artifacts)!=1:raise ValueError('Expected exactly one unexpired data-only artifact')
        a=artifacts[0]
        if not 0<a['size_in_bytes']<=MAX_DOWNLOAD or not re.fullmatch(r'sha256:[a-f0-9]{64}',a.get('digest','')):
            raise ValueError('Missing artifact digest or artifact exceeds size budget')
        return a,run
    def latest(self):
        self.repository()
        for a in sorted(self.artifacts(),key=lambda x:x['id'],reverse=True):
            if not NAME.fullmatch(a['name']) or a['expired']:continue
            try:
                selected,run=self.select(a['workflow_run']['id'])
                return selected,run
            except ValueError:continue
        return None
    def download(self,artifact,directory):
        directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
        url=f'https://api.github.com/repos/{REPO}/actions/artifacts/{artifact["id"]}/zip'
        if not self.token:raise ValueError('Artifact download needs a GitHub token with public repository Actions read permission')
        request=urllib.request.Request(url,headers={'Authorization':'Bearer '+self.token,'Accept':'application/vnd.github+json'})
        try:
            urllib.request.build_opener(NoRedirect()).open(request,timeout=30)
            raise ValueError('Expected a signed storage redirect')
        except urllib.error.HTTPError as error:
            if error.code!=302:raise RuntimeError('Artifact download authorization failed; no credential printed') from None
            location=error.headers.get('Location','')
        parsed=urllib.parse.urlsplit(location)
        if parsed.scheme!='https' or parsed.username or parsed.password or not (parsed.hostname or '').endswith(('.blob.core.windows.net','.actions.githubusercontent.com','.githubusercontent.com')):
            raise ValueError('Untrusted artifact storage redirect')
        target=directory/'artifact.zip';hasher=hashlib.sha256();count=0
        try:
            # Intentionally no Authorization header on this request.
            with urllib.request.urlopen(location,timeout=120) as source,target.open('wb') as sink:
                while chunk:=source.read(1024*1024):
                    count+=len(chunk)
                    if count>MAX_DOWNLOAD:raise ValueError('Artifact download exceeds limit')
                    hasher.update(chunk);sink.write(chunk)
        except Exception:raise RuntimeError('Artifact transfer failed; signed URL omitted') from None
        if 'sha256:'+hasher.hexdigest()!=artifact.get('digest'):
            raise ValueError('GitHub artifact digest mismatch')
        output=directory/'forecast-bundle.tar.gz'
        with zipfile.ZipFile(target) as z:
            members=z.infolist()
            if len(members)!=1 or members[0].filename!=output.name or members[0].file_size>200_000_000 or members[0].flag_bits&1:
                raise ValueError('Artifact must contain exactly one bounded weather bundle')
            with z.open(members[0]) as source,output.open('wb') as sink:
                while chunk:=source.read(1024*1024):sink.write(chunk)
        return output
