#!/usr/bin/env python3
"""Host-only canonical readers and trusted Release/seed kernel compiler producers.

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
# Kind -> top-level workflow, event, exact reusable job prefix.
PRODUCER_KINDS = {
    'build': ('release.yml', 'pull_request_target', 'build'),
    'candidate-validation': ('release.yml', 'pull_request_target', 'candidate-validation'),
    'validate-main': ('validate-staged.yml', 'workflow_dispatch', 'validate-main'),
    'manual-seed': ('kernel-seed.yml', 'workflow_dispatch', 'seed'),
    'automatic-seed': ('release.yml', 'pull_request_target', 'kernel-seed'),
}
PRODUCERS = (('release.yml', 'pull_request_target'), ('kernel-seed.yml', 'workflow_dispatch'))
INDEX_LIMIT = 64 * 1024
SEARCH_LIMIT = 10


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


def seed_context(env, event, commit):
    """A dispatch owns only its immutable canonical main workflow source."""
    require(env.get('KERNEL_CACHE_WRITE') == 'true'
            and env.get('GITHUB_EVENT_NAME') == 'workflow_dispatch'
            and env.get('GITHUB_REPOSITORY') == REPO and same_repo(event.get('repository', {}))
            and env.get('GITHUB_REF') == 'refs/heads/main'
            and re.fullmatch('[0-9a-f]{40}', commit)
            and commit == env.get('GITHUB_SHA') == env.get('GITHUB_WORKFLOW_SHA')
            and env.get('GITHUB_WORKFLOW_REF') == REPO + '/.github/workflows/kernel-seed.yml@refs/heads/main')


def consumer_context(env, event, commit):
    """Canonical exact-source readers, independent of shared-cache ownership."""
    require(env.get('GITHUB_REPOSITORY') == REPO and same_repo(event.get('repository', {}))
            and re.fullmatch('[0-9a-f]{40}', commit)
            and re.fullmatch('[0-9a-f]{40}', env.get('GITHUB_SHA', ''))
            and env.get('CACHE_MODE') in {'cold', 'warm'})
    name, ref = env.get('GITHUB_EVENT_NAME'), env.get('GITHUB_REF', '')
    pr = event.get('pull_request', {})
    if name == 'workflow_dispatch':
        require(ref.startswith('refs/heads/') and len(ref) > len('refs/heads/'))
        if ref == 'refs/heads/main':
            require(commit == env['GITHUB_SHA'])
    elif name == 'pull_request_target':
        require(event.get('action') == 'closed' and pr.get('state') == 'closed'
                and pr.get('merged') is True and ref == 'refs/heads/main'
                and pr.get('base', {}).get('ref') == 'main'
                and same_repo(pr.get('base', {}).get('repo', {}))
                and commit == pr.get('merge_commit_sha'))
    elif name == 'pull_request':
        require(event.get('action') == 'labeled'
                and event.get('label', {}).get('name') in {'validate-staged', 'validate-staged-warm'}
                and re.fullmatch(r'refs/pull/[1-9][0-9]*/merge', ref)
                and same_repo(pr.get('base', {}).get('repo', {}))
                and commit == pr.get('head', {}).get('sha'))
    else:
        require(False)


def instant(value):
    require(isinstance(value, str) and value.endswith('Z'))
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


def workflow_path(value, filename):
    # GitHub documents both plain paths and paths suffixed with @main.
    path = '.github/workflows/' + filename
    return value in {path, path + '@main', path + '@refs/heads/main'}


def verify_producer(api, run, artifact, receipt, architecture):
    """Authority comes from GitHub's run/PR/job records, never receipt claims."""
    modern = receipt.get('schema_version') == 3
    kind = receipt.get('producer_kind') if modern else ('manual-seed' if workflow_path(run.get('path'), 'kernel-seed.yml') else 'build')
    require(kind in PRODUCER_KINDS)
    workflow, event, prefix = PRODUCER_KINDS[kind]
    seed = event == 'workflow_dispatch'
    require(workflow_path(run.get('path'), workflow) and run.get('event') == event)
    require(run.get('status') == 'completed' and run.get('conclusion') == 'success'
            and same_repo(run.get('repository', {})) and same_repo(run.get('head_repository', {})))
    common = {'schema_version', 'source_commit', 'run_id', 'run_attempt',
              'architecture', 'export_sha256', 'export_bytes'}
    extra = ({'producer_kind', 'workflow_sha', 'source_ref', 'coverage_key', 'measured'} |
             (set() if seed else {'source_pr'})) if modern else ({'producer_kind', 'workflow_sha', 'source_ref'} if seed else {'source_pr'})
    require(set(receipt) == common | extra
            and receipt['schema_version'] == (3 if modern else 2 if seed else 1)
            and receipt['architecture'] == architecture
            and type(run['id']) is int and run['id'] > 0
            and type(run['run_attempt']) is int and run['run_attempt'] > 0
            and type(receipt['run_id']) is int and type(receipt['run_attempt']) is int
            and receipt['run_id'] == run['id'] and receipt['run_attempt'] == run['run_attempt']
            and re.fullmatch('[0-9a-f]{40}', receipt['source_commit'])
            and re.fullmatch('[0-9a-f]{64}', receipt['export_sha256'])
            and type(receipt['export_bytes']) is int and 0 < receipt['export_bytes'] <= LIMIT)
    if modern:
        validate_measurements(receipt, architecture)
        require(receipt['source_ref'] == 'refs/heads/main' and receipt['workflow_sha'] == receipt['source_commit'])
    commit = receipt['source_commit']
    reusable = REPO + '/.github/workflows/staged-candidate.yml@'
    references = [r for r in run.get('referenced_workflows', [])
                  if r.get('path') in {reusable + commit, reusable + 'main', reusable + 'refs/heads/main'}]
    require(len(references) == 1 and references[0].get('sha') == commit
            and (references[0].get('ref') == 'refs/heads/main'
                 or (references[0].get('path') == reusable + commit and 'ref' not in references[0])))
    if seed:
        require(receipt['producer_kind'] == (kind if modern else 'kernel-seed')
                and receipt['source_ref'] == 'refs/heads/main'
                and receipt['workflow_sha'] == commit == run.get('head_sha')
                and run.get('head_branch') == 'main')
        # Never consult the current main tip: GitHub's immutable run source and
        # same-commit local reusable workflow are the dispatch authority.
    else:
        require(type(receipt['source_pr']) is int and receipt['source_pr'] > 0)
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
    # Attempt authority is the documented attempt-specific endpoint; job JSON
    # has no supported run_attempt field.
    jobs = api.json(f"actions/runs/{run['id']}/attempts/{run['run_attempt']}/jobs?per_page=100")
    require(jobs['total_count'] == len(jobs['jobs']))
    runner = 'ubuntu-24.04' + ('-arm' if architecture == 'arm64' else '')
    selected = [j for j in jobs['jobs'] if j.get('name') == f"{prefix} / kernel ({architecture}, {runner})"]
    require(len(selected) == 1)
    job = selected[0]
    require(job.get('status') == 'completed' and job.get('conclusion') == 'success'
            and job.get('run_id') == run['id']
            and job.get('head_sha') == run['head_sha']
            and instant(run['created_at']) <= instant(job['started_at'])
            and instant(job['started_at']) <= instant(artifact['created_at']) <= instant(job['completed_at']))


