"""Trusted merged-PR release orchestration. Never used by untrusted PR CI."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from .architecture import runner
from .policy import eligible, next_version, release_notes
from .release import validate
from .sources import sha256

REPO = 'michibiki-io/shirube-os'


def gh(*args, json_result=False):
    value = subprocess.check_output(['gh', *args], text=True)
    return json.loads(value) if json_result else value


def git(*args):
    return subprocess.check_output(['git', *args], text=True).strip()


def identity(commit):
    if not re.fullmatch('[0-9a-f]{40}', commit) or git('rev-parse', 'HEAD') != commit:
        raise ValueError('Release checkout does not match the exact merged commit')
    if git('status', '--porcelain'):
        raise ValueError('Release source checkout must be clean')


def releases():
    pages = gh('api', '--paginate', '--slurp', f'repos/{REPO}/releases', json_result=True)
    return [item for page in pages for item in page]


def get_release(version):
    return gh('api', f'repos/{REPO}/releases/tags/{version}', json_result=True)


def require_draft(version, commit):
    live = get_release(version)
    if not live['draft'] or live['target_commitish'] != commit:
        raise ValueError('Refusing to mutate a published release or another source candidate')
    return live


def download(version, destination):
    destination.mkdir(parents=True, exist_ok=False)
    gh('release', 'download', version, '--repo', REPO, '--dir', str(destination))


def verify_published(version, commit):
    live = get_release(version)
    if live['draft']:
        raise ValueError('Expected a published release')
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)/'published'
        download(version, root)
        validate(root, commit, version)
        expected = {p.name for p in root.iterdir()}
        if expected != {a['name'] for a in live['assets']}:
            raise ValueError('Published asset inventory changed during verification')
        # Downloaded bytes, metadata and source inventories were all verified;
        # no build/upload/update is permitted on this path.
    return 'NO_OP'


def emit(values):
    with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
        for key,value in values.items():
            encoded = json.dumps(value, separators=(',',':')) if isinstance(value, dict) else str(value)
            if '\n' in encoded or '\r' in encoded:
                raise ValueError('Multiline Actions output is not allowed')
            output.write(f'{key}={encoded}\n')


def resolve():
    event = json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text())
    pr = event.get('pull_request', {})
    # The trigger itself excludes direct pushes. Fetch all changed-file pages.
    number = pr.get('number') or event.get('number')
    if not isinstance(number, int):
        emit({'eligible':'false'}); return
    pages = gh('api', '--paginate', '--slurp', f'repos/{REPO}/pulls/{number}/files', json_result=True)
    changed=[item for page in pages for item in page]
    if isinstance(pr.get('changed_files'),int) and len(changed)!=pr['changed_files']:
        raise ValueError('Incomplete changed-file inventory; refusing to classify trigger')
    paths=[path for item in changed for path in [item['filename'],item.get('previous_filename','')] if path]
    if not eligible(os.environ['GITHUB_EVENT_NAME'], event, paths):
        emit({'eligible':'false'}); return
    commit = pr['merge_commit_sha']
    identity(commit)
    existing = releases()
    matching=[]
    for record in existing:
        if not re.fullmatch(r'v[0-9]+\.[0-9]+\.[0-9]+',record['tag_name']):continue
        target=record['target_commitish'] if record['draft'] else git('rev-parse',record['tag_name']+'^{commit}')
        if target==commit:matching.append(record)
    if len(matching)>1:
        raise ValueError('Ambiguous release versions for the merged commit')
    if matching:
        selected = matching[0]
        version = selected['tag_name']
        if not selected['draft']:
            verify_published(version, commit)
            emit({'eligible':'true','noop':'true','commit':commit,'version':version})
            return
    else:
        published = [r for r in existing if not r['draft'] and not r['prerelease'] and
                     re.fullmatch(r'v[0-9]+\.[0-9]+\.[0-9]+',r['tag_name'])]
        published.sort(key=lambda r:tuple(map(int,r['tag_name'][1:].split('.'))))
        previous = published[-1] if published else None
        if previous:
            previous_commit = git('rev-parse', previous['tag_name']+'^{commit}')
            subprocess.run(['git','merge-base','--is-ancestor',previous_commit,commit],check=True)
            revision = previous_commit+'..'+commit
        else:
            revision = commit
        pages=gh('api','--paginate','--slurp',f'repos/{REPO}/pulls/{number}/commits',json_result=True)
        raw_commits=[item for page in pages for item in page]
        if not raw_commits or (isinstance(pr.get('commits'),int) and len(raw_commits)!=pr['commits']):
            raise ValueError('Incomplete individual PR commit metadata; refusing PR-title classification')
        commits=[{'sha':item['sha'],'subject':item['commit']['message'].splitlines()[0],
                  'message':item['commit']['message']} for item in raw_commits]
        version = next_version(previous['tag_name'] if previous else None,[c['message'] for c in commits])
        tag_exists=subprocess.run(['git','show-ref','--verify','--quiet','refs/tags/'+version]).returncode==0
        if tag_exists and git('rev-parse',version+'^{commit}')!=commit:
            raise ValueError('Computed version already points to a different source commit')
        with tempfile.NamedTemporaryFile(mode='w',suffix='.md') as notes:
            notes.write('Shirube OS '+version+'\n\nSource commit: `'+commit+'`\n\n'+release_notes(commits)); notes.flush()
            # Reserve this version/commit durably before compilation. A rerun
            # finds this draft and cannot allocate a different version.
            gh('release','create',version,'--repo',REPO,'--draft','--target',commit,
               '--title','Shirube OS '+version,'--notes-file',notes.name)
    matrix = {'include':[{'architecture':a,'runner':runner(a,os.getenv('ACTIONS_RUNNER_'+a.upper(),''))}
                         for a in ['amd64','arm64']]}
    emit({'eligible':'true','noop':'false','commit':commit,'version':version,'matrix':matrix})


def stage():
    arch,version,commit = (os.environ[x] for x in ['SHIRUBE_ARCHITECTURE','SHIRUBE_VERSION','SHIRUBE_SOURCE_COMMIT'])
    identity(commit)
    root = Path(os.environ['SHIRUBE_CANDIDATE_DIRECTORY'])
    manifest = validate(root, commit, version, architectures={arch})
    live = require_draft(version, commit)
    prefix = f'shirube-os-{version}-{arch}'
    if any(not item['name'].startswith(prefix) for item in manifest['assets']):
        raise ValueError('Candidate assets must have architecture-specific owned names')
    # Remove only this architecture's previous unpublished candidate. Never
    # reuse part of a signing generation, touch the other architecture or clobber
    # any published material. Workflow-wide concurrency serializes releases.
    for asset in live['assets']:
        if asset['name'].startswith(prefix) or asset['name'] == f'candidate-{arch}.json':
            require_draft(version,commit)
            gh('api','--method','DELETE',f'repos/{REPO}/releases/assets/{asset["id"]}')
    files = [root/item['name'] for item in manifest['assets']]
    candidate = root/f'candidate-{arch}.json'
    candidate.write_text(json.dumps(manifest,indent=2)+'\n')
    require_draft(version,commit)
    gh('release','upload',version,'--repo',REPO,*[str(p) for p in files],str(candidate))


def publish():
    version,commit = (os.environ[x] for x in ['SHIRUBE_VERSION','SHIRUBE_SOURCE_COMMIT'])
    identity(commit)
    live = get_release(version)
    if not live['draft']:
        verify_published(version,commit); return
    require_draft(version,commit)
    with tempfile.TemporaryDirectory() as temporary:
        root=Path(temporary)/'candidates'; download(version,root)
        candidates=[json.loads((root/f'candidate-{a}.json').read_text()) for a in ['amd64','arm64']]
        manifest=dict(candidates[0]); manifest['architectures']={}; manifest['assets']=[]
        for candidate in candidates:
            for key in ['schema_version','manifest_kind','provider','project','version','source_commit','source_clean','upstream']:
                if candidate[key]!=manifest[key]:
                    raise ValueError('Candidates disagree on shared source/version/provenance')
            for arch, record in candidate['architectures'].items():
                if arch in manifest['architectures']:
                    raise ValueError('Duplicate architecture candidate')
                manifest['architectures'][arch]=record
            manifest['assets'].extend(candidate['assets'])
        for arch in ['amd64','arm64']:
            (root/f'candidate-{arch}.json').unlink()
        (root/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
        names=sorted([x['name'] for x in manifest['assets']]+['manifest.json'])
        (root/'SHA256SUMS').write_text(''.join(f'{sha256(root/name)}  {name}\n' for name in names))
        validate(root,commit,version)
        for asset in live['assets']:
            if asset['name'] in {'candidate-amd64.json','candidate-arm64.json','manifest.json','SHA256SUMS'}:
                require_draft(version,commit)
                gh('api','--method','DELETE',f'repos/{REPO}/releases/assets/{asset["id"]}')
        require_draft(version,commit)
        gh('release','upload',version,'--repo',REPO,str(root/'manifest.json'),str(root/'SHA256SUMS'))
        # Verify remote staging bytes, not only the local directory, before the
        # one publication transition. Uncertain outcomes are reconciled once.
        remote=Path(temporary)/'remote'; download(version,remote); validate(remote,commit,version)
        import shutil
        public=Path(os.environ['SHIRUBE_PUBLIC_DIRECTORY'])
        shutil.copytree(remote,public)


def promote():
    version,commit=(os.environ[x] for x in ['SHIRUBE_VERSION','SHIRUBE_SOURCE_COMMIT'])
    identity(commit)
    live=get_release(version)
    if not live['draft']:
        verify_published(version,commit);return
    require_draft(version,commit)
    artifact_id=os.environ.get('SHIRUBE_ACTIONS_ARTIFACT_ID','')
    digest=os.environ.get('SHIRUBE_ACTIONS_ARTIFACT_DIGEST','')
    if not artifact_id.isdigit() or not re.fullmatch('[0-9a-f]{64}',digest):
        raise ValueError('Successful Actions artifact upload evidence is required')
    artifact=gh('api',f'repos/{REPO}/actions/artifacts/{artifact_id}',json_result=True)
    if artifact.get('expired') or artifact.get('workflow_run',{}).get('head_sha')!=commit:
        raise ValueError('Actions artifact is expired or belongs to another source commit')
    if artifact.get('digest')!='sha256:'+digest:
        raise ValueError('Actions artifact digest mismatch')
    with tempfile.TemporaryDirectory() as temporary:
        remote=Path(temporary)/'remote';download(version,remote);validate(remote,commit,version)
        notes=Path(temporary)/'notes.md'
        notes.write_text((live.get('body') or '')+'\n\nVerified Actions artifact: '+
                         f'https://github.com/{REPO}/actions/runs/{artifact["workflow_run"]["id"]}/artifacts/{artifact_id}'+
                         '\nArtifact SHA256: `'+digest+'`\n')
        try:
            gh('release','edit',version,'--repo',REPO,'--notes-file',str(notes),'--draft=false')
        except subprocess.CalledProcessError:
            current=get_release(version)
            if current['draft']:raise
            verify_published(version,commit)


def main():
    parser=argparse.ArgumentParser(); parser.add_argument('command',choices=['resolve','stage','publish','promote'])
    args=parser.parse_args(); globals()[args.command]()


if __name__=='__main__':main()
