"""Temporary publish-only recovery of the pinned v2026.03.14 draft."""
import argparse
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from http.client import HTTPException, HTTPSConnection
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlencode
import zipfile

from . import actions
from .release import RELEASE_ASSET_LIMIT
from .source_contract import LIMIT, filename

REPO = actions.REPO
REPOSITORY_ID = 1449410161
SOURCE = '0a1eeb67a9e6f56b59c601ca9db06c59f0c80c9b'
EVENT_HEAD = 'c9889e0584d87999151b66bb10c767bfd3e3eab5'
VERSION = 'v2026.03.14'
ORIGINAL_RUN = 44030573245
RELEASE_ID = 448240202
DRAFT_BODY_SHA256 = '37260014d0d8a1b2da8a4662fd875eabd1a0465b6631dad340ba58d3eba2578b'
WORKFLOW = '.github/workflows/recover-release.yml'
RELEASE_PATH = f'releases/{RELEASE_ID}'
UPLOAD_HOST = 'uploads.github.com'
UPLOAD_PATH = f'/repos/{REPO}/{RELEASE_PATH}/assets'
CHUNK = 1024**2
HEADROOM = 1024**3
UPLOAD_IDLE_SECONDS = 300
UPLOAD_TOTAL_SECONDS = 600
APPEND_WINDOW_SECONDS = 120 * 60
VISIBILITY_DELAYS = (0, 5, 15)
UPLOAD_STEP = 'Run actions/upload-artifact@cf430e030ddbb5b0abf93d22962f4752f3646cd9'


@dataclass(frozen=True)
class Pin:
    id: int
    name: str
    size: int
    digest: str
    expiry: str


CANDIDATES = {
    'amd64': Pin(14324155876, 'shirube-candidate-amd64', 9659508072,
                 '7d8845749409acc35ccd4dedc324e62fe424dc68089307c59d8389bbecf162c6',
                 '2026-11-24T11:19:43Z'),
    'arm64': Pin(14325887299, 'shirube-candidate-arm64', 9523254818,
                 '74083fdeea33727633a12c6c61d8287f49c9106dd38f3343781f347e3d6d1822',
                 '2026-11-24T11:52:17Z'),
}
PLAN = Pin(14304918763, 'release-plan-1', 3537,
           '21e0ea060d0441a5535e69a13af82d48beb0b58e9ca2eaed03b3a6558bbb1f41',
           '2026-12-17T06:30:30Z')
EVIDENCE = Pin(14383219260, 'release-evidence-2', 6994,
               '607a3cac8e9bd2a1fb8fe548f58c13018ddc8753e9a717dc30b79256ae2730f2',
               '2026-12-18T00:15:08Z')
BUILD_JOBS = {'resolve', 'build / admission'} | {
    f'build / {job} ({arch}, {runner})'
    for arch, runner in [('amd64', 'ubuntu-24.04'), ('arm64', 'ubuntu-24.04-arm')]
    for job in ('compiler', 'native', 'vpp', 'kea', 'salt', 'runtime', 'kernel',
                'integration', 'iso', 'clearance', 'guests')
}
SKIPPED_JOBS = {'candidate-validation', 'seed-plan', 'kernel-seed', 'stage', 'publish'}
GUEST_JOBS = {'build / guests (amd64, ubuntu-24.04)': 133937040878,
              'build / guests (arm64, ubuntu-24.04-arm)': 133937041054}
METADATA = ('manifest.json', 'SHA256SUMS')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def api(path):
    return actions.gh('api', f'repos/{REPO}/{path}', json_result=True)


def pages(path):
    return actions.gh('api', '--paginate', '--slurp', f'repos/{REPO}/{path}', json_result=True)


def valid_id(value):
    return type(value) is int and value > 0


def utcnow():
    return datetime.now(timezone.utc)


def bound_run(run, run_id, branch, sha):
    require(run.get('id') == run_id and run.get('head_branch') == branch
            and run.get('head_sha') == sha and run.get('repository_id') == REPOSITORY_ID
            and run.get('head_repository_id') == REPOSITORY_ID,
            'Artifact does not belong to the expected repository/run/head')