def inspect_zip(path):
    with zipfile.ZipFile(path) as archive:
        members = archive.infolist()
        require(len(members) == 2 and {m.filename for m in members} == {'export.tar', 'provenance.json'})
        require(all(not m.is_dir() and stat.S_IFMT(m.external_attr >> 16) in (0, stat.S_IFREG)
                    and not m.flag_bits & 1 and m.file_size <= LIMIT for m in members))
        require(archive.getinfo('provenance.json').file_size <= 16384)
        return json.loads(archive.read('provenance.json'))


def import_cache(api, directory, architecture, coverage_key=None):
    """Bounded searches: ten successful Release runs, then ten dedicated seed runs."""
    if coverage_key is not None:
        status, choice = find_index(api, architecture, coverage_key)
        if status == 'covered':
            restore_artifact(api, directory, architecture, *choice)
            return 'RESTORED_TRUSTED_ARTIFACT'
    for workflow, event in PRODUCERS:
        runs = api.json(f'actions/workflows/{workflow}/runs?event={event}&status=success&per_page=10')
        for candidate in runs['workflow_runs'][:10]:
            run = api.json(f"actions/runs/{candidate['id']}")
            require(run['id'] == candidate['id'] and workflow_path(run.get('path'), workflow)
                    and run.get('event') == event)
            name = f"shirube-kernel-compiler-v1-{architecture}-{run['run_attempt']}"
            artifacts = api.json(f"actions/runs/{run['id']}/artifacts?per_page=100")
            require(artifacts['total_count'] == len(artifacts['artifacts']))
            choices = [a for a in artifacts['artifacts'] if a['name'] == name and not a['expired']]
            if not choices:
                continue
            require(len(choices) == 1)
            artifact = api.json(f"actions/artifacts/{choices[0]['id']}")
            require(artifact['id'] == choices[0]['id'] and artifact['name'] == name and 0 < artifact['size_in_bytes'] <= LIMIT + 1024**2)
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


