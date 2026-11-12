"""One pinned, main-only recovery. No build, upload, deletion or generic targets."""
from pathlib import Path
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

from . import actions
from .sources import sha256

SOURCE = '4498c6104b65dfae37be1e5ebe8d78c27100884b'
HEAD = '42f6706d21f7bf306e9d8bcbf7af968242763cdf'
VERSION = 'v2026.03.8'
RUN = 43164071342
ATTEMPT = 1
REPOSITORY_ID = 1449410161
RELEASE = 442546669
ARTIFACT = 13941132568
ARTIFACT_DIGEST = '783c13ef2ef1a80c42ec66c561fe2c59bdc014cec1296031213d53731d34d0e2'
ASSET_BYTES = 19162268023
METADATA = {
    'manifest.json': (739252780, 63191, '326b10ea6b13bcf83d4a9f6845cdc7b8505b6584881dc0d2cb2b60dcdb438745'),
    'SHA256SUMS': (739252785, 20660, '11d0bb47006a6ff6a7f6ffcff49319dd4c89941ece35560765bcf7548d26e874'),
}
WORKFLOW = '.github/workflows/recover-release.yml'
VERIFY_STEP = 'Verify both candidates, sources, checksums and every required gate'
UPLOAD_STEP = 'Run actions/upload-artifact@cf430e030ddbb5b0abf93d22962f4752f3646cd9'
PUBLISH_STEP = 'Publish only after successful verified Actions artifact upload'
PREDECESSORS = {'resolve', 'build / admission', 'stage (amd64)', 'stage (arm64)'} | {
    f'build / {job} ({arch}, {runner})'
    for arch, runner in [('amd64', 'ubuntu-24.04'), ('arm64', 'ubuntu-24.04-arm')]
    for job in ('compiler', 'native', 'vpp', 'kea', 'salt', 'runtime', 'kernel',
                'integration', 'iso', 'clearance', 'guests')
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def api(path):
    return actions.gh('api', f'repos/{actions.REPO}/{path}', json_result=True)


def pages(path):
    return actions.gh('api', '--paginate', '--slurp',
                      f'repos/{actions.REPO}/{path}', json_result=True)


def clean_root(path, commit):
    def git(*args):
        return subprocess.check_output(['git', '-C', str(path), *args], text=True).strip()
    require(path.is_dir() and not path.is_symlink(), 'Missing or symlinked checkout')
    require(git('rev-parse', '--show-toplevel') == str(path), 'Checkout must be its own Git root')
    require(git('rev-parse', 'HEAD') == commit and not git('status', '--porcelain'),
            'Checkout must be clean at its exact source')
    # Also refuse shared worktree administration between source and controller.
    require(Path(git('rev-parse', '--absolute-git-dir')) == path / '.git',
            'Independent Git checkout required')


def controller_context():
    env = os.environ
    commit = env.get('GITHUB_SHA', '')
    require(bool(re.fullmatch('[0-9a-f]{40}', commit)), 'Missing controller SHA')
    expected = {'GITHUB_EVENT_NAME': 'workflow_dispatch', 'GITHUB_REF': 'refs/heads/main',
                'GITHUB_REPOSITORY': actions.REPO, 'GITHUB_REPOSITORY_ID': str(REPOSITORY_ID),
                'GITHUB_WORKFLOW_REF': f'{actions.REPO}/{WORKFLOW}@refs/heads/main',
                'GITHUB_WORKFLOW_SHA': commit}
    require(all(env.get(k) == v for k, v in expected.items()), 'Untrusted main workflow context')
    event = json.loads(Path(env['GITHUB_EVENT_PATH']).read_text())
    require(event.get('ref') == 'refs/heads/main' and not event.get('inputs')
            and event.get('repository', {}).get('id') == REPOSITORY_ID
            and event['repository'].get('full_name') == actions.REPO,
            'Unexpected dispatch payload or inputs')
    controller = Path(__file__).resolve().parents[1]
    require(Path.cwd() == controller, 'Controller cwd must be its checkout root')
    source = controller.parent / 'original-source'
    require(source.resolve() == source and controller.name != source.name,
            'Controller and original source must be separate sibling roots')
    clean_root(controller, commit)
    clean_root(source, SOURCE)
    require(commit != SOURCE, 'Original source cannot be relabeled as controller')
    run_id, attempt = env.get('GITHUB_RUN_ID', ''), env.get('GITHUB_RUN_ATTEMPT', '')
    require(all(re.fullmatch(r'[1-9][0-9]*', x) for x in (run_id, attempt)), 'Missing controller run')
    run = api(f'actions/runs/{run_id}/attempts/{attempt}')
    require(run.get('id') == int(run_id) and run.get('run_attempt') == int(attempt)
            and run.get('repository', {}).get('id') == REPOSITORY_ID
            and run.get('event') == 'workflow_dispatch' and run.get('head_branch') == 'main'
            and run.get('head_sha') == commit and run.get('path') == WORKFLOW,
            'Controller run is not the trusted main workflow')
    subprocess.run(['git', 'merge-base', '--is-ancestor', SOURCE, commit], check=True)
    main = api('branches/main')['commit']['sha']
    subprocess.run(['git', 'merge-base', '--is-ancestor', commit, main], check=True)
    return controller, source, commit


def evidence():
    run = api(f'actions/runs/{RUN}')
    require(run.get('id') == RUN and run.get('run_attempt') == ATTEMPT
            and run.get('repository', {}).get('id') == REPOSITORY_ID
            and run['repository'].get('full_name') == actions.REPO
            and run.get('path') == '.github/workflows/release.yml'
            and run.get('event') == 'pull_request_target' and run.get('head_sha') == HEAD
            and run.get('status') == 'completed' and run.get('conclusion') == 'failure',
            'Original release run provenance changed')
    pr = api('pulls/21')
    require(pr.get('number') == 21 and pr.get('merged') is True
            and pr.get('merge_commit_sha') == SOURCE and pr.get('head', {}).get('sha') == HEAD
            and pr.get('base', {}).get('ref') == 'main'
            and pr['base'].get('repo', {}).get('id') == REPOSITORY_ID,
            'Merged PR/source relationship changed')
    job_pages = pages(f'actions/runs/{RUN}/attempts/{ATTEMPT}/jobs?per_page=100')
    jobs = [job for page in job_pages for job in page['jobs']]
    require(bool(job_pages) and all(page.get('total_count') == 27 for page in job_pages)
            and len(jobs) == 27 and len({j['id'] for j in jobs}) == 27
            and {j['name'] for j in jobs} == PREDECESSORS | {'publish'},
            'Incomplete, duplicate or unexpected attempt jobs')
    for job in jobs:
        require(type(job.get('id')) is int and job['id'] > 0
                and job.get('run_id') == RUN and type(job.get('run_attempt')) is int
                and job['run_attempt'] == ATTEMPT
                and job.get('status') == 'completed', 'Job belongs to another run/attempt')
        if job['name'] != 'publish':
            require(job.get('conclusion') == 'success', 'Required predecessor did not succeed')
            continue
        require(job['id'] == 131154518909 and job.get('conclusion') == 'failure',
                'Unexpected publish job')
        steps = job['steps']
        for name, outcome in [(VERIFY_STEP, 'success'), (UPLOAD_STEP, 'success'), (PUBLISH_STEP, 'failure')]:
            matches = [s for s in steps if s['name'] == name]
            require(len(matches) == 1 and matches[0].get('status') == 'completed'
                    and matches[0].get('conclusion') == outcome, 'Missing or changed publish/upload evidence')
        require([s['name'] for s in steps if s.get('conclusion') == 'failure'] == [PUBLISH_STEP],
                'Another publish step failed')
        numbers = [next(s['number'] for s in steps if s['name'] == n)
                   for n in (VERIFY_STEP, UPLOAD_STEP, PUBLISH_STEP)]
        require(all(type(n) is int and n > 0 for n in numbers)
                and numbers == sorted(set(numbers)), 'Unexpected publish step ordering')
    artifact = api(f'actions/artifacts/{ARTIFACT}')
    actions.artifact_evidence(artifact, str(ARTIFACT), ARTIFACT_DIGEST, str(RUN),
                              str(ATTEMPT), VERSION, SOURCE)
    require(artifact.get('expires_at', '').startswith('2027-02-10T')
            and artifact.get('size_in_bytes') == 19162306263
            and artifact.get('workflow_run', {}).get('head_sha') == HEAD
            and artifact['workflow_run'].get('repository_id') == REPOSITORY_ID,
            'Pinned aggregate artifact changed')


def candidate():
    # List lookup detects ambiguous tag ownership, then fetch the complete live
    # asset inventory rather than relying on an embedded release asset page.
    selected = actions.get_release(VERSION, include_drafts=True)
    require(selected.get('id') == RELEASE, 'Pinned release ID changed')
    live = api(f'releases/{RELEASE}')
    require(live.get('id') == RELEASE and live.get('tag_name') == VERSION
            and live.get('target_commitish') == SOURCE and live.get('prerelease') is False
            and type(live.get('draft')) is bool
            and live.get('name') == 'Shirube OS '+VERSION
            and f'Source commit: `{SOURCE}`' in (live.get('body') or ''),
            'Release source/body ownership changed')
    live['assets'] = [a for page in pages(f'releases/{RELEASE}/assets?per_page=100') for a in page]
    actions.release_snapshot(live)  # duplicate IDs/names are never acceptable
    require(len(live['assets']) == 182 and sum(a['size'] for a in live['assets']) == ASSET_BYTES,
            'Pinned asset count/bytes changed')
    for asset in live['assets']:
        require(type(asset.get('id')) is int and asset['id'] > 0
                and type(asset.get('size')) is int and asset['size'] > 0
                and asset.get('state') == 'uploaded'
                and re.fullmatch(r'sha256:[0-9a-f]{64}', asset.get('digest') or '')
                and isinstance(asset.get('name'), str)
                and asset['name'] not in {'.', '..'} and '/' not in asset['name']
                and '\\' not in asset['name'], 'Malformed asset inventory')
        if asset['name'] in METADATA:
            aid, size, digest = METADATA[asset['name']]
            require((asset['id'], asset['size'], asset['digest']) == (aid, size, 'sha256:'+digest),
                    'Pinned metadata changed')
    require(set(METADATA) <= {a['name'] for a in live['assets']}, 'Missing pinned metadata')
    return live


def unchanged(snapshot):
    live = candidate()
    require(actions.release_snapshot(live) == snapshot, 'Release ownership/inventory changed')
    return live


def validate_original(source, root):
    # Run the untouched original module in its own cwd/import context. Its
    # module-relative source-lock and source_fixtures are retained. No GH token,
    # PYTHONPATH or controller imports are passed to this read-only validator.
    script = '''import pathlib, sys
sys.path.insert(0, sys.argv[1])
from producer.actions import identity
from producer.release import validate
import producer.release
assert pathlib.Path(producer.release.__file__).resolve() == pathlib.Path(sys.argv[1]) / 'producer/release.py'
identity(sys.argv[3])
validate(pathlib.Path(sys.argv[2]), sys.argv[3], sys.argv[4])
identity(sys.argv[3])
'''
    subprocess.run([sys.executable, '-I', '-B', '-c', script, str(source), str(root), SOURCE, VERSION],
                   cwd=source, env={'PATH': os.defpath, 'LANG': 'C.UTF-8'}, check=True)


def check_bytes(root, assets):
    require({p.name for p in root.iterdir()} == {a['name'] for a in assets}, 'Downloaded inventory mismatch')
    for asset in assets:
        path = root / asset['name']
        require(path.is_file() and not path.is_symlink() and path.stat().st_size == asset['size']
                and 'sha256:'+sha256(path) == asset['digest'], 'Downloaded asset bytes changed')
    for name, (_, _, digest) in METADATA.items():
        require(sha256(root / name) == digest, 'Pinned metadata bytes changed')


def published_tag(*, allow_absent=False):
    # Matching refs returns [] for absence without treating API errors as 404s.
    # Other versions sharing this prefix do not own the exact release tag.
    refs = [ref for page in pages(f'git/matching-refs/tags/{VERSION}') for ref in page]
    exact = [ref for ref in refs if ref['ref'] == f'refs/tags/{VERSION}']
    require(len(exact) <= 1, 'Ambiguous exact tag')
    if not exact:
        require(allow_absent, 'Missing published tag')
        return
    tag = exact[0]['object']
    seen = set()
    while True:
        require(isinstance(tag, dict) and bool(re.fullmatch('[0-9a-f]{40}', tag.get('sha', ''))),
                'Malformed tag object')
        if tag.get('type') != 'tag':
            break
        require(tag['sha'] not in seen and len(seen) < 8, 'Ambiguous tag chain')
        seen.add(tag['sha'])
        tag = api('git/tags/'+tag['sha'])['object']
    require(tag['type'] == 'commit' and tag['sha'] == SOURCE, 'Published tag source mismatch')


def recover():
    controller, source, commit = controller_context()
    live = candidate()
    snapshot = actions.release_snapshot(live)
    published_tag(allow_absent=live['draft'])
    if live['draft']:
        evidence()
        actions.require_not_superseded(VERSION)
    with tempfile.TemporaryDirectory(prefix='shirube-recovery-') as temporary:
        # Bounded 1 GiB headroom: no second ZIP, extraction, copy or upload tree.
        required = ASSET_BYTES + 1024**3
        free = shutil.disk_usage(temporary).free
        print(f'Recovery capacity: required={required} free={free} bytes', flush=True)
        require(free >= required, 'Insufficient free space before draft download')
        root = Path(temporary) / 'assets'
        actions.download(VERSION, root)
        unchanged(snapshot)
        check_bytes(root, live['assets'])
        validate_original(source, root)  # full original validator exactly once
        clean_root(source, SOURCE)
        clean_root(controller, commit)
        if not live['draft']:
            unchanged(snapshot)
            published_tag()
            return 'NO_OP'
        notes = (live['body'] + '\n\nRecovery verified by controller `'+commit+'` from main.\n'
                 f'Original source: `{SOURCE}`; original run: https://github.com/{actions.REPO}/actions/runs/{RUN}/attempts/{ATTEMPT}.\n'
                 f'GitHub verified-upload artifact: {ARTIFACT}; GitHub archive SHA256: `{ARTIFACT_DIGEST}`.\n'
                 'Archive digest is GitHub upload evidence; no aggregate ZIP was downloaded or freshly hashed.\n'
                 'All existing release assets and pinned manifest/checksums were downloaded once and verified with the original source validator.\n')
        notes_file = Path(temporary) / 'notes.md'
        notes_file.write_text(notes)
        evidence()  # expiry and live provenance must still hold at publication
        actions.require_not_superseded(VERSION)
        unchanged(snapshot)  # final read immediately before the sole mutation
        published_tag(allow_absent=True)
        try:
            actions.gh('release', 'edit', VERSION, '--repo', actions.REPO,
                       '--notes-file', str(notes_file), '--draft=false')
        except (subprocess.CalledProcessError, OSError):
            # An uncertain edit is reconciled once, using already verified bytes.
            # Never retry the edit or download/validate the large asset set again.
            current = candidate()
            expected = dict(live, draft=False, body=notes)
            require(actions.release_snapshot(current) == actions.release_snapshot(expected),
                    'Uncertain publication did not produce the exact expected release')
            published_tag()
            return 'RECOVERED'
        current = candidate()
        expected = dict(live, draft=False, body=notes)
        require(actions.release_snapshot(current) == actions.release_snapshot(expected),
                'Publication readback differs from verified release')
        published_tag()
    return 'RECOVERED'


if __name__ == '__main__':
    require(len(sys.argv) == 1, 'Recovery accepts no target arguments')
    print(recover())
