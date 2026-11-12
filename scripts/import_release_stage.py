#!/usr/bin/env python3
"""Bounded, host-only migration of one fixed Release's unsigned transfers.

An import is an optional cache hint. Every rejection falls back to compilation;
credentials and remote error bodies must never become output or receipts.
"""
import argparse
import ctypes
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import sys
import tarfile
import tempfile
import time
import urllib.parse
import urllib.request
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from producer.component_inputs import CACHEABLE, outer_key
from producer.staged import build_scope
from producer.stage_artifacts import unpack
from producer.sources import sha256

REPOSITORY = 'michibiki-io/shirube-os'
REPOSITORY_ID = 1449410161
RUN = 43164071342
COMMIT = '4498c6104b65dfae37be1e5ebe8d78c27100884b'
HEAD = '42f6706d21f7bf306e9d8bcbf7af968242763cdf'
CUTOFF = '2026-11-19T05:50:46Z'
METADATA_LIMIT = 64 * 1024**2
API_LIMIT = 2 * 1024**2
TIME_LIMIT = 600
DISK_MARGIN = 512 * 1024**2


def require(condition):
    if not condition:
        raise ValueError('Import evidence or eligibility differs')


def timestamp(value):
    require(isinstance(value, str) and value.endswith('Z'))
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


def load_manifest(path):
    require(path.stat().st_size <= API_LIMIT)
    data = json.loads(path.read_bytes())
    require(data['schema_version'] == 1 and data['repository'] == REPOSITORY
            and data['repository_id'] == REPOSITORY_ID and data['run_id'] == RUN
            and data['run_attempt'] == 1 and data['workflow_id'] == 414368882
            and data['workflow_path'] == '.github/workflows/release.yml'
            and data['source_pr'] == 21 and data['source_commit'] == COMMIT
            and data['head_sha'] == HEAD and data['head_branch'] == 'feature/#20'
            and data['version'] == 'v2026.03.8' and data['expires_at'] == CUTOFF)
    require(data['referenced_workflow'] == {
        'path': REPOSITORY + '/.github/workflows/staged-candidate.yml@' + COMMIT,
        'sha': COMMIT, 'ref': 'refs/heads/main'})
    entries = data['entries']
    require(len(entries) == 10 and {(e['stage'], e['architecture']) for e in entries}
            == {(s, a) for s in CACHEABLE for a in ('amd64', 'arm64')})
    for e in entries:
        require(re.fullmatch('[0-9a-f]{64}', e['input_id']) is not None
                and re.fullmatch('sha256:[0-9a-f]{64}', e['compiler_fingerprint']) is not None)
        require(set(e['transfer']) == {'bytes', 'sha256'}
                and 0 < e['transfer']['bytes'] <= 2 * 1024**3
                and re.fullmatch('[0-9a-f]{64}', e['transfer']['sha256']) is not None)
        a, j = e['artifact'], e['job']
        require(a['name'] == 'shirube-stage-' + e['architecture'] + '-' + e['stage']
                and re.fullmatch('sha256:[0-9a-f]{64}', a['digest']) is not None
                and 0 < a['size_in_bytes'] <= 2 * 1024**3
                and timestamp(a['expires_at']) > timestamp(CUTOFF))
        runner = 'ubuntu-24.04' + ('-arm' if e['architecture'] == 'arm64' else '')
        require(j['name'] == f"build / {e['stage']} ({e['architecture']}, {runner})"
                and j['run_id'] == RUN and j['run_attempt'] == 1
                and j['head_sha'] == HEAD and j['conclusion'] == 'success'
                and timestamp(j['started_at']) <= timestamp(a['created_at']) <= timestamp(j['completed_at']))
    require(len({e['artifact']['id'] for e in entries}) == 10
            and len({e['job']['id'] for e in entries}) == 10)
    return data