def producer_context(env, event, commit):
    """Independent kernel writer; other component authority is unchanged."""
    kind = env.get('PRODUCER_KIND')
    require(kind in PRODUCER_KINDS and env.get('KERNEL_CACHE_WRITE') == 'true')
    workflow, name, _ = PRODUCER_KINDS[kind]
    require(env.get('GITHUB_EVENT_NAME') == name and env.get('GITHUB_REPOSITORY') == REPO
            and same_repo(event.get('repository', {})) and env.get('GITHUB_REF') == 'refs/heads/main'
            and env.get('GITHUB_WORKFLOW_REF') == REPO + '/.github/workflows/'+workflow+'@refs/heads/main')
    require(re.fullmatch('[0-9a-f]{40}', commit) is not None)
    if name == 'workflow_dispatch':
        require(commit == env.get('GITHUB_SHA') == env.get('GITHUB_WORKFLOW_SHA'))
    else:
        pr = event.get('pull_request', {})
        require(event.get('action') == 'closed' and pr.get('state') == 'closed' and pr.get('merged') is True
                and pr.get('merge_commit_sha') == commit and pr.get('base', {}).get('ref') == 'main'
                and same_repo(pr.get('base', {}).get('repo', {})))
    return kind


def validate_measurements(receipt, architecture):
    require(re.fullmatch('[0-9a-f]{64}', receipt['coverage_key']) is not None)
    measured = receipt['measured']
    require(set(measured) == {'scope', 'namespace', 'export'}
            and measured['scope'].get('schema_version') == 1
            and measured['scope'].get('architecture') == architecture
            and set(measured['scope']) == {'schema_version', 'architecture', 'toolchain'}
            and re.fullmatch('[0-9a-f]{64}', measured['scope']['toolchain']) is not None
            and re.fullmatch('[0-9a-f]{64}', measured['namespace']) is not None
            and set(measured['export']) == {'entries', 'skipped_capacity'}
            and type(measured['export']['entries']) is int and 0 < measured['export']['entries'] <= 50000
            and type(measured['export']['skipped_capacity']) is int and measured['export']['skipped_capacity'] >= 0)


def export_name(architecture, attempt):
    return f'shirube-kernel-compiler-v1-{architecture}-{attempt}'


def index_name(architecture, key):
    require(architecture in {'amd64', 'arm64'} and re.fullmatch('[0-9a-f]{64}', key))
    return f'shirube-kernel-index-v1-{architecture}-{key}'


def artifact_binding(artifact):
    return {k: artifact[k] for k in ('id', 'name', 'digest', 'size_in_bytes', 'created_at', 'expires_at', 'workflow_run')}


def validate_artifact(artifact, *, limit):
    require(type(artifact['id']) is int and artifact['id'] > 0
            and type(artifact['size_in_bytes']) is int and 0 < artifact['size_in_bytes'] <= limit
            and artifact['expired'] is False
            and instant(artifact['expires_at']) > datetime.now().astimezone()
            and instant(artifact['created_at']) < instant(artifact['expires_at'])
            and re.fullmatch('sha256:[0-9a-f]{64}', artifact['digest']) is not None)


