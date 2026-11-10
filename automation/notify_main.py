"""Trigger private main-site deployment after a verified weather bundle was uploaded.

Only a private-repository-scoped, fine-grained GitHub token may be used here.
No model bytes or public-repository credentials are sent to the private site.
"""
import json
import os
import urllib.error
import urllib.request

TARGET = "https://api.github.com/repos/avidakovic86/moje-vrijeme/actions/workflows/auto-main-maps.yml/dispatches"

def dispatch():
    if os.environ.get("GITHUB_REPOSITORY") != "avidakovic86/moje-vrijeme-modeli":
        raise ValueError("Refusing to dispatch from an unexpected repository")
    if os.environ.get("GITHUB_REF") != "refs/heads/main":
        raise ValueError("Only the producer main branch can trigger main-site deployment")
    if os.environ.get("GITHUB_EVENT_NAME") not in ("schedule", "workflow_dispatch", "push"):
        raise ValueError("Untrusted producer event")
    token = os.environ.get("MAIN_WORKFLOW_DISPATCH_TOKEN", "").strip()
    if not token:
        print("::notice::Immediate main-site deployment needs the MAIN_WORKFLOW_DISPATCH_TOKEN repository secret; scheduled checks still work.")
        return False
    data = json.dumps({"ref": "main"}).encode("utf-8")
    request = urllib.request.Request(TARGET, data=data, method="POST", headers={
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "Content-Type": "application/json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "moje-vrijeme-modeli-completion-dispatch",
    })
    try:
        with urllib.request.urlopen(request, timeout=25) as response:
            if response.status != 204:
                raise ValueError("Unexpected GitHub workflow dispatch status")
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"GitHub workflow dispatch rejected with HTTP {error.code}; verify the fine-grained token Actions:write scope and target repository") from None
    print("::notice::Verified forecast uploaded; triggered Moje vrijeme private MAIN deployment.")
    return True

if __name__ == "__main__":
    dispatch()