def original_attempt(record, attempt, conclusion):
    require(record.get('id') == ORIGINAL_RUN and record.get('run_attempt') == attempt
            and record.get('event') == 'pull_request_target'
            and record.get('path') == '.github/workflows/release.yml'
            and record.get('workflow_id') == 414368882
            and record.get('head_sha') == EVENT_HEAD and record.get('head_branch') == 'feature/#44'
            and record.get('repository', {}).get('id') == REPOSITORY_ID
            and record.get('repository', {}).get('full_name') == REPO
            and record.get('head_repository', {}).get('id') == REPOSITORY_ID
            and record.get('status') == 'completed' and record.get('conclusion') == conclusion,
            f'Original Release attempt {attempt} provenance changed')


def attempt_jobs(attempt):
    data = pages(f'actions/runs/{ORIGINAL_RUN}/attempts/{attempt}/jobs?per_page=100')
    jobs = [j for page in data for j in page['jobs']]
    names = BUILD_JOBS | SKIPPED_JOBS | {'candidate-check', 'evidence'}
    require(bool(data) and all(p.get('total_count') == len(names) for p in data)
            and len(jobs) == len(names) and len({j['id'] for j in jobs}) == len(names)
            and {j['name'] for j in jobs} == names, 'Uncertain original job inventory')
    result = {j['name']: j for j in jobs}
    for j in jobs:
        require(valid_id(j['id']) and j.get('run_id') == ORIGINAL_RUN
                and j.get('run_attempt') == attempt and j.get('status') == 'completed',
                'Original job run, attempt or status differs')
        expected = ('success' if j['name'] in BUILD_JOBS else
                    'skipped' if j['name'] in SKIPPED_JOBS else
                    'cancelled' if j['name'] == 'candidate-check' and attempt == 1 else
                    'success' if j['name'] == 'candidate-check' else 'failure')
        require(j.get('conclusion') == expected, f'Original job result changed: {j["name"]}')
    return result


def successful_step(job, name, *, number=None):
    return any(s.get('name') == name and (number is None or s.get('number') == number)
               and s.get('status') == 'completed' and s.get('conclusion') == 'success'
               for s in job['steps'])


def check_pin(pin):
    artifact = api(f'actions/artifacts/{pin.id}')
    require((artifact.get('id'), artifact.get('name'), artifact.get('size_in_bytes'),
             artifact.get('digest'), artifact.get('expires_at'))
            == (pin.id, pin.name, pin.size, 'sha256:'+pin.digest, pin.expiry)
            and artifact.get('expired') is False
            and datetime.fromisoformat(pin.expiry) > utcnow(),
            f'Wrong or expired pinned artifact: {pin.name}')
    bound_run(artifact.get('workflow_run', {}), ORIGINAL_RUN, 'feature/#44', EVENT_HEAD)


def stream(path, target, size, digest, *, binary=False):
    """Bound disk use and verify exactly the bytes streamed by the authenticated GET."""
    command = ['gh', 'api', f'repos/{REPO}/{path}']
    if binary:
        command += ['-H', 'Accept: application/octet-stream']
    count = 0
    checksum = hashlib.sha256()
    with target.open('xb') as output, subprocess.Popen(command, stdout=subprocess.PIPE) as process:
        while chunk := process.stdout.read(CHUNK):
            count += len(chunk)
            if count > size:
                process.kill()
                raise ValueError('Download exceeds the pinned size')
            output.write(chunk)
            checksum.update(chunk)
        require(process.wait() == 0 and count == size and checksum.hexdigest() == digest,
                'Download failed or size/SHA-256 differs')


def original_archive(pin, target):
    check_pin(pin)
    stream(f'actions/artifacts/{pin.id}/zip', target, pin.size, pin.digest)


def small_json_zip(path, names):
    with zipfile.ZipFile(path) as archive:
        entries = archive.infolist()
        require(len(entries) == len(names) and {e.filename for e in entries} == set(names)
                and all(0 < e.file_size <= 128 * 1024 for e in entries),
                'Malformed original plan/evidence archive')
        return {name: json.loads(archive.read(name)) for name in names}


