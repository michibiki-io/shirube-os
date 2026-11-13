#!/usr/bin/env python3
"""Host-only trusted Release admission for optional kernel compiler results.

No token enters the build. An artifact digest is integrity evidence only: the
producer's main merge, workflow revision, successful job and upload must also
match. Unknown provenance falls back to compilation, never to a weaker cache.
"""
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import tempfile
import zipfile
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.import_release_stage import API
from producer.sources import sha256

REPO = 'michibiki-io/shirube-os'
REPO_ID = 1449410161
LIMIT = 2 * 1024**3


def require(value):
    if not value:
        raise ValueError('Kernel compiler producer admission rejected')


def same_repo(repo):
    return repo.get('full_name') == REPO and repo.get('id') == REPO_ID


def current_context(env, event, commit):
    pr = event.get('pull_request', {})
    require(env.get('CACHE_WRITE') == 'true' and env.get('GITHUB_EVENT_NAME') == 'pull_request_target'
            and env.get('GITHUB_REPOSITORY') == REPO and env.get('GITHUB_REF') == 'refs/heads/main'
            and same_repo(event.get('repository', {})) and event.get('action') == 'closed'
            and pr.get('state') == 'closed' and pr.get('merged') is True
            and pr.get('base', {}).get('ref') == 'main' and same_repo(pr.get('base', {}).get('repo', {}))
            and re.fullmatch('[0-9a-f]{40}', commit) and pr.get('merge_commit_sha') == commit)
    return pr


def instant(value):
    require(isinstance(value, str) and value.endswith('Z'))
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


def verify_producer(api, run, artifact, receipt, architecture):
    """Authority comes from GitHub's run/PR/job records, never receipt claims."""
    require(run.get('event') == 'pull_request_target' and run.get('path') == '.github/workflows/release.yml'
            and run.get('status') == 'completed' and run.get('conclusion') == 'success'
            and same_repo(run.get('repository', {})) and same_repo(run.get('head_repository', {})))
    require(set(receipt) == {'schema_version', 'source_commit', 'source_pr', 'run_id', 'run_attempt',
                             'architecture', 'export_sha256', 'export_bytes'}
            and receipt['schema_version'] == 1 and receipt['architecture'] == architecture
            and receipt['run_id'] == run['id'] and receipt['run_attempt'] == run['run_attempt']
            and type(receipt['source_pr']) is int and receipt['source_pr'] > 0
            and re.fullmatch('[0-9a-f]{40}', receipt['source_commit'])
            and re.fullmatch('[0-9a-f]{64}', receipt['export_sha256'])
            and type(receipt['export_bytes']) is int and 0 < receipt['export_bytes'] <= LIMIT)
    commit = receipt['source_commit']
    reference = {'path': REPO + '/.github/workflows/staged-candidate.yml@' + commit,
                 'sha': commit, 'ref': 'refs/heads/main'}
    require(reference in run.get('referenced_workflows', []))
    pr = api.json(f"pulls/{receipt['source_pr']}")
    require(pr.get('state') == 'closed' and pr.get('merged') is True and pr.get('merge_commit_sha') == commit
            and pr.get('base', {}).get('ref') == 'main' and same_repo(pr.get('base', {}).get('repo', {}))
            and pr.get('head', {}).get('sha') == run.get('head_sha')
            and pr.get('head', {}).get('ref') == run.get('head_branch')
            and instant(pr['merged_at']) <= instant(run['created_at']))
    owner = artifact.get('workflow_run', {})
    require(owner.get('id') == run['id'] and owner.get('repository_id') == REPO_ID
            and owner.get('head_sha') == run['head_sha'] and artifact.get('expired') is False
            and instant(artifact['expires_at']) > datetime.now().astimezone()
            and re.fullmatch('sha256:[0-9a-f]{64}', artifact.get('digest', '')))
    jobs = api.json(f"actions/runs/{run['id']}/attempts/{run['run_attempt']}/jobs?per_page=100")
    require(jobs['total_count'] == len(jobs['jobs']))
    runner = 'ubuntu-24.04' + ('-arm' if architecture == 'arm64' else '')
    selected = [j for j in jobs['jobs'] if j.get('name') == f'build / kernel ({architecture}, {runner})']
    require(len(selected) == 1)
    job = selected[0]
    require(job.get('status') == 'completed' and job.get('conclusion') == 'success'
            and job.get('run_id') == run['id'] and job.get('run_attempt') == run['run_attempt']
            and job.get('head_sha') == run['head_sha']
            and instant(job['started_at']) <= instant(artifact['created_at']) <= instant(job['completed_at']))


