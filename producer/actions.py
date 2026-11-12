"""Trusted merged-PR release orchestration. Never used by untrusted PR CI."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from .integration import preflight as integration_preflight
from .policy import eligible, next_version, release_notes, VERSION_PATTERN, version_parts, require_stream_version
from .release import validate, RELEASE_ASSET_LIMIT
from .sources import sha256

REPO = 'michibiki-io/shirube-os'

# Exact publication-only paths, not directory exemptions. New/unknown image
# inputs and both sides of renames still reach policy.eligible before allocation.
# Keep the three retired recovery paths: deleted files remain in the PR inventory,
# so these exact entries keep the deletion PR non-image after merge.
PUBLICATION_PATHS = frozenset({
    'producer/actions.py', 'producer/recover_release.py',
    'tests/test_actions.py', 'tests/test_recover_release.py',
    '.github/workflows/recover-release.yml',
})


def image_paths(paths):
    return [path for path in paths if path not in PUBLICATION_PATHS]


def artifact_evidence(artifact, artifact_id, digest, run_id, attempt, version, commit):
    if (not re.fullmatch(r'[1-9][0-9]*', str(artifact_id))
            or not re.fullmatch(r'[1-9][0-9]*', str(run_id))
            or not re.fullmatch(r'[1-9][0-9]*', str(attempt))
            or not re.fullmatch('[0-9a-f]{64}', digest or '')):
        raise ValueError('Successful Actions artifact upload evidence is required')
    if (artifact.get('id') != int(artifact_id)
            or artifact.get('workflow_run', {}).get('id') != int(run_id)
            or artifact.get('name') != f'shirube-os-{version}-{commit}-{attempt}'
            or artifact.get('expired') is not False
            or artifact.get('digest') != 'sha256:'+digest):
        raise ValueError('Actions artifact identity/run/name/digest/expiry mismatch')
    try:
        expiry = datetime.fromisoformat(artifact['expires_at'].replace('Z', '+00:00'))
        if expiry <= datetime.now(timezone.utc):
            raise ValueError('Actions artifact expired')
    except (KeyError, TypeError, AttributeError) as error:
        raise ValueError('Actions artifact expiry is required') from error
    # workflow_run.head_sha is the PR event head, not the merged build source.
    # Exact checkout identity and full remote validation establish that source.


def release_snapshot(live):
    """Stable ownership and complete asset identity; download counts may change."""
    fields = ('id', 'node_id', 'name', 'label', 'size', 'digest', 'state',
              'content_type', 'url', 'browser_download_url', 'created_at', 'updated_at')
    assets = live['assets']
    if len({a['id'] for a in assets}) != len(assets) or len({a['name'] for a in assets}) != len(assets):
        raise ValueError('Ambiguous release asset inventory')
    return ({k: live.get(k) for k in ('id', 'tag_name', 'target_commitish', 'body',
                                     'name', 'prerelease', 'draft')},
            sorted([tuple(a.get(k) for k in fields) + ((a.get('uploader') or {}).get('id'),)
                    for a in assets], key=lambda a: a[0]))


def unchanged_draft(version, commit, snapshot):
    live = require_draft(version, commit)
    if release_snapshot(live) != snapshot:
        raise ValueError('Draft ownership or asset inventory changed')
    return live


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


def get_release(version, include_drafts=False):
    if include_drafts:
        # GitHub's get-by-tag endpoint returns published releases only. Drafts
        # are visible in the paginated releases list to users with push access.
        matching = [record for record in releases() if record['tag_name'] == version]
        if len(matching) > 1:
            raise ValueError('Ambiguous releases for the candidate version')
        if matching and matching[0]['draft']:
            return matching[0]
    return gh('api', f'repos/{REPO}/releases/tags/{version}', json_result=True)


def require_draft(version, commit):
    live = get_release(version, include_drafts=True)
    if not live['draft'] or live['target_commitish'] != commit:
        raise ValueError('Refusing to mutate a published release or another source candidate')
    return live


def require_not_superseded(version, existing=None):
    if existing is None:
        existing = releases()
    if any(not r['draft'] and not r['prerelease'] and
           re.fullmatch(VERSION_PATTERN,r['tag_name']) and
           version_parts(r['tag_name']) > version_parts(version) for r in existing):
        raise ValueError('Draft release has been superseded by a newer published version')


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


def individual_commits(pr):
    """Collect complete PR messages using the event's immutable comparison."""
    base = (pr.get('base') or {}).get('sha')
    head = (pr.get('head') or {}).get('sha')
    expected = pr.get('commits')
    if any(not isinstance(sha, str) or not re.fullmatch('[0-9a-f]{40}', sha)
           for sha in (base, head)):
        raise ValueError('Individual PR commit metadata requires full base/head SHAs')
    if type(expected) is not int or expected <= 0:
        raise ValueError('Individual PR commit metadata requires a positive commit count')
    # PR commit listing is capped at 250; comparison pagination retains all
    # commits, including original messages after squash or rebase merges.
    pages = gh('api', '--paginate', '--slurp',
               f'repos/{REPO}/compare/{base}...{head}?per_page=100', json_result=True)
    raw_commits = []
    for page in pages:
        if page.get('base_commit', {}).get('sha') != base:
            raise ValueError('PR comparison base does not match the event')
        total = page.get('total_commits')
        if type(total) is not int or total != expected:
            raise ValueError('PR comparison total does not match the event commit count')
        raw_commits.extend(page['commits'])
    shas = [item['sha'] for item in raw_commits]
    if len(shas) != expected or len(set(shas)) != expected or head not in shas:
        raise ValueError('Incomplete or duplicate individual PR commit metadata')
    return [{'sha': item['sha'], 'subject': item['commit']['message'].splitlines()[0],
             'message': item['commit']['message']} for item in raw_commits]


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
    if not eligible(os.environ['GITHUB_EVENT_NAME'], event, image_paths(paths)):
        emit({'eligible':'false'}); return
    commit = pr['merge_commit_sha']
    identity(commit)
    stream_version=json.loads(Path('source-lock.json').read_text())['stream']['version']
    existing = releases()
    matching=[]
    for record in existing:
        if not re.fullmatch(VERSION_PATTERN,record['tag_name']):continue
        target=record['target_commitish'] if record['draft'] else git('rev-parse',record['tag_name']+'^{commit}')
        if target==commit:matching.append(record)
    if len(matching)>1:
        raise ValueError('Ambiguous release versions for the merged commit')
    if matching:
        selected = matching[0]
        version = selected['tag_name']
        require_stream_version(version,stream_version)
        if not selected['draft']:
            verify_published(version, commit)
            emit({'eligible':'true','noop':'true','commit':commit,'version':version})
            return
        require_not_superseded(version, existing)
        integration_preflight(version)
    else:
        published = [r for r in existing if not r['draft'] and not r['prerelease'] and
                     re.fullmatch(VERSION_PATTERN,r['tag_name'])]
        published.sort(key=lambda r:version_parts(r['tag_name']))
        previous = published[-1] if published else None
        if previous:
            previous_commit = git('rev-parse', previous['tag_name']+'^{commit}')
            subprocess.run(['git','merge-base','--is-ancestor',previous_commit,commit],check=True)
            revision = previous_commit+'..'+commit
        else:
            revision = commit
        commits = individual_commits(pr)
        # Failed candidates keep their reserved versions. A later merged commit
        # must advance past those drafts without changing their ownership.
        reserved = [r['tag_name'] for r in existing if r['draft'] and
                    re.fullmatch(VERSION_PATTERN,r['tag_name'])]
        latest = max([r['tag_name'] for r in published]+reserved,key=version_parts,default=None)
        version = next_version(latest,stream_version)
        tag_exists=subprocess.run(['git','show-ref','--verify','--quiet','refs/tags/'+version]).returncode==0
        if tag_exists and git('rev-parse',version+'^{commit}')!=commit:
            raise ValueError('Computed version already points to a different source commit')
        integration_preflight(version)
        with tempfile.NamedTemporaryFile(mode='w',suffix='.md') as notes:
            notes.write('Shirube OS '+version+'\n\nUpstream Stream: `'+stream_version+'`\n\nSource commit: `'+commit+'`\n\n'+release_notes(commits)); notes.flush()
            # Reserve this version/commit durably before compilation. A rerun
            # finds this draft and cannot allocate a different version.
            gh('release','create',version,'--repo',REPO,'--draft','--target',commit,
               '--title','Shirube OS '+version,'--notes-file',notes.name)
    emit({'eligible':'true','noop':'false','commit':commit,'version':version})