def original_evidence(root):
    original_attempt(api(f'actions/runs/{ORIGINAL_RUN}/attempts/1'), 1, 'cancelled')
    original_attempt(api(f'actions/runs/{ORIGINAL_RUN}/attempts/2'), 2, 'failure')
    original_attempt(api(f'actions/runs/{ORIGINAL_RUN}'), 2, 'failure')
    pr = api('pulls/45')
    require(pr.get('number') == 45 and pr.get('merged') is True and pr.get('state') == 'closed'
            and pr.get('merge_commit_sha') == SOURCE
            and pr.get('head', {}).get('sha') == EVENT_HEAD
            and pr.get('head', {}).get('repo', {}).get('id') == REPOSITORY_ID
            and pr.get('base', {}).get('ref') == 'main'
            and pr.get('base', {}).get('repo', {}).get('id') == REPOSITORY_ID,
            'Source is not the exact merged product PR')
    first, second = attempt_jobs(1), attempt_jobs(2)
    for name, ident in GUEST_JOBS.items():
        require(first[name]['id'] == ident
                and successful_step(first[name], 'Execute guests with verified predecessors', number=6)
                and successful_step(first[name], UPLOAD_STEP, number=7),
                'Missing original candidate production/upload')
    require(second['candidate-check']['id'] == 134460309176
            and successful_step(second['candidate-check'],
                'Validate aggregate from both same-run Actions candidates without Release access', number=4)
            and second['stage']['id'] == 134472813062
            and second['publish']['id'] == 134472814680
            and second['evidence']['id'] == 134472812049
            and successful_step(second['evidence'], UPLOAD_STEP, number=5),
            'Missing exact post-rerun validation or no-publication evidence')
    for pin in CANDIDATES.values():
        check_pin(pin)
    with tempfile.TemporaryDirectory(dir=root) as tmp:
        p = Path(tmp)
        original_archive(PLAN, p/'plan.zip')
        original_archive(EVIDENCE, p/'evidence.zip')
        plan = small_json_zip(p/'plan.zip', ('plan.json',))['plan.json']
        evidence = small_json_zip(p/'evidence.zip', ('plan.json', 'result.json'))
    result = evidence['result.json']
    require(evidence['plan.json'] == plan, 'Original plan changed between attempts')
    identity = {'schema_version': 2, 'source_commit': SOURCE, 'version': VERSION,
                'run_id': str(ORIGINAL_RUN), 'run_attempt': '1', 'publication': 'publish',
                'validation': 'candidate', 'noop': False}
    require(all(plan.get(k) == result.get(k) == value for k, value in identity.items())
            and plan['gates'] == {'focused': 'passed', 'candidate': 'required-but-unrun',
                                  'aggregate': 'required-but-unrun', 'publication': 'required-but-unrun'}
            and result['gates'] == {'focused': 'passed', 'candidate': 'passed', 'aggregate': 'passed',
                                    'publication': 'required-but-unrun'}
            and result.get('status') == 'failed' and result.get('plan_run_attempt') == '1'
            and result.get('evidence_run_attempt') == '2',
            'Original plan/result must prove all required validation passed and only publication unrun')


def clean_checkout(root, commit):
    require(root.is_dir() and root.resolve() == root and (root/'.git').is_dir()
            and not (root/'.git').is_symlink(), 'Separate clean checkout required')
    def git(*args):
        return subprocess.check_output(['git', '-C', str(root), *args], text=True).strip()
    require(git('rev-parse', '--show-toplevel') == str(root)
            and git('rev-parse', '--absolute-git-dir') == str(root/'.git')
            and git('rev-parse', 'HEAD') == commit and not git('status', '--porcelain'),
            'Unexpected exact checkout root, source or cleanliness')


def verify_controller_on_main(controller_sha):
    live_main = api('branches/main')['commit']['sha']
    require(re.fullmatch('[0-9a-f]{40}', live_main), 'Missing live main identity')
    if live_main == controller_sha:
        return
    # The job checkout may predate a later main merge. Compare two immutable
    # SHAs on GitHub instead of assuming that new commit is in the local graph.
    ancestry = api(f'compare/{controller_sha}...{live_main}?per_page=1')
    require(ancestry.get('base_commit', {}).get('sha') == controller_sha
            and ancestry.get('merge_base_commit', {}).get('sha') == controller_sha
            and ancestry.get('status') == 'ahead' and ancestry.get('behind_by') == 0
            and valid_id(ancestry.get('ahead_by')), 'Current main does not contain the pinned controller')


