"""Bounded kernel C compiler results, separate from signed stage transfers.

Persistent inputs are admitted by the host's trusted Release artifact importer.
Checksums here detect damage, never establish writer authority. Only the dedicated
compiler-result store is exported; the key-owning build tree is never traversed.
"""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile

from .library_cache import compiler_fingerprint, tree_identity
from .sources import sha256
from .stage_artifacts import digest

LIMIT = 2 * 1024**3
FILE_LIMIT = 64 * 1024**2
COUNT_LIMIT = 50000
RESULT = re.compile(r'[0-9a-f]/[0-9a-f]/[A-Za-z0-9]+R')
STATE = Path('/build-private/kernel-compiler.json')
SCHEMA = 1


def require(condition):
    if not condition:
        raise ValueError('Kernel compiler cache boundary rejected')


def tools():
    result = {}
    for name in ('gcc', 'as', 'ld', 'ccache'):
        path = Path(shutil.which(name)).resolve()
        result[name] = sha256(path)
    # A driver hash alone does not identify its compiler/assembler/linker backend.
    for name in ('cc1', 'collect2', 'lto1', 'lto-wrapper', 'liblto_plugin.so'):
        path = Path(subprocess.check_output(['gcc', ('-print-file-name=' if name.endswith('.so') else '-print-prog-name=') + name], text=True).strip())
        require(path.is_file())
        result[name] = sha256(path.resolve())
    return digest({'programs': result, 'installed': compiler_fingerprint()})


def envelope(architecture, toolchain):
    require(architecture in ('amd64', 'arm64') and re.fullmatch('[0-9a-f]{64}', toolchain))
    return dict(schema_version=SCHEMA, architecture=architecture, toolchain=toolchain)


def restore(archive, destination, expected):
    """Validate every member before materialization; never import config/links."""
    archive, destination = Path(archive), Path(destination)
    require(not destination.exists() and not archive.is_symlink()
            and archive.is_file() and archive.stat().st_size <= LIMIT)
    with tarfile.open(archive, 'r:') as source:
        members = []
        total = 0
        for member in source:
            require(member.isfile() and len(members) < COUNT_LIMIT + 1
                    and 0 <= member.size <= FILE_LIMIT)
            total += member.size
            require(total <= LIMIT)
            members.append(member)
        names = [m.name for m in members]
        require(len(set(names)) == len(names) and names.count('manifest.json') == 1
                and source.getmember('manifest.json').size <= 8 * 1024**2)
        meta = json.load(source.extractfile('manifest.json'))
        require(isinstance(meta, dict) and set(meta) == {'scope', 'files'} and meta['scope'] == expected
                and isinstance(meta['files'], list) and len(meta['files']) <= COUNT_LIMIT)
        records = meta['files']
        require(all(isinstance(r, dict) and set(r) == {'name', 'size', 'sha256'}
                    and isinstance(r['name'], str) and RESULT.fullmatch(r['name'])
                    and type(r['size']) is int and 0 < r['size'] <= FILE_LIMIT
                    and re.fullmatch('[0-9a-f]{64}', r['sha256']) for r in records))
        require(sorted(names) == sorted(['manifest.json'] + [r['name'] for r in records]))
        for record in records:
            member = source.getmember(record['name'])
            require(member.size == record['size'])
            require(hashlib.sha256(source.extractfile(member).read()).hexdigest() == record['sha256'])
        destination.mkdir(mode=0o700, parents=True)
        try:
            for record in records:
                target = destination / record['name']
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open('xb') as output:
                    shutil.copyfileobj(source.extractfile(record['name']), output)
        except BaseException:
            shutil.rmtree(destination)
            raise
    return len(records)


def export(store, archive, expected):
    """Export only ccache preprocessor-mode result entries, never its settings."""
    store, archive = Path(store), Path(archive)
    require(store.is_dir() and not store.is_symlink() and not archive.exists())
    records = []
    total = 0
    for path in sorted(store.rglob('*')):
        require(not path.is_symlink() and (path.is_file() or path.is_dir()))
        if not path.is_file():
            continue
        name = path.relative_to(store).as_posix()
        if not RESULT.fullmatch(name):
            # ccache's administrative files are local only. Anything else is a
            # boundary violation, not silently included as a compiler result.
            require(path.name in {'stats', 'CACHEDIR.TAG', 'ccache.conf'} or name == 'tmp/.cleaned')
            continue
        size = path.stat().st_size
        require(0 < size <= FILE_LIMIT and len(records) < COUNT_LIMIT)
        total += size
        require(total <= LIMIT - 16 * 1024**2)
        records.append(dict(name=name, size=size, sha256=sha256(path)))
    meta = (json.dumps({'scope': expected, 'files': records}, sort_keys=True) + '\n').encode()
    require(len(meta) <= 8 * 1024**2)
    with tarfile.open(archive, 'x') as output:
        header = tarfile.TarInfo('manifest.json'); header.size = len(meta)
        output.addfile(header, io.BytesIO(meta))
        for record in records:
            header = tarfile.TarInfo(record['name']); header.size = record['size']
            with (store / record['name']).open('rb') as stream:
                output.addfile(header, stream)
    require(archive.stat().st_size <= LIMIT)
    return len(records)


