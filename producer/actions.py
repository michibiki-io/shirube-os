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

from . import release_plan


def changed_file_paths(pages, expected):
    """Require the complete API inventory; include both sides of every rename."""
    if type(expected) is not int or expected <= 0:
        raise ValueError('A positive changed-file count is required')
    if not isinstance(pages, list) or not pages or any(not isinstance(page, list) for page in pages):
        raise ValueError('Malformed changed-file pages')
    changed = [item for page in pages for item in page]
    if len(changed) != expected:
        raise ValueError('Incomplete changed-file inventory; refusing to classify trigger')

    def path(value):
        if (not isinstance(value, str) or not value
                or any(c in value for c in ('\\', '\x00', '\n', '\r'))
                or any(part in {'', '.', '..'} for part in value.split('/'))):
            raise ValueError('Malformed changed-file path')
        return value

    paths, seen = [], set()
    for item in changed:
        if (not isinstance(item, dict) or not isinstance(item.get('status'), str)
                or item['status'] not in {
                'added', 'removed', 'modified', 'renamed', 'copied', 'changed', 'unchanged'}):
            raise ValueError('Malformed changed-file record')
        name = path(item.get('filename'))
        if name in seen:
            raise ValueError('Duplicate changed-file record')
        seen.add(name)
        paths.append(name)
        if item['status'] == 'renamed' or 'previous_filename' in item:
            paths.append(path(item.get('previous_filename')))
    return paths


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
    if type(number) is not int or number <= 0:
        print(json.dumps({'release_impact': 'skip', 'reason': 'missing-pr-number'}, sort_keys=True))
        emit({'eligible':'false'}); return
    pages = gh('api', '--paginate', '--slurp', f'repos/{REPO}/pulls/{number}/files', json_result=True)
    paths = changed_file_paths(pages, pr.get('changed_files'))
    if not eligible(os.environ['GITHUB_EVENT_NAME'], event, ['event-admission']):
        emit({'eligible': 'false', 'publication': 'none', 'validation': 'none'}); return
    commit = pr['merge_commit_sha']
    identity(commit)
    decision = release_plan.plan(paths, commit,
        consumers=release_plan.declared_consumers(Path('.')),
        run=os.environ.get('GITHUB_RUN_ID', ''), attempt=os.environ.get('GITHUB_RUN_ATTEMPT', ''),
        inputs=dependency_evidence(paths, commit))
    decision['gates']['focused'] = 'passed'
    save_plan(decision)
    stream_version=json.loads(Path('source-lock.json').read_text())['stream']['version']
    if decision['publication'] != 'publish':
        complete_resolution(decision, 'v'+stream_version+'.0')
        return
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
            decision['gates'].update(candidate='skipped-not-required', aggregate='skipped-not-required', publication='passed')
            decision['published_evidence'] = 'verified-own-source-version-only'
            complete_resolution(decision, version, noop=True)
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
        cumulative, commits = cumulative_history(revision)
        accumulated = release_plan.classify(cumulative, release_plan.declared_consumers(Path('.')))
        decision['cumulative'] = {'baseline': previous_commit if previous else None, **accumulated}
        if accumulated['publication'] == 'hold':
            decision.update(publication='hold', reason='cumulative-unknown-role', candidate_only=True, deferred=True)
            decision['gates']['publication'] = 'skipped-not-required'
            complete_resolution(decision, 'v'+stream_version+'.0')
            return
        original = individual_commits(pr)
        present = {c['sha'] for c in commits}
        commits.extend(dict(c, message=c['message']+'\n\nCurrent PR original message')
                       for c in original if c['sha'] not in present)
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
    complete_resolution(decision, version)



def dependency_evidence(paths, commit):
    """Immutable Git object identities for both sides, not claims of binary equality."""
    parent = git('rev-parse', commit+'^1')
    def tree(ref):
        raw = subprocess.check_output(['git', 'ls-tree', '-rz', ref])
        return {row.split(b'\t', 1)[1].decode(): row.split(b'\t', 1)[0].decode()
                for row in raw.split(b'\0') if row}
    before, after = tree(parent), tree(commit)
    return {p: {'before': before.get(p, 'absent'), 'after': after.get(p, 'absent')}
            for p in sorted(set(paths))}


def cumulative_history(revision):
    """All intervening commits, including non-published merges; no title inference."""
    commits, paths = [], set()
    for sha in git('rev-list', '--reverse', '--topo-order', revision).splitlines():
        parents = git('rev-list', '--parents', '-n', '1', sha).split()[1:]
        comparison = (['-c', sha] if len(parents) > 1 else
                      [parents[0], sha] if parents else ['--root', sha])
        # Non-merge commits are already included by rev-list. Combined merge
        # diffs retain merge-only resolutions without reimporting either side's
        # already-published history (including main merges into a feature).
        raw = subprocess.check_output(['git', 'diff-tree', '--no-commit-id',
                                       '--name-only', '--no-renames', '-r', '-z', *comparison])
        changed = sorted({p.decode() for p in raw.split(b'\0') if p})
        if changed:
            changed_file_paths([[{'filename': p, 'status': 'changed'} for p in changed]], len(changed))
        paths.update(changed)
        message = git('show', '-s', '--format=%B', sha)
        impact = release_plan.classify(changed, release_plan.declared_consumers(Path('.')))
        commits.append({'sha': sha, 'subject': message.splitlines()[0],
                        'message': message+'\n\nChange roles: '+','.join(sorted(impact['roles']))+
                        '; publication='+impact['publication']+'; validation='+impact['validation']})
    if not commits:
        raise ValueError('Empty cumulative release history')
    return sorted(paths), commits