def controller_context():
    env = os.environ
    controller_sha = env.get('GITHUB_SHA', '')
    run_id = env.get('GITHUB_RUN_ID', '')
    require(re.fullmatch('[0-9a-f]{40}', controller_sha) and controller_sha != SOURCE
            and re.fullmatch('[1-9][0-9]*', run_id) and int(run_id) != ORIGINAL_RUN,
            'Missing distinct controller source/run')
    expected = {'GITHUB_EVENT_NAME': 'workflow_dispatch', 'GITHUB_REF': 'refs/heads/main',
                'GITHUB_REPOSITORY': REPO, 'GITHUB_REPOSITORY_ID': str(REPOSITORY_ID),
                'GITHUB_WORKFLOW_REF': f'{REPO}/{WORKFLOW}@refs/heads/main',
                'GITHUB_WORKFLOW_SHA': controller_sha, 'GITHUB_RUN_ATTEMPT': '1'}
    require(all(env.get(k) == v for k, v in expected.items()), 'Untrusted main dispatch/workflow')
    event = json.loads(Path(env['GITHUB_EVENT_PATH']).read_text())
    require(event.get('ref') == 'refs/heads/main' and event.get('inputs') in (None, {})
            and event.get('repository', {}).get('id') == REPOSITORY_ID
            and event.get('repository', {}).get('full_name') == REPO,
            'Unexpected recovery event or inputs')
    controller = Path(env['GITHUB_WORKSPACE'])/'controller'
    source = controller.parent/'original-source'
    require(Path.cwd() == controller == Path(__file__).resolve().parents[1],
            'Controller must execute in its isolated checkout')
    clean_checkout(controller, controller_sha)
    clean_checkout(source, SOURCE)
    run = api(f'actions/runs/{run_id}/attempts/1')
    require(run.get('id') == int(run_id) and run.get('run_attempt') == 1
            and run.get('status') == 'in_progress' and run.get('path') == WORKFLOW
            and run.get('event') == 'workflow_dispatch' and run.get('head_branch') == 'main'
            and run.get('head_sha') == controller_sha
            and run.get('repository', {}).get('id') == REPOSITORY_ID
            and run.get('head_repository', {}).get('id') == REPOSITORY_ID,
            'Current Actions run is not the trusted main recovery')
    subprocess.run(['git', 'merge-base', '--is-ancestor', SOURCE, controller_sha], check=True)
    verify_controller_on_main(controller_sha)
    return source, {'run_id': run_id, 'attempt': '1', 'controller': controller_sha}


def original_validator(source, operation, root, architecture=None):
    # Keep the product lock, source fixture exceptions and imports at the
    # exact old commit, without publication or Actions credentials.
    validator = """
from pathlib import Path
import json, sys
sys.path.insert(0, sys.argv[1])
from producer import actions, release
assert Path(actions.__file__).resolve() == Path(sys.argv[1]) / 'producer/actions.py'
assert Path(release.__file__).resolve() == Path(sys.argv[1]) / 'producer/release.py'
root, commit, version, arch = Path(sys.argv[2]), *sys.argv[3:]
actions.identity(commit)
if arch:
    print(json.dumps(release.validate(root, commit, version, architectures={arch})))
else:
    actions.aggregate(root, commit, version)
actions.identity(commit)
"""
    require(operation == ('candidate' if architecture else 'aggregate'), 'Unknown validation scope')
    result = subprocess.check_output([sys.executable, '-I', '-B', '-c', validator, str(source),
                                      str(root), SOURCE, VERSION, architecture or ''],
                                     cwd=source, env={'PATH': os.defpath, 'LANG': 'C.UTF-8'}, text=True)
    return json.loads(result) if architecture else None


def capacity(root):
    sizes = [p.size for p in CANDIDATES.values()]
    # Prior extracted payload + current archive and extraction can overlap.
    # Later the canonical payload and independent readback coexist. Hardlinks
    # in the canonical tree do not duplicate the extracted data blocks.
    needed = max(sum(sizes)+max(sizes), 2*sum(sizes)) + HEADROOM
    free = shutil.disk_usage(root).free
    print(f'Recovery capacity: required={needed} free={free} bytes', flush=True)
    require(free >= needed, 'Not enough space on the recovery filesystem before staging')


