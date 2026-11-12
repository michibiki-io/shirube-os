"""Versioned, explicit compile dependencies in canonical repository path names.

Keep this declaration conservative when shared recipes/export contracts change.
Adding a build-time dependency requires adding it here, with mutation coverage.
"""
import argparse
import json
from pathlib import Path

from .library_cache import compiler_fingerprint, toolchain_identity
from .native_stages import COMPILE_STAGES, select_jobs
from .sources import sha256
from .stage_artifacts import digest

SCHEMA_VERSION = 1
CACHEABLE = ('native', 'vpp', 'kea', 'salt', 'runtime')
COMMON_PRODUCER = (
    '__init__', 'component_inputs', 'staged', 'native_stages', 'native_common', 'stage_artifacts',
    'stage_metrics', 'library_cache', 'architecture', 'sources', 'resources',
    'policy', 'collect', 'packages', 'signing', 'release', 'source_fixtures',
    'public_test_keys', 'sanitize', 'branding', 'bash_source',
    'network_diagnostics', 'package_notices', 'ocaml_sources', 'bundles',
)
COMMON_FILES = (
    'containers/native/Dockerfile', 'containers/native/build.py',
    'containers/native/apt-get',
    'containers/native/apt-retries.conf', 'LICENSE', 'shirube-os-logo.svg',
    'patches/vyos/force_mtu_host.sh',
) + tuple('producer/' + name + '.py' for name in COMMON_PRODUCER)
FILES = {
    'native': ('containers/native/package.sh', 'containers/native/http_api.py',
               'containers/native/reconstruct_http_rust.py',
               'containers/native/http-api.lock.json', 'containers/native/http-rust.lock.json',
               'producer/recipes.py', 'producer/cargo_sources.py',
               'producer/http_rust_sources.py', 'producer/debian_sources.py', 'producer/salt_cargo.py'),
    'vpp': ('containers/native/extras.py.in', 'producer/vpp_inputs.py',
            'containers/native/vpp-python.lock.json', 'containers/native/vpp-external.lock.json'),
    'kea': ('containers/native/extras.py.in',),
    'salt': ('containers/native/extras.py.in', 'producer/salt_sources.py',
             'producer/salt_cargo.py', 'producer/salt_python.py', 'containers/native/salt-sources.lock.json'),
    'runtime': ('containers/native/extras.py.in', 'containers/native/fpm_package.py',
                'producer/go_modules.py', 'producer/go_notices.py'),
    'kernel': ('containers/native/package.sh', 'containers/native/extras.py.in',
               'containers/native/sign-modules.py.in', 'containers/native/fpm_package.py',
               'producer/recipes.py', 'producer/go_modules.py', 'producer/go_notices.py',
               'producer/embedded_notices.py', 'producer/sflow.py'),
}
EXTERNAL = {'native': (), 'vpp': ('vpp', 'vpp-patches'), 'kea': ('kea-packaging',),
            'salt': ('salt-tools',), 'runtime': ('vyos-utils', 'i2util'),
            'kernel': ('nat-rtsp', 'ipt-netflow')}
LOCK_VALUES = {'native': ('http_api',), 'vpp': (), 'kea': ('kea_build_tool',), 'salt': (),
               'runtime': ('go', 'go_dependency_overrides.telegraf', 'go_notice_overrides'),
               'kernel': ('embedded_legal_notices.openvpn-dco', 'embedded_legal_notices.nat-rtsp',
                          'embedded_legal_notices.vyos-ipt-netflow')}


def input_path(project, name):
    """Resolve only the Dockerfile's explicit aliases; never hash absolute names."""
    project = Path(project)
    if (project / 'containers/native').is_dir():
        return project / name
    if name.startswith('containers/native/'):
        return project / 'native' / name.removeprefix('containers/native/')
    if name.startswith('patches/vyos/'):
        return project / 'vyos-patches' / name.removeprefix('patches/vyos/')
    if name.startswith('patches/kernel/'):
        return project.parent / 'kernel-patches' / name.removeprefix('patches/kernel/')
    return project / name


def required_file(project, name):
    path = input_path(project, name)
    if path.is_symlink() or not path.is_file():
        raise ValueError('Missing/nonregular declared component input: ' + name)
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
    lock = json.loads((project / 'source-lock.json').read_text())
    names = (*COMMON_FILES, *FILES[stage])
    files = {name: required_file(project, name) for name in names}
    # Kernel patches are applied in sorted filename order. Preserve paths/order,
    # including additions/deletions; a missing declared tree is an error.
    patches = []
    if stage == 'kernel':
        root = input_path(project, 'patches/kernel/')
        if root.is_symlink() or not root.is_dir():
            raise ValueError('Missing declared kernel patch tree')
        for path in sorted(root.rglob('*')):
            if path.is_symlink() or not (path.is_file() or path.is_dir()):
                raise ValueError('Nonregular kernel patch input')
            if path.is_file():
                name = 'patches/kernel/' + str(path.relative_to(root))
                patches.append({'path': name, **required_file(project, name)})
    external = json.loads(input_path(project, 'containers/native/external-sources.lock.json').read_text())
    catalog = json.loads(input_path(project, 'containers/native/packages.json').read_text())
    jobs = select_jobs(catalog['jobs'], stage)
    if stage in {'native', 'kernel'} and not jobs:
        raise ValueError('Missing selected component jobs')
    selected = {job['name'] for job in jobs}
    if len(selected) != len(jobs) or any(dep not in selected for job in jobs for dep in job.get('requires', [])):
        raise ValueError('Incomplete component job graph')
    keys = ('stream', 'container_bases.' + architecture, 'debian_snapshot', 'debian_security_snapshot', *LOCK_VALUES[stage])
    return {'schema_version': SCHEMA_VERSION, 'stage': stage, 'architecture': architecture,
            'toolchain': toolchain_identity(project, architecture),
            'source_values': {key: selected_value(lock, key) for key in keys},
            'external': {key: selected_value(external, key) for key in EXTERNAL[stage]},
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
        from .staged import build_scope
        print('key=' + outer_key(build_scope(args.project, args.stage, args.architecture, compiler_fingerprint(), {})))


if __name__ == '__main__':
    main()