def find_index(api, architecture, key):
    """Exact-name metadata across ALL kinds before any legacy export download.

    Ten tiny candidates maximum; an incomplete window, malformed record or API
    error cannot establish absence. A verified match can establish coverage even
    in an incomplete window. Whole-run failure never supplies a usable sibling.
    """
    unknown = False
    try:
        name = index_name(architecture, key)
        page = api.json(f'actions/artifacts?name={name}&per_page={SEARCH_LIMIT}')
        candidates = page['artifacts']
        require(type(page['total_count']) is int and page['total_count'] >= len(candidates)
                and len(candidates) <= SEARCH_LIMIT)
        require(len({entry['id'] for entry in candidates}) == len(candidates))
        unknown = page['total_count'] > len(candidates)
        for entry in candidates:
            try:
                require(entry['name'] == name)
                if entry['expired'] is True or instant(entry['expires_at']) <= datetime.now().astimezone():
                    continue
                index = api.json(f"actions/artifacts/{entry['id']}")
                require(index['id'] == entry['id'] and index['name'] == name)
                validate_artifact(index, limit=INDEX_LIMIT)
                run = api.json(f"actions/runs/{index['workflow_run']['id']}")
                # Reject foreign/unsupported contexts before fetching any ZIP.
                require(same_repo(run.get('repository', {})) and same_repo(run.get('head_repository', {})))
                require(any(workflow_path(run.get('path'), w) and run.get('event') == e
                            for w, e, _ in PRODUCER_KINDS.values()))
                if run.get('status') != 'completed':
                    unknown = True
                    continue
                if run.get('conclusion') != 'success':
                    continue
                with tempfile.TemporaryDirectory(prefix='kernel-index-') as tmp:
                    target = Path(tmp)/'index.zip'
                    api.download(index['id'], target, index['size_in_bytes'])
                    require('sha256:'+sha256(target) == index['digest'])
                    with zipfile.ZipFile(target) as archive:
                        members = archive.infolist()
                        require(len(members) == 1 and members[0].filename == 'index.json'
                                and not members[0].is_dir() and not members[0].flag_bits & 1
                                and stat.S_IFMT(members[0].external_attr >> 16) in (0, stat.S_IFREG)
                                and members[0].file_size <= INDEX_LIMIT)
                        record = json.loads(archive.read('index.json'))
                require(set(record) == {'schema_version', 'receipt', 'export_artifact'} and record['schema_version'] == 1)
                receipt = record['receipt']
                require(receipt['schema_version'] == 3 and receipt['coverage_key'] == key)
                verify_producer(api, run, index, receipt, architecture)
                export = api.json(f"actions/artifacts/{record['export_artifact']['id']}")
                if export.get('expired') is True or instant(export['expires_at']) <= datetime.now().astimezone():
                    continue
                validate_artifact(export, limit=LIMIT + 1024**2)
                require(artifact_binding(export) == record['export_artifact']
                        and export['id'] != index['id']
                        and export['name'] == export_name(architecture, run['run_attempt'])
                        and instant(export['created_at']) <= instant(index['created_at']))
                verify_producer(api, run, export, receipt, architecture)
                return 'covered', (run, export, receipt)
            except Exception:
                unknown = True
    except Exception:
        unknown = True
    return ('unknown' if unknown else 'missing'), None