def extract_candidate(archive_file, root, bound):
    """Pinned uploads are compression-level 0; never let expanded bytes exceed archive size."""
    with zipfile.ZipFile(archive_file) as archive:
        entries = archive.infolist()
        names = [filename(i.orig_filename) for i in entries]
        require(len(names) == len(set(names)) <= RELEASE_ASSET_LIMIT
                and set(METADATA) < set(names) and sum(i.file_size for i in entries) <= bound,
                'Malformed or oversized candidate ZIP inventory')
        require(all(not i.is_dir() and stat.S_IFMT(i.external_attr >> 16) in (0, stat.S_IFREG)
                    and not (i.flag_bits & 1) and 0 < i.file_size < LIMIT for i in entries),
                'Only bounded, unencrypted regular candidate files are permitted')
        root.mkdir()
        for member, name in zip(entries, names):
            remaining = member.file_size
            with archive.open(member) as source, (root/name).open('xb') as target:
                while chunk := source.read(CHUNK):
                    require(len(chunk) <= remaining, 'ZIP entry exceeded its verified size')
                    target.write(chunk)
                    remaining -= len(chunk)
            require(not remaining, 'Truncated ZIP entry')


def check_tag(*, optional):
    refs = [ref for p in pages(f'git/matching-refs/tags/{VERSION}') for ref in p]
    matches = [r for r in refs if r.get('ref') == f'refs/tags/{VERSION}']
    require(len(matches) == 1 or (not matches and optional), 'Missing or ambiguous release tag')
    if not matches:
        return
    obj = matches[0]['object']
    visited = set()
    while obj.get('type') == 'tag':
        sha = obj.get('sha', '')
        require(re.fullmatch('[0-9a-f]{40}', sha) and sha not in visited and len(visited) < 8,
                'Uncertain annotated release tag')
        visited.add(sha)
        obj = api(f'git/tags/{sha}')['object']
    require(obj.get('type') == 'commit' and obj.get('sha') == SOURCE,
            'Existing release tag belongs to another source')


def asset_identity(asset):
    name = filename(asset.get('name'))
    require(valid_id(asset.get('id')) and valid_id(asset.get('size')) and asset['size'] < LIMIT
            and asset.get('state') == 'uploaded' and isinstance(asset.get('digest'), str)
            and re.fullmatch('sha256:[0-9a-f]{64}', asset['digest']),
            f'Asset {name}: invalid ID, size, digest or state (including starter); preserve draft')
    return {'id': asset['id'], 'size': asset['size'], 'digest': asset['digest'], 'name': name}


def inventory(assets):
    records = [asset_identity(a) for a in assets]
    result = {a['name']: a for a in records}
    require(len(records) == len(result) == len({a['id'] for a in records}) <= RELEASE_ASSET_LIMIT,
            'Duplicate or oversized release asset inventory')
    return result


def release_identity():
    live = api(RELEASE_PATH)
    require(live.get('id') == RELEASE_ID and live.get('tag_name') == VERSION
            and live.get('target_commitish') == SOURCE and live.get('name') == f'Shirube OS {VERSION}'
            and live.get('prerelease') is False and type(live.get('draft')) is bool
            and live.get('upload_url') == f'https://{UPLOAD_HOST}{UPLOAD_PATH}{{?name,label}}',
            'Pinned release identity changed')
    return live


def require_owned_draft(live):
    require(live['draft'] is True and isinstance(live.get('body'), str)
            and hashlib.sha256(live['body'].encode()).hexdigest() == DRAFT_BODY_SHA256,
            'Pinned release is no longer the original draft; preserve without retry')


def live_release():
    live = release_identity()
    live['assets'] = [a for p in pages(f'{RELEASE_PATH}/assets?per_page=100') for a in p]
    actions.release_snapshot(live)
    inventory(live['assets'])
    return live


def owned_draft():
    all_releases = actions.releases()
    require([r.get('id') for r in all_releases if r.get('tag_name') == VERSION] == [RELEASE_ID],
            'Release tag does not uniquely identify the pinned draft')
    actions.require_not_superseded(VERSION, all_releases)
    live = live_release()
    require_owned_draft(live)
    check_tag(optional=True)
    return live


