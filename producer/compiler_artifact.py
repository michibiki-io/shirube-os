"""Same-run, immutable native producer image handoff; never rebuild on load."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import tarfile

from .sources import sha256
from .stage_artifacts import digest

# Every Dockerfile COPY input, plus the recipe and build-context exclusions.
INPUTS = ('.dockerignore', 'containers/native', 'producer', 'guest', 'patches/kernel',
          'patches/vyos', 'patches/nmap', 'LICENSE', 'NOTICE', 'source-lock.json',
          'license-audit.json', 'shirube-os-logo.svg', 'LICENSES', 'third_party/geoip')
BASES = {
    'amd64': 'debian@sha256:c67e92c8099ba22853ab4cfda7cfb494d253d6012ca82dee98ba8976772af018',
    'arm64': 'debian@sha256:ec8534cda2cb30f29f44f23e135297af7a1496f2990fd8e4e84aa36b081a757a',
}


def identity(commit, architecture, run_id):
    if (not re.fullmatch('[0-9a-f]{40}', commit) or architecture not in BASES
            or not re.fullmatch('[1-9][0-9]*', run_id)):
        raise ValueError('Exact commit, native architecture and same-run ID required')
    if (subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip() != commit
            or subprocess.check_output(['git', 'status', '--porcelain'], text=True).strip()):
        raise ValueError('Exact clean checkout required')
    names = subprocess.check_output(['git', 'ls-files', '-z', '--', *INPUTS]).decode().split('\0')
    files = {name: sha256(Path(name)) for name in names if name}
    return dict(schema_version=1, source_commit=commit, architecture=architecture,
                os='linux', run_id=run_id,
                inputs_id=digest({'base': BASES[architecture], 'files': files}))


def inspect(image, architecture):
    if not re.fullmatch('sha256:[0-9a-f]{64}', image):
        raise ValueError('Immutable image ID required')
    records = json.loads(subprocess.check_output(['docker', 'image', 'inspect', image]))
    if (len(records) != 1 or records[0]['Id'] != image
            or records[0]['Architecture'] != architecture or records[0]['Os'] != 'linux'):
        raise ValueError('Loaded compiler image identity/platform differs')


def archive_identity(archive, image, architecture):
    """Check the single docker-save config before asking Docker to load anything."""
    with tarfile.open(archive, 'r:') as source:
        members = source.getmembers()
        names = [m.name for m in members]
        if len(names) != len(set(names)) or any(
                m.name.startswith('/') or '..' in Path(m.name).parts
                or not (m.isfile() or m.isdir()) for m in members):
            raise ValueError('Unsafe/duplicate compiler archive member')
        def read_json(name):
            member = source.getmember(name)
            if not member.isfile() or member.size > 1024 * 1024:
                raise ValueError('Invalid compiler archive metadata')
            return source.extractfile(member).read()
        manifest = json.loads(read_json('manifest.json'))
        if not isinstance(manifest, list) or len(manifest) != 1:
            raise ValueError('Exactly one compiler image required')
        config = read_json(manifest[0]['Config'])
        record = json.loads(config)
        if ('sha256:' + hashlib.sha256(config).hexdigest() != image
                or record.get('architecture') != architecture or record.get('os') != 'linux'):
            raise ValueError('Archived compiler image identity/platform differs')


def export(directory, expected, image):
    inspect(image, expected['architecture'])
    directory.mkdir(parents=True, exist_ok=False)
    archive = directory / 'compiler.tar'
    subprocess.run(['docker', 'image', 'save', '--output', str(archive), image], check=True)
    archive_identity(archive, image, expected['architecture'])
    manifest = {**expected, 'image_id': image, 'archive_bytes': archive.stat().st_size,
                'archive_sha256': sha256(archive)}
    (directory / 'compiler.json').write_text(json.dumps(manifest, sort_keys=True) + '\n')


def load(directory, expected, *, discard_archive=False):
    if (directory.is_symlink() or not directory.is_dir()
            or {p.name for p in directory.iterdir()} != {'compiler.tar', 'compiler.json'}
            or any(p.is_symlink() or not p.is_file() for p in directory.iterdir())):
        raise ValueError('Missing/extra/nonregular compiler artifact inputs')
    manifest_path = directory / 'compiler.json'
    if manifest_path.stat().st_size > 16384:
        raise ValueError('Oversized compiler manifest')
    manifest = json.loads(manifest_path.read_text())
    if (not isinstance(manifest, dict)
            or set(manifest) != set(expected) | {'image_id', 'archive_bytes', 'archive_sha256'}
            or any(manifest.get(k) != v for k, v in expected.items())
            or not isinstance(manifest['image_id'], str)
            or not re.fullmatch('sha256:[0-9a-f]{64}', manifest['image_id'])):
        raise ValueError('Compiler manifest identity differs')
    archive = directory / 'compiler.tar'
    if (type(manifest['archive_bytes']) is not int or manifest['archive_bytes'] != archive.stat().st_size
            or manifest['archive_sha256'] != sha256(archive)):
        raise ValueError('Compiler archive hash/size differs')
    archive_identity(archive, manifest['image_id'], expected['architecture'])
    subprocess.run(['docker', 'image', 'load', '--input', str(archive)], check=True, stdout=sys.stderr)
    inspect(manifest['image_id'], expected['architecture'])
    if discard_archive:
        archive.unlink()
    return manifest['image_id']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('export', 'load'))
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--commit', required=True)
    parser.add_argument('--architecture', required=True)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--image')
    parser.add_argument('--discard-archive', action='store_true',
                        help='Retire the task-owned downloaded tar after successful load/inspect')
    args = parser.parse_args()
    if args.discard_archive and args.operation != 'load':
        parser.error('--discard-archive requires load')
    expected = identity(args.commit, args.architecture, args.run_id)
    if args.operation == 'export':
        export(args.directory, expected, args.image or '')
    else:
        print('imageid=' + load(args.directory, expected, discard_archive=args.discard_archive))


if __name__ == '__main__':
    main()