def current_context(env, event, commit, now):
    pr = event.get('pull_request', {})
    repo = event.get('repository', {})
    base = pr.get('base', {})
    require(env.get('CACHE_WRITE') == 'true' and env.get('CACHE_MODE') == 'warm'
            and env.get('CACHE_HIT') != 'true'
            and env.get('GITHUB_EVENT_NAME') == 'pull_request_target'
            and env.get('GITHUB_REPOSITORY') == REPOSITORY
            and env.get('GITHUB_REF') == 'refs/heads/main'
            and repo.get('full_name') == REPOSITORY and repo.get('id') == REPOSITORY_ID
            and event.get('action') == 'closed' and pr.get('state') == 'closed'
            and pr.get('merged') is True and base.get('ref') == 'main'
            and base.get('repo', {}).get('full_name') == REPOSITORY
            and base.get('repo', {}).get('id') == REPOSITORY_ID
            and re.fullmatch('[0-9a-f]{40}', commit) is not None
            and commit == pr.get('merge_commit_sha') and now < timestamp(CUTOFF))


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class API:
    """Never forward Authorization to signed artifact-storage redirects."""
    def __init__(self, token):
        require(bool(token))
        self.token = token
        self.deadline = time.monotonic() + TIME_LIMIT
        self.opener = urllib.request.build_opener(NoRedirect)

    def request(self, path):
        return urllib.request.Request('https://api.github.com/repos/' + REPOSITORY + '/' + path,
            headers={'Authorization': 'Bearer ' + self.token, 'Accept': 'application/vnd.github+json',
                     'X-GitHub-Api-Version': '2022-11-28'})

    def timeout(self):
        remaining = self.deadline - time.monotonic()
        require(remaining > 0)
        return min(30, remaining)

    def json(self, path):
        with self.opener.open(self.request(path), timeout=self.timeout()) as response:
            data = response.read(API_LIMIT + 1)
        require(len(data) <= API_LIMIT)
        return json.loads(data)

    def download(self, artifact_id, destination, size):
        import urllib.error
        try:
            response = self.opener.open(self.request(f'actions/artifacts/{artifact_id}/zip'), timeout=self.timeout())
        except urllib.error.HTTPError as error:
            require(error.code == 302)
            location = error.headers.get('Location', '')
            error.close()
            url = urllib.parse.urlsplit(location)
            require(url.scheme == 'https' and bool(url.hostname) and not url.username and not url.password)
            # A new request carries no API headers. Further redirects are rejected.
            response = self.opener.open(urllib.request.Request(location), timeout=self.timeout())
        with response, destination.open('xb') as output:
            length = response.headers.get('Content-Length')
            require(length is None or int(length) == size)
            total = 0
            while True:
                self.timeout()
                block = response.read(min(1024**2, size - total + 1))
                if not block:
                    break
                total += len(block)
                require(total <= size)
                output.write(block)
            require(total == size)


def verify_remote(api, manifest, entry, now):
    m = manifest
    run = api.json(f'actions/runs/{RUN}')
    require(all(run.get(k) == v for k, v in {
        'id': RUN, 'workflow_id': m['workflow_id'], 'path': m['workflow_path'],
        'event': 'pull_request_target', 'run_attempt': 1, 'head_sha': HEAD,
        'head_branch': m['head_branch']}.items()))
    for key in ('repository', 'head_repository'):
        require(run[key]['id'] == REPOSITORY_ID and run[key]['full_name'] == REPOSITORY)
    require(m['referenced_workflow'] in run['referenced_workflows'])
    # The API head is the PR head; the closed-only Release and merged PR bind
    # the actual payload to the reusable main SHA. pull_requests may be empty.
    pr = api.json('pulls/21')
    require(pr['number'] == 21 and pr['state'] == 'closed' and pr['merged'] is True
            and pr['merge_commit_sha'] == COMMIT and bool(pr['closed_at']) and bool(pr['merged_at'])
            and pr['head']['sha'] == HEAD and pr['head']['ref'] == m['head_branch']
            and pr['base']['ref'] == 'main'
            and timestamp(pr['merged_at']) <= timestamp(run['created_at'])
            and timestamp(pr['closed_at']) <= timestamp(run['created_at']))
    for side in ('head', 'base'):
        require(pr[side]['repo']['id'] == REPOSITORY_ID and pr[side]['repo']['full_name'] == REPOSITORY)
    job = api.json(f"actions/jobs/{entry['job']['id']}")
    require(all(job.get(k) == v for k, v in entry['job'].items()) and job['status'] == 'completed')
    artifact = api.json(f"actions/artifacts/{entry['artifact']['id']}")
    require(all(artifact.get(k) == v for k, v in entry['artifact'].items())
            and artifact['expired'] is False and timestamp(artifact['expires_at']) > now)
    owner = artifact['workflow_run']
    require(all(owner.get(k) == v for k, v in {'id': RUN, 'repository_id': REPOSITORY_ID,
        'head_repository_id': REPOSITORY_ID, 'head_sha': HEAD, 'head_branch': m['head_branch']}.items()))


def safe_directory(path):
    path = path.absolute()
    for parent in (*reversed(path.parents), path):
        require(not parent.is_symlink())
        if parent.exists():
            require(parent.is_dir())
    path.mkdir(parents=True, exist_ok=True)
    return path


def install_fresh(source, destination):
    # Linux hosted runners: atomic directory rename with no replacement, even
    # if another process creates an empty directory between check and rename.
    libc = ctypes.CDLL(None, use_errno=True)
    result = libc.renameat2(-100, os.fsencode(source), -100, os.fsencode(destination), 1)
    if result:
        raise OSError(ctypes.get_errno(), 'Fresh cache install refused')