def draft(expected):
    live = owned_draft()
    require(inventory(live['assets']) == expected, 'Draft contains unexpected assets; do not overwrite or retry')
    return live


class AmbiguousUpload(Exception):
    def __init__(self, classification, *, retryable=False):
        super().__init__(classification)
        self.retryable = retryable


@contextmanager
def upload_deadline():
    """The Linux runner has one synchronous POST; bound total time as well as socket idle time."""
    require(not any(signal.getitimer(signal.ITIMER_REAL)), 'Unexpected existing upload deadline')
    def expired(*_):
        raise TimeoutError('Upload total deadline elapsed')
    previous = signal.signal(signal.SIGALRM, expired)
    try:
        signal.setitimer(signal.ITIMER_REAL, UPLOAD_TOTAL_SECONDS)
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def upload(path, size, digest):
    """One append to the pinned ID, with no redirect, label, overwrite or hidden retry."""
    name = filename(path.name)
    require_owned_draft(release_identity())
    connection = HTTPSConnection(UPLOAD_HOST, timeout=UPLOAD_IDLE_SECONDS, blocksize=CHUNK)
    phase = 'send'
    try:
        with path.open('rb') as source:
            try:
                with upload_deadline():
                    connection.request('POST', UPLOAD_PATH+'?'+urlencode({'name': name}), source, {
                        'Authorization': 'Bearer '+os.environ['GH_TOKEN'],
                        'Accept': 'application/vnd.github+json', 'Content-Type': 'application/octet-stream',
                        'Content-Length': str(size), 'User-Agent': 'shirube-os-recover-release',
                        'X-GitHub-Api-Version': '2022-11-28',
                    })
                    phase = 'response headers'
                    response = connection.getresponse()
                    if response.status in (408, 422, 500, 502, 503, 504):
                        # 422 can be a late commit; 502 can leave a starter.
                        # Neither status alone provides evidence for a repeat.
                        raise AmbiguousUpload(f'{phase}: HTTP {response.status}')
                    require(response.status == 201,
                            f'Asset {name}: definite HTTP {response.status}; preserve draft')
                    phase = 'response body'
                    content = response.read(CHUNK)
            except TimeoutError:
                # Once 201 was known, only readback is allowed, even if its
                # response body timed out.
                raise AmbiguousUpload(f'{phase}: timeout', retryable=phase != 'response body') from None
            except (OSError, HTTPException):
                raise AmbiguousUpload(f'{phase}: transport interrupted') from None
        try:
            payload = json.loads(content)
        except (ValueError, UnicodeError):
            raise AmbiguousUpload('response body: 201 without readable JSON') from None
        require(isinstance(payload, dict), f'Asset {name}: invalid 201 response; preserve draft')
        reply = asset_identity(payload)
        require((reply['name'], reply['size'], reply['digest']) == (name, size, 'sha256:'+digest),
                f'Asset {name}: 201 response conflicts with canonical bytes; preserve draft')
        return reply
    finally:
        connection.close()


def read_owned_inventory():
    live = live_release()
    require_owned_draft(live)
    return inventory(live['assets'])


def require_canonical_subset(actual, expected):
    for name, record in actual.items():
        require(name in expected, f'Asset {name}: unexpected name in pinned draft; preserve draft')
        require({key: record[key] for key in ('size', 'digest')} == expected[name],
                f'Asset {name}: size/SHA-256 differs from canonical bytes; preserve draft')


def read_append(expected, before, name, reply):
    for delay in VISIBILITY_DELAYS:
        if delay:
            time.sleep(delay)
        actual = read_owned_inventory()
        require_canonical_subset(actual, expected)
        require(all(actual.get(k) == v for k, v in before.items())
                and set(actual)-set(before) <= {name},
                f'Asset {name}: other verified assets changed during append; preserve draft')
        if name in actual:
            require(reply is None or reply == actual[name],
                    f'Asset {name}: live ID differs from 201 response; preserve draft')
            return actual
    return None  # Repeated complete and unchanged reads, not a failed or partial read.


