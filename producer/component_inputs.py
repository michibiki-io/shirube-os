"""Versioned, explicit compile dependencies in canonical repository path names.

Keep this declaration conservative when shared recipes/export contracts change.
Adding a build-time dependency requires adding it here, with mutation coverage.
"""
import argparse
import json
from pathlib import Path

from .library_cache import compiler_fingerprint, toolchain_identity
from .native_stages import COMPILE_STAGES, select_jobs
from .component_graph import graph, input_path, regular
from .sources import sha256
from .stage_artifacts import digest

SCHEMA_VERSION = 2
CACHEABLE = ('native', 'vpp', 'kea', 'salt', 'runtime')
def required_file(project, name):
    path = regular(project, name)
    return {'sha256': sha256(path), 'executable': bool(path.stat().st_mode & 0o111)}


def selected_value(value, name):
    try:
        for key in name.split('.'):
            value = value[key]
    except (KeyError, TypeError) as error:
        raise ValueError('Unknown declared lock selection: ' + name) from error
    return value


def manifest(project, stage, architecture):
    if stage not in COMPILE_STAGES or architecture not in {'amd64', 'arm64'}:
        raise ValueError('Unknown component or architecture')
    project = Path(project)
    declaration, nodes = graph(project, stage)
    lock = json.loads(regular(project, 'source-lock.json').read_text())
    structured = {'source-lock.json', 'containers/native/packages.json',
                  'containers/native/external-sources.lock.json'}
    names = sorted((set(nodes) - structured) | {
        'containers/native/inputs-common.json', 'containers/native/inputs-'+stage+'.json'})
    files = {name: required_file(project, name) for name in names}
    patches = []
    for prefix in declaration['trees']:
        root = regular(project, prefix, directory=True)
        for path in sorted(root.rglob('*')):
            name = prefix+'/'+str(path.relative_to(root))
            if path.is_dir():
                regular(project, name, directory=True)
            else:
                patches.append({'path': name, **required_file(project, name)})
    external = json.loads(regular(project, 'containers/native/external-sources.lock.json').read_text())
    catalog = json.loads(regular(project, 'containers/native/packages.json').read_text())
    jobs = select_jobs(catalog['jobs'], stage)
    if stage in {'native', 'kernel'} and not jobs:
        raise ValueError('Missing selected component jobs')
    selected = {job['name'] for job in jobs}
    if len(selected) != len(jobs) or any(dep not in selected for job in jobs for dep in job.get('requires', [])):
        raise ValueError('Incomplete component job graph')
    keys = ('stream', 'container_bases.' + architecture, 'debian_snapshot', 'debian_security_snapshot', *declaration['lock_values'])
    return {'schema_version': SCHEMA_VERSION, 'stage': stage, 'architecture': architecture,
            'toolchain': toolchain_identity(project, architecture), 'graph': nodes,
            'source_values': {key: selected_value(lock, key) for key in keys},
            'external': {key: selected_value(external, key) for key in declaration['external']},
            'catalog': {**{key: selected_value(catalog, key) for key in ('schema_version', 'stream_version', 'source_sha256')},
                        'jobs': jobs}, 'files': files, 'patches': patches}


def outer_key(record):
    """The immutable Actions key is exactly the validated inner scope identity."""
    if record['stage'] not in CACHEABLE or record['dependencies']:
        raise ValueError('Only independent eligible compile scopes can be cached')
    return 'shirube-stage-v2-' + record['stage'] + '-' + record['architecture'] + '-' + record['input_id']


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['compiler-key', 'stage-key'])
    parser.add_argument('--project', type=Path, default=Path('/src'))
    parser.add_argument('--architecture', required=True, choices=['amd64', 'arm64'])
    parser.add_argument('--stage', choices=CACHEABLE)
    args = parser.parse_args()
    if args.command == 'compiler-key':
        print('key=shirube-staged-compiler-v2-' + args.architecture + '-' + toolchain_identity(args.project, args.architecture).removeprefix('sha256:'))
    else:
        if not args.stage: parser.error('--stage required for stage-key')
        from .compile_scope import build_scope
        print('key=' + outer_key(build_scope(args.project, args.stage, args.architecture, compiler_fingerprint(), {})))


if __name__ == '__main__':
    main()