def inspect_zip(path):
    with zipfile.ZipFile(path) as archive:
        members = archive.infolist()
        require(len(members) == 2 and {m.filename for m in members} == {'export.tar', 'provenance.json'})
        require(all(not m.is_dir() and stat.S_IFMT(m.external_attr >> 16) in (0, stat.S_IFREG)
                    and not m.flag_bits & 1 and m.file_size <= LIMIT for m in members))
        require(archive.getinfo('provenance.json').file_size <= 16384)
        return json.loads(archive.read('provenance.json'))


def import_cache(api, directory, architecture):
    """Bounded search of ten completed Release runs; no old-run mutation."""
    runs = api.json('actions/workflows/release.yml/runs?event=pull_request_target&status=success&per_page=10')
    for candidate in runs['workflow_runs'][:10]:
        run = api.json(f"actions/runs/{candidate['id']}")
        name = f"shirube-kernel-compiler-v1-{architecture}-{run['run_attempt']}"
        artifacts = api.json(f"actions/runs/{run['id']}/artifacts?per_page=100")
        require(artifacts['total_count'] == len(artifacts['artifacts']))
        choices = [a for a in artifacts['artifacts'] if a['name'] == name and not a['expired']]
        if not choices:
            continue
        require(len(choices) == 1)
        artifact = api.json(f"actions/artifacts/{choices[0]['id']}")
        require(artifact['name'] == name and 0 < artifact['size_in_bytes'] <= LIMIT + 1024**2)
        with tempfile.TemporaryDirectory(prefix='kernel-compiler-import-', dir=directory.parent) as temporary:
            temporary = Path(temporary)
            downloaded = temporary / 'artifact.zip'
            api.download(artifact['id'], downloaded, artifact['size_in_bytes'])
            require('sha256:' + sha256(downloaded) == artifact['digest'])
            receipt = inspect_zip(downloaded)
            verify_producer(api, run, artifact, receipt, architecture)
            with zipfile.ZipFile(downloaded) as archive:
                require(archive.getinfo('export.tar').file_size == receipt['export_bytes'])
                target = temporary / 'seed.tar'
                with archive.open('export.tar') as source, target.open('xb') as output:
                    shutil.copyfileobj(source, output)
            require(sha256(target) == receipt['export_sha256'])
            # No archive member has been supplied to the build before all gates.
            target.rename(directory / 'seed.tar')
        return 'RESTORED_TRUSTED_ARTIFACT'
    return 'MISS'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('import', 'receipt'))
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--architecture', choices=('amd64', 'arm64'), required=True)
    parser.add_argument('--commit', required=True)
    args = parser.parse_args()
    pr = current_context(os.environ, json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text()), args.commit)
    directory = args.directory
    require(not directory.is_symlink() and directory.resolve() != Path('/') and len(directory.resolve().parts) >= 3)
    if args.operation == 'import':
        directory.mkdir(parents=True, exist_ok=False)
        status = 'COLD'
        if os.environ.get('CACHE_MODE') == 'warm':
            try:
                status = import_cache(API(os.environ.get('GH_TOKEN')), directory, args.architecture)
            except (ValueError, OSError, KeyError, TypeError, AttributeError, zipfile.BadZipFile):
                # No remote error body or credential reaches the logs.
                status = 'MISS_INVALID_OR_UNAVAILABLE'
        print('Kernel compiler cache: ' + status)
    else:
        archive = directory / 'export.tar'
        require(archive.is_file() and not archive.is_symlink() and 0 < archive.stat().st_size <= LIMIT)
        receipt = dict(schema_version=1, source_commit=args.commit, source_pr=pr['number'],
                       run_id=int(os.environ['GITHUB_RUN_ID']), run_attempt=int(os.environ['GITHUB_RUN_ATTEMPT']),
                       architecture=args.architecture, export_sha256=sha256(archive), export_bytes=archive.stat().st_size)
        (directory / 'provenance.json').write_text(json.dumps(receipt, sort_keys=True) + '\n')


if __name__ == '__main__':
    main()