def append_missing(path, expected, before, deadline):
    name = filename(path.name)
    require(name in expected and name not in before, f'Asset {name}: append must target missing canonical bytes')
    for attempt in (1, 2):
        require(time.monotonic() + UPLOAD_TOTAL_SECONDS + sum(VISIBILITY_DELAYS) + 1 <= deadline,
                f'Asset {name}: insufficient append time budget; preserve draft')
        print(f'Asset {name}: POST attempt {attempt}', flush=True)
        reply, retryable = None, False
        try:
            reply = upload(path, expected[name]['size'], expected[name]['digest'][7:])
            outcome = '201'
        except AmbiguousUpload as error:
            outcome, retryable = str(error), error.retryable
        time.sleep(1)  # Pace writes (GitHub secondary rate limits), including retry.
        print(f'Asset {name}: {outcome}; reconciling live inventory', flush=True)
        after = read_append(expected, before, name, reply)
        if after is not None:
            print(f'Asset {name}: verified live ID {after[name]["id"]}', flush=True)
            return after
        require(attempt == 1 and retryable,
                f'Asset {name}: append not confirmed after bounded readback ({outcome}); preserve draft')
        print(f'Asset {name}: absent from unchanged inventories; allowing one timeout retry', flush=True)
    raise AssertionError('Unreachable append state')


def download_asset(asset, root):
    stream(f'releases/assets/{asset["id"]}', root/asset['name'],
           asset['size'], asset['digest'][7:], binary=True)


def state_root():
    return Path(os.environ['RUNNER_TEMP'])/'shirube-recover14'


def aggregate_view(source, root, manifests):
    for arch, manifest in manifests.items():
        (root/f'candidate-{arch}.json').write_text(json.dumps(manifest))
    original_validator(source, 'aggregate', root)


def asset_bytes(path):
    return {'size': path.stat().st_size, 'digest': 'sha256:'+actions.sha256(path)}


def canonical_inputs(source, root):
    """Verify and retain both original candidates before any release writes."""
    canonical = root/'canonical'
    canonical.mkdir()
    manifests, expected = {}, {}
    for arch, pin in CANDIDATES.items():
        with tempfile.TemporaryDirectory(dir=root) as tmp:
            work = Path(tmp)
            original_archive(pin, work/'archive.zip')
            extract_candidate(work/'archive.zip', work/'candidate', pin.size)
            (work/'archive.zip').unlink()
            manifest = original_validator(source, 'candidate', work/'candidate', arch)
            manifests[arch] = manifest
            for item in manifest['assets']:
                name = filename(item['name'])
                require(name.startswith(f'shirube-os-{VERSION}-{arch}') and name not in expected,
                        f'Asset {name}: candidate name not architecture-owned or duplicate')
                original = work/'candidate'/name
                require(original.is_file() and not original.is_symlink(), f'Asset {name}: not a regular file')
                os.link(original, canonical/name)
                expected[name] = {'size': item['size'], 'digest': 'sha256:'+item['sha256']}
    require(len(expected)+len(METADATA) <= RELEASE_ASSET_LIMIT
            and sum(a['size'] for a in expected.values()) <= sum(p.size for p in CANDIDATES.values()),
            'Canonical output exceeds the pinned release footprint')
    aggregate_view(source, canonical, manifests)
    expected.update({name: asset_bytes(canonical/name) for name in METADATA})
    require({p.name for p in canonical.iterdir()} == set(expected), 'Unexpected canonical artifact scope')
    return canonical, manifests, expected