def stage():
    arch,version,commit = (os.environ[x] for x in ['SHIRUBE_ARCHITECTURE','SHIRUBE_VERSION','SHIRUBE_SOURCE_COMMIT'])
    identity(commit)
    root = Path(os.environ['SHIRUBE_CANDIDATE_DIRECTORY'])
    manifest = validate(root, commit, version, architectures={arch})
    live = require_draft(version, commit)
    prefix = f'shirube-os-{version}-{arch}'
    if any(not item['name'].startswith(prefix) for item in manifest['assets']):
        raise ValueError('Candidate assets must have architecture-specific owned names')
    retained = sum(not (item['name'].startswith(prefix) or
                        item['name'] == f'candidate-{arch}.json') for item in live['assets'])
    if retained+len(manifest['assets'])+1 > RELEASE_ASSET_LIMIT:
        raise ValueError('Staged candidates exceed the GitHub release asset count limit')
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
    live = get_release(version, include_drafts=True)
    if not live['draft']:
        verify_published(version,commit); return
    require_draft(version,commit)
    require_not_superseded(version)
    public=Path(os.environ['SHIRUBE_PUBLIC_DIRECTORY'])
    if public.exists() or public.is_symlink():
        raise FileExistsError('Fresh public destination required')
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
        import shutil
        # Only this internally generated tree is disposable after upload succeeds.
        shutil.rmtree(root)
        # Verify remote staging bytes, not only the local directory, before the
        # one publication transition. Uncertain outcomes are reconciled once.
        remote=Path(temporary)/'remote'; download(version,remote); validate(remote,commit,version)
        # move() would otherwise nest into an existing caller-owned directory.
        if public.exists() or public.is_symlink():
            raise FileExistsError('Fresh public destination required')
        shutil.move(str(remote), str(public))