def restore_artifact(api, directory, architecture, run, artifact, expected):
    """The large export is downloaded only by an actual compiler-cache reader."""
    with tempfile.TemporaryDirectory(prefix='kernel-compiler-import-', dir=directory.parent) as tmp:
        downloaded = Path(tmp)/'artifact.zip'
        api.download(artifact['id'], downloaded, artifact['size_in_bytes'])
        require('sha256:'+sha256(downloaded) == artifact['digest'])
        receipt = inspect_zip(downloaded)
        require(receipt == expected)
        verify_producer(api, run, artifact, receipt, architecture)
        with zipfile.ZipFile(downloaded) as archive:
            require(archive.getinfo('export.tar').file_size == receipt['export_bytes'])
            target = Path(tmp)/'seed.tar'
            with archive.open('export.tar') as source, target.open('xb') as output:
                shutil.copyfileobj(source, output)
        require(sha256(target) == receipt['export_sha256'])
        target.rename(directory/'seed.tar')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('import', 'receipt', 'index'))
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--architecture', choices=('amd64', 'arm64'), required=True)
    parser.add_argument('--commit', required=True)
    args = parser.parse_args()
    event = json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text())
    if args.operation == 'import':
        consumer_context(os.environ, event, args.commit)
    else:
        kind = producer_context(os.environ, event, args.commit)
    directory = args.directory
    require(not directory.is_symlink() and directory.resolve() != Path('/') and len(directory.resolve().parts) >= 3)
    if args.operation == 'import':
        directory.mkdir(parents=True, exist_ok=False)
        status = 'COLD'
        if os.environ.get('CACHE_MODE') == 'warm':
            try:
                from producer.kernel_seed import key
                status = import_cache(API(os.environ.get('GH_TOKEN')), directory, args.architecture, key(Path('.'), args.architecture))
            except Exception:
                # Optional acceleration includes HTTPException/IncompleteRead,
                # truncated streams and metadata errors. Fail closed to a fresh
                # build without leaking response bodies or signed URLs.
                (directory / 'seed.tar').unlink(missing_ok=True)
                status = 'MISS_INVALID_OR_UNAVAILABLE'
        print('Kernel compiler cache: ' + status)
    elif args.operation == 'index':
        api = API(os.environ.get('GH_TOKEN'))
        receipt = json.loads((directory/'provenance.json').read_text())
        require(receipt['schema_version'] == 3 and receipt['producer_kind'] == kind
                and receipt['source_commit'] == args.commit and receipt['architecture'] == args.architecture
                and receipt['run_id'] == int(os.environ['GITHUB_RUN_ID'])
                and receipt['run_attempt'] == int(os.environ['GITHUB_RUN_ATTEMPT']))
        validate_measurements(receipt, args.architecture)
        identity = int(os.environ['EXPORT_ARTIFACT_ID'])
        artifact = api.json(f'actions/artifacts/{identity}')
        validate_artifact(artifact, limit=LIMIT + 1024**2)
        output_digest = os.environ['EXPORT_ARTIFACT_DIGEST']
        require(re.fullmatch('[0-9a-f]{64}', output_digest) is not None)
        # A merged pull_request_target run belongs to the PR head, while the
        # compiled source is its merge commit. The trusted event supplies that
        # run identity; dispatch runs instead belong to their exact source.
        run_sha = (event.get('pull_request', {}).get('head', {}).get('sha', '')
                   if PRODUCER_KINDS[kind][1] == 'pull_request_target' else args.commit)
        require(re.fullmatch('[0-9a-f]{40}', run_sha) is not None)
        require(artifact['id'] == identity and artifact['digest'] == 'sha256:'+output_digest
                and artifact['name'] == export_name(args.architecture, int(os.environ['GITHUB_RUN_ATTEMPT']))
                and artifact['workflow_run']['id'] == int(os.environ['GITHUB_RUN_ID'])
                and artifact['workflow_run']['repository_id'] == REPO_ID
                and artifact['workflow_run']['head_sha'] == run_sha)
        record = dict(schema_version=1, receipt=receipt, export_artifact=artifact_binding(artifact))
        (directory/'index.json').write_text(json.dumps(record, sort_keys=True)+'\n')
        print('index-name='+index_name(args.architecture, receipt['coverage_key']))
    else:
        from producer.kernel_seed import key
        archive = directory / 'export.tar'
        require(archive.is_file() and not archive.is_symlink() and 0 < archive.stat().st_size <= LIMIT)
        metrics = json.loads((directory/'metrics.json').read_text())
        measured = {name: metrics[name] for name in ('scope', 'namespace', 'export')}
        receipt = dict(schema_version=3, source_commit=args.commit, producer_kind=kind,
                       workflow_sha=args.commit, source_ref='refs/heads/main',
                       coverage_key=key(Path('.'), args.architecture), measured=measured,
                       run_id=int(os.environ['GITHUB_RUN_ID']), run_attempt=int(os.environ['GITHUB_RUN_ATTEMPT']),
                       architecture=args.architecture, export_sha256=sha256(archive), export_bytes=archive.stat().st_size)
        if PRODUCER_KINDS[kind][1] == 'pull_request_target':
            receipt['source_pr'] = event['pull_request']['number']
        validate_measurements(receipt, args.architecture)
        (directory / 'provenance.json').write_text(json.dumps(receipt, sort_keys=True) + '\n')


if __name__ == '__main__':
    main()