def stage():
    # Reserve nominal time within the 180-minute job for independent remote
    # verification and the new artifact. Keep the earlier artifact expiry.
    remaining = min((datetime.fromisoformat(p.expiry)-utcnow()).total_seconds() for p in CANDIDATES.values())
    deadline = time.monotonic() + min(APPEND_WINDOW_SECONDS, remaining)
    source, context = controller_context()
    root = state_root()
    root.mkdir()  # Never reuse local files or proof from another dispatch.
    capacity(root)
    original_evidence(root)
    canonical, manifests, expected = canonical_inputs(source, root)
    staged = inventory(owned_draft()['assets'])
    require_canonical_subset(staged, expected)
    print(f'Verified {len(staged)} existing draft assets against {len(expected)} canonical files', flush=True)

    payload = [name for name in expected if name not in METADATA]
    for name in payload:
        if name not in staged:
            staged = append_missing(canonical/name, expected, staged, deadline)
    draft(staged)
    public = root/'public'
    public.mkdir()
    for name in payload:
        download_asset(staged[name], public)
    aggregate_view(source, public, manifests)
    for name in METADATA:
        require(asset_bytes(public/name) == expected[name],
                f'Asset {name}: independent aggregate disagrees with canonical bytes; preserve draft')
    # Follow the original order: no metadata append until all actually staged
    # payload bytes pass fresh aggregate validation. Existing metadata was
    # already compared to the canonical inventory even before payload writes.
    draft(staged)
    with tempfile.TemporaryDirectory(dir=root) as tmp:
        for name in METADATA:
            if name not in staged:
                staged = append_missing(canonical/name, expected, staged, deadline)
            download_asset(staged[name], Path(tmp))
            require(asset_bytes(Path(tmp)/name) == expected[name],
                    f'Asset {name}: independent metadata bytes differ; preserve draft')
    confirmed = draft(staged)
    require(set(staged) == set(expected) == {p.name for p in public.iterdir()},
            'Unexpected output artifact scope')
    snapshot = actions.release_snapshot(confirmed)
    (root/'verified-draft.json').write_text(json.dumps({'context': context, 'assets': staged,
                                                       'snapshot': snapshot}))
    print(f'Staged and independently validated {len(staged)} assets on draft {RELEASE_ID}', flush=True)


def check_new_upload(context):
    ident = os.environ.get('SHIRUBE_ACTIONS_ARTIFACT_ID', '')
    digest = os.environ.get('SHIRUBE_ACTIONS_ARTIFACT_DIGEST', '')
    require(re.fullmatch('[1-9][0-9]*', ident), 'Missing newly uploaded Actions artifact ID')
    record = api(f'actions/artifacts/{ident}')
    actions.artifact_evidence(record, ident, digest, context['run_id'], context['attempt'], VERSION, SOURCE)
    bound_run(record.get('workflow_run', {}), int(context['run_id']), 'main', context['controller'])
    return ident, digest


def public_snapshot(live):
    # The browser URL changes from the draft untagged path to the release tag.
    return actions.release_snapshot(dict(live, assets=[
        {k: v for k, v in a.items() if k != 'browser_download_url'} for a in live['assets']]))


def verify_published(expected):
    current = live_release()
    require(public_snapshot(current) == public_snapshot(expected),
            'Publication readback differs from the verified release; do not retry')
    check_tag(optional=False)


def promote():
    _, context = controller_context()
    root = state_root()
    ready = json.loads((root/'verified-draft.json').read_text())
    require(ready['context'] == context, 'Staged verification was not made by this recovery run')
    original_evidence(root)
    ident, digest = check_new_upload(context)
    live = draft(ready['assets'])
    require(json.dumps(actions.release_snapshot(live)) == json.dumps(ready['snapshot']),
            'Draft changed after independent staging verification')
    notes = (live['body']+f'\n\nRecovery source: {SOURCE}; controller: {context["controller"]}.'
             f'\nOriginal run: https://github.com/{REPO}/actions/runs/{ORIGINAL_RUN}/attempts/2.'
             f'\nVerified Actions artifact: https://github.com/{REPO}/actions/runs/{context["run_id"]}/artifacts/{ident}'
             f'\nArtifact SHA256: {digest}\n')
    expected = dict(live, body=notes, draft=False)
    # PATCH the ID once, then reconcile by ID, inventory and tag, not by a
    # response's status/body and never by reissuing a mutation.
    patch = root/'release-patch.json'
    with patch.open('x') as output:
        json.dump({'body': notes, 'draft': False}, output)
    require(all(datetime.fromisoformat(p.expiry) > utcnow() for p in CANDIDATES.values()),
            'A source candidate expired before promotion')
    try:
        actions.gh('api', '--method', 'PATCH', f'repos/{REPO}/{RELEASE_PATH}', '--input', str(patch))
    except (OSError, subprocess.CalledProcessError):
        verify_published(expected)
    else:
        verify_published(expected)
    print(f'Recovered and verified {VERSION} on release {RELEASE_ID}', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('step', choices=('stage', 'promote'))
    {'stage': stage, 'promote': promote}[parser.parse_args().step]()