def promote():
    version,commit=(os.environ[x] for x in ['SHIRUBE_VERSION','SHIRUBE_SOURCE_COMMIT'])
    identity(commit)
    live=get_release(version, include_drafts=True)
    if not live['draft']:
        verify_published(version,commit);return
    require_draft(version,commit)
    if (type(live.get('id')) is not int or live['id'] <= 0
            or live.get('tag_name') != version
            or f'Source commit: `{commit}`' not in (live.get('body') or '')):
        raise ValueError('Draft release ID/body/source ownership is required')
    snapshot=release_snapshot(live)
    require_not_superseded(version)
    artifact_id=os.environ.get('SHIRUBE_ACTIONS_ARTIFACT_ID','')
    digest=os.environ.get('SHIRUBE_ACTIONS_ARTIFACT_DIGEST','')
    run_id=os.environ.get('GITHUB_RUN_ID', '')
    attempt=os.environ.get('GITHUB_RUN_ATTEMPT', '')
    if not all(re.fullmatch(r'[1-9][0-9]*', v) for v in (artifact_id, run_id, attempt)) or not re.fullmatch('[0-9a-f]{64}', digest):
        raise ValueError('Successful Actions artifact upload evidence is required')
    artifact=gh('api',f'repos/{REPO}/actions/artifacts/{artifact_id}',json_result=True)
    artifact_evidence(artifact, artifact_id, digest, run_id, attempt, version, commit)
    with tempfile.TemporaryDirectory() as temporary:
        remote=Path(temporary)/'remote';download(version,remote);validate(remote,commit,version)
        unchanged_draft(version,commit,snapshot)
        notes=Path(temporary)/'notes.md'
        notes.write_text((live.get('body') or '')+'\n\nVerified Actions artifact: '+
                         f'https://github.com/{REPO}/actions/runs/{artifact["workflow_run"]["id"]}/artifacts/{artifact_id}'+
                         '\nArtifact SHA256: `'+digest+'`\n')
        artifact_evidence(gh('api', f'repos/{REPO}/actions/artifacts/{artifact_id}', json_result=True),
                          artifact_id, digest, run_id, attempt, version, commit)
        require_not_superseded(version)
        unchanged_draft(version,commit,snapshot)
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
