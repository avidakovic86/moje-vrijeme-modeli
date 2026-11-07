"""Fail closed before public ingestion or artifact retention changes."""
import datetime as dt, json, math, os
MAX_ARCHIVE=200_000_000

def check(config, repo, env):
    month=dt.datetime.now(dt.timezone.utc).strftime('%Y-%m')
    if repo.get('private') is not False or repo.get('full_name')!='avidakovic86/moje-vrijeme-modeli':
        raise ValueError('Free public standard-runner policy requires the public model repository')
    if env.get('GITHUB_REPOSITORY')!=repo['full_name'] or env.get('GITHUB_REF')!='refs/heads/main' or env.get('GITHUB_EVENT_NAME') not in ('workflow_dispatch','schedule'):
        raise ValueError('Only approved main-branch schedule or owner-triggered runs are enabled')
    if config.get('reviewedMonth')!=month or config.get('githubSpendingLimitUSD')!=0 or config.get('standardPublicRunnerReviewed') is not True:
        raise ValueError('Current-month free quota review and zero extra spending are required')
    budget=config.get('artifactBudgetBytes')
    if type(budget) is not int or not 2*MAX_ARCHIVE<=budget<=500*1024*1024:
        raise ValueError('Reviewed remaining shared artifact budget must cover two bounded bundles')
    if config.get('retentionDays')!=1 or config.get('maxRetainedBundles')!=2:
        raise ValueError('Retain at most two weather bundles for one day')
    return {'approved':True,'reviewedMonth':month,'compute':'public standard ubuntu-latest only','artifactBudgetBytes':budget}

if __name__=='__main__':
    from github_artifacts import GitHub
    print(json.dumps(check(json.loads(os.environ['PUBLIC_QUOTA_REVIEW_JSON']),GitHub().repository(),os.environ)))