def prepare(root, mount, architecture, *, state=STATE):
    root, mount, state = Path(root), Path(mount), Path(state)
    require(root.is_dir() and mount.is_dir() and not mount.is_symlink() and not state.exists())
    toolchain = tools()
    scope = envelope(architecture, toolchain)
    # Run after mrproper, old key removal, configuration and patches, before
    # Kbuild generates any key. Content-only identity; no timestamp relaxation.
    require(not (root / 'certs/signing_key.pem').exists())
    namespace = digest({'scope': scope, 'sources_config': tree_identity(root)})
    store = state.parent / 'kernel-ccache'
    require(not store.exists())
    status = 'MISS'
    seed = mount / 'seed.tar'
    if seed.exists() or seed.is_symlink():
        try:
            restore(seed, store, scope)
            status = 'RESTORED'  # This is not a compiler hit; see per-run stats.
        except (ValueError, OSError, KeyError, TypeError, tarfile.TarError, json.JSONDecodeError):
            status = 'MISS_INVALID'
    store.mkdir(mode=0o700, exist_ok=True)
    config = state.with_suffix('.conf')
    config.write_text(f'''cache_dir = {store}
max_size = 1GiB
compiler_check = content
direct_mode = false
depend_mode = false
sloppiness =
hard_link = false
file_clone = false
run_second_cpp = true
hash_dir = true
remote_storage =
namespace = {namespace}
''')
    data = dict(root=str(root.resolve()), store=str(store), config=str(config), scope=scope,
                status=status, namespace=namespace, mount=str(mount))
    state.write_text(json.dumps(data, sort_keys=True) + '\n')
    subprocess.run(['ccache', '--zero-stats'], env=environment(data), check=True)
    return data


def environment(data):
    # Never inherit sloppiness, compiler wrappers, external config, remote stores
    # or cached configuration. The run-private config is the sole authority.
    env = {k: v for k, v in os.environ.items() if not k.startswith('CCACHE_')}
    env['CCACHE_CONFIGPATH'] = data['config']
    return env


def eligible(args, root):
    """Only ordinary kernel C-to-object calls. Assembly/certs/modpost/link bypass."""
    if ('-c' not in args or '-o' not in args or '-x' in args or any(a.startswith(('@', '-fplugin', '-specs', '--specs', '-B', '-fprofile', '-wrapper')) for a in args)):
        return False
    sources = [Path(a) for a in args if a.endswith('.c')]
    if len(sources) != 1:
        return False
    source = sources[0].resolve()
    root = Path(root).resolve()
    if not source.is_relative_to(root) or not source.is_file():
        return False
    relative = source.relative_to(root)
    if relative.parts[0] in {'certs', 'scripts', 'tools'} or source.name.endswith('.mod.c'):
        return False
    try:
        output = Path(args[args.index('-o') + 1]).resolve()
    except IndexError:
        return False
    return output.is_relative_to(root) and output.suffix == '.o'


def compile_command(args, data):
    if eligible(args, data['root']):
        env = environment(data)
        # Preserve all options, even a -D/-I that currently has no preprocessor effect.
        env['CCACHE_NAMESPACE'] = digest({'source_namespace': data['namespace'], 'argv': args})
        return ['ccache', '/usr/bin/gcc', *args], env
    return ['/usr/bin/gcc', *args], environment(data)


def finish(*, state=STATE):
    if not Path(state).exists():
        return
    data = json.loads(Path(state).read_text())
    env = environment(data)
    subprocess.run(['ccache', '--cleanup'], env=env, check=True)
    stats = subprocess.check_output(['ccache', '--print-stats'], env=env, text=True)
    report = {'scope': data['scope'], 'namespace': data['namespace'], 'restore': data['status'],
              'statistics': dict(line.split() for line in stats.splitlines())}
    export(data['store'], Path(data['mount']) / 'export.tar', data['scope'])
    (Path(data['mount']) / 'metrics.json').write_text(json.dumps(report, sort_keys=True) + '\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--architecture', choices=('amd64', 'arm64'), required=True)
    args = parser.parse_args()
    mount = os.environ.get('SHIRUBE_KERNEL_COMPILE_CACHE')
    if mount:
        prepare(args.root, mount, args.architecture)


if __name__ == '__main__':
    main()