def complete_resolution(decision, version, noop=False):
    decision.update(version=version, noop=noop)
    save_plan(decision)
    print(json.dumps({k: decision[k] for k in ('publication', 'validation', 'reason', 'source_commit')}, sort_keys=True))
    emit({'eligible': str(decision['publication'] == 'publish').lower(), 'noop': str(noop).lower(),
          'publication': decision['publication'], 'validation': decision['validation'],
          'commit': decision['source_commit'], 'version': version})


def save_plan(decision):
    directory = os.environ.get('SHIRUBE_PLAN_DIRECTORY')
    if directory:
        root = Path(directory); root.mkdir(parents=True, exist_ok=True)
        (root/'plan.json').write_text(json.dumps(decision, sort_keys=True, indent=2)+'\n')


def require_product_event():
    """A candidate version string or rerun never authorizes release mutations."""
    if os.environ.get('SHIRUBE_PUBLICATION_DECISION') != 'publish':
        raise ValueError('A resolved product publication decision is required')
    event = json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text())
    pr = event.get('pull_request', {})
    if not eligible(os.environ['GITHUB_EVENT_NAME'], event, ['event-admission']):
        raise ValueError('Product publication event required')
    paths = changed_file_paths(gh('api', '--paginate', '--slurp',
        f"repos/{REPO}/pulls/{pr.get('number') or event.get('number')}/files", json_result=True), pr.get('changed_files'))
    if release_plan.classify(paths, release_plan.declared_consumers(Path('.')))['publication'] != 'publish':
        raise ValueError('Validation-only or held candidates cannot enter release mutation paths')
    if pr['merge_commit_sha'] != os.environ.get('SHIRUBE_SOURCE_COMMIT'):
        raise ValueError('Product source differs from event')


def aggregate(root, commit, version):
    """Identical shared provenance/checksum/both-architecture contract for all candidates."""
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


def validate_candidates():
    commit, version = os.environ['SHIRUBE_SOURCE_COMMIT'], os.environ['SHIRUBE_VERSION']
    identity(commit)
    source = Path(os.environ['SHIRUBE_CANDIDATES_DIRECTORY'])
    with tempfile.TemporaryDirectory(dir=source.parent) as temporary:
        root = Path(temporary)
        for arch in ('amd64', 'arm64'):
            candidate = source/('shirube-candidate-'+arch)
            record = validate(candidate, commit, version, architectures={arch})
            (root/('candidate-'+arch+'.json')).write_text(json.dumps(record))
            for item in record['assets']:
                destination = root/item['name']
                if destination.exists(): raise ValueError('Candidate asset names overlap')
                original = candidate/item['name']
                if original.is_symlink() or not original.is_file():
                    raise ValueError('Regular same-run candidate assets required')
                # Same filesystem links avoid a second multi-GB asset set.
                os.link(original, destination)
        aggregate(root, commit, version)


def evidence():
    root = Path(os.environ['SHIRUBE_PLAN_DIRECTORY'])
    decision = json.loads((root/'plan.json').read_text())
    results = {k: v['result'] if isinstance(v, dict) else v
               for k, v in json.loads(os.environ['SHIRUBE_JOB_RESULTS']).items()}
    # resolve already passed its focused checks; required outputs never infer PASS.
    gates = {'focused': results.get('resolve'),
             'candidate': results.get('build') if decision['publication'] == 'publish' else results.get('candidate-validation'),
             'aggregate': results.get('candidate-check'),
             'publication': results.get('resolve') if decision.get('noop') else results.get('publish')}
    final, passed = release_plan.outcomes(decision, gates)
    final['plan_run_attempt'] = decision['run_attempt']
    final['evidence_run_attempt'] = os.environ['GITHUB_RUN_ATTEMPT']
    (root/'result.json').write_text(json.dumps(final, sort_keys=True, indent=2)+'\n')
    with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as output:
        output.write('```json\n'+json.dumps(final, sort_keys=True, indent=2)+'\n```\n')
    if not passed: raise ValueError('Required validation/publication failed or was not run')


def stage():
    arch,version,commit = (os.environ[x] for x in ['SHIRUBE_ARCHITECTURE','SHIRUBE_VERSION','SHIRUBE_SOURCE_COMMIT'])
    identity(commit)
    require_product_event()
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
    require_product_event()
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
        aggregate(root, commit, version)
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
    require_product_event()
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
    parser=argparse.ArgumentParser(); parser.add_argument('command',choices=['resolve','stage','publish','promote','validate_candidates','evidence'])
    args=parser.parse_args(); globals()[args.command]()


if __name__=='__main__':main()