def verify_archive(archive, scratch, entry, expected, manifest):
    require('sha256:' + sha256(archive) == entry['artifact']['digest'])
    stage = entry['stage']
    with zipfile.ZipFile(archive) as source:
        members = source.infolist()
        require(len(members) == 2 and {m.filename for m in members} == {stage+'.tar', stage+'.json'})
        for member in members:
            mode = member.external_attr >> 16
            require(not member.is_dir() and not member.flag_bits & 1
                    and stat.S_IFMT(mode) in (0, stat.S_IFREG)
                    and member.compress_type == zipfile.ZIP_STORED)
            limit = entry['transfer']['bytes'] if member.filename.endswith('.tar') else METADATA_LIMIT
            require(0 < member.file_size <= limit and member.compress_size == member.file_size)
        pair = scratch / 'entry'; pair.mkdir()
        for suffix in ('tar', 'json'):
            with source.open(stage+'.'+suffix) as src, (pair/('transfer.'+suffix)).open('xb') as out:
                shutil.copyfileobj(src, out, 1024**2)
    tar = pair/'transfer.tar'
    require(tar.stat().st_size == entry['transfer']['bytes'] and sha256(tar) == entry['transfer']['sha256'])
    meta = json.loads((pair/'transfer.json').read_bytes())
    require(meta['scope'] == expected and meta['source_commit'] == manifest['source_commit']
            and meta['content_source_commit'] == manifest['source_commit'] and meta['version'] == manifest['version'])
    # Bound member count and expansion before invoking the existing full validator.
    with tarfile.open(tar, 'r:') as source:
        count = total = 0
        for member in source:
            count += 1; total += member.size
            require(count <= 100000 and total <= entry['transfer']['bytes'] and member.isfile())
    receipt = unpack(tar, scratch/'verified', expected, manifest['source_commit'],
                     manifest['version'], entry['transfer']['sha256'])
    require(meta == {**receipt, **entry['transfer']})
    return pair


def import_stage(project, cache, stage, architecture, key, commit, env, event, manifest, api_factory=API, now=None):
    now = now or datetime.now(timezone.utc)
    current_context(env, event, commit, now)
    entry = next(e for e in manifest['entries'] if (e['stage'], e['architecture']) == (stage, architecture))
    expected = build_scope(project, stage, architecture, entry['compiler_fingerprint'], {})
    require(expected['input_id'] == entry['input_id'] and outer_key(expected) == key)
    cache = safe_directory(cache)
    # Keep interrupted cleanup outside cache-save scope, but retain atomic rename.
    require(cache.stat().st_dev == cache.parent.stat().st_dev)
    destination = cache/entry['input_id']
    require(not destination.exists() and not destination.is_symlink())
    required = entry['artifact']['size_in_bytes'] + 2 * entry['transfer']['bytes'] + METADATA_LIMIT + DISK_MARGIN
    require(shutil.disk_usage(cache).free >= required)
    api = api_factory(env.get('GH_TOKEN', ''))
    verify_remote(api, manifest, entry, now)
    with tempfile.TemporaryDirectory(prefix='.release-import-', dir=cache.parent) as temp:
        scratch = Path(temp)
        archive = scratch/'artifact.zip'
        api.download(entry['artifact']['id'], archive, entry['artifact']['size_in_bytes'])
        require(archive.stat().st_size == entry['artifact']['size_in_bytes'])
        pair = verify_archive(archive, scratch, entry, expected, manifest)
        require(datetime.now(timezone.utc) < timestamp(CUTOFF))
        install_fresh(pair, destination)
    return 'IMPORTED'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', type=Path, default=Path('.'))
    parser.add_argument('--cache', type=Path, required=True)
    parser.add_argument('--stage', choices=CACHEABLE, required=True)
    parser.add_argument('--architecture', choices=['amd64', 'arm64'], required=True)
    parser.add_argument('--key', required=True)
    parser.add_argument('--commit', required=True)
    args = parser.parse_args()
    def timeout(signum, frame):
        raise TimeoutError('Import deadline')
    previous = signal.signal(signal.SIGALRM, timeout)
    signal.alarm(TIME_LIMIT)
    try:
        event_path = Path(os.environ['GITHUB_EVENT_PATH'])
        require(event_path.stat().st_size <= API_LIMIT)
        event = json.loads(event_path.read_bytes())
        manifest = load_manifest(Path(__file__).with_name('release-stage-import.json'))
        status = import_stage(args.project, args.cache, args.stage, args.architecture,
                              args.key, args.commit, os.environ, event, manifest)
        print('Release stage fallback: ' + status)
    except Exception:
        # Do not print exceptions: HTTP errors can contain signed URLs/tokens.
        print('::warning::Release stage fallback: SKIPPED (ineligible, unavailable or invalid); normal source compilation continues')
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


if __name__ == '__main__':
    main()
