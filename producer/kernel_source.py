"""Independently authenticate the kernel.org source; Stream supplies its recipes."""
import base64
import hashlib
import json
import lzma
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tarfile
import tempfile

from .security_contract import SECRET
from .sources import download, extract, sha256


RETAINED = '.kernel-upstream'
KEY_FILE = 'release-publisher.gpg'
SOURCE_MEMBER_PREFIX = 'kernel.org/'
VERIFICATION = 'verification.json'
BUILD_RECEIPTS = ('kernel.config', 'recipe-checksums.txt', 'kernel-tool-patches.sha256',
                  'kernel-patch-state.txt')
SHA256 = re.compile('[0-9a-f]{64}')
VERSION = re.compile(r'6\.6\.[0-9]+')
DIGEST_KEYS = ('sha256', 'signature_sha256', 'key_sha256')
STREAM_KEYS = ('archive_sha256', 'nested_member', 'nested_sha256')
NOTICE_PATHS = {'COPYING', 'LICENSES/exceptions/Linux-syscall-note'}
PUBLIC_KERNEL_FIXTURES = {
    ('08ca57ee614714958ffc6aa6451e34fb96fc398001e7e16ff7fc6dbfccb94688',
     'linux-6.6.163/tools/testing/selftests/sgx/sign_key.pem'):
        '1f6566d02247dc09d4dcb542ee2399522ceebf9b435ee00fe2053aa5582541e2',
}


def validate(lock):
    if not isinstance(lock, dict) or not isinstance(lock.get('version'), str):
        raise ValueError('A complete kernel.org source lock is required')
    version = lock['version']
    root = 'linux-' + version
    base = 'https://cdn.kernel.org/pub/linux/kernel/v6.x/' + root
    if (not VERSION.fullmatch(version) or lock['source_root'] != root
            or lock['kernel_release'] != version + '-vyos'
            or lock['package_version'] != version + '-1'
            or lock['url'] != base + '.tar.xz'
            or lock['signature_url'] != base + '.tar.sign'
            or lock['key_authority'] != 'https://www.kernel.org/signature.html'
            or not re.fullmatch('[0-9A-F]{40}', lock['signer_fingerprint'])
            or any(not SHA256.fullmatch(lock[name]) for name in DIGEST_KEYS)
            or set(lock['notices']) != NOTICE_PATHS
            or any(not SHA256.fullmatch(value) for value in lock['notices'].values())):
        raise ValueError('Incomplete or inconsistent kernel.org source lock')


def source_paths(jobs, source_lock):
    """Never select the kernel tree from the Stream archive."""
    stream_roots, kernel = {'vyos-build'}, None
    for job in jobs:
        if job['name'] != 'kernel':
            if job.get('source_origin') == 'kernel.org':
                raise ValueError('Only the kernel job can select kernel.org source')
            stream_roots.add(job['source'].split('/')[0])
            continue
        kernel = source_lock.get('kernel')
        if kernel is None:
            raise ValueError('Kernel source lock is required')
        validate(kernel)
        if job.get('source_origin') != 'kernel.org' or job['source'] != kernel['source_root']:
            raise ValueError('Kernel catalog and pinned kernel.org source differ')
    return stream_roots, kernel


def _stream_identity(stream):
    values = {name: stream[name] for name in STREAM_KEYS}
    if (not SHA256.fullmatch(values['archive_sha256'])
            or not SHA256.fullmatch(values['nested_sha256'])
            or not re.fullmatch(r'vyos-[0-9.]+-sources/vyos-tarballs/vyos_tarballs\.tar\.gz',
                                values['nested_member'])):
        raise ValueError('Missing authenticated Stream recipe identity')
    return values


def provenance(lock, stream):
    validate(lock)
    stream = _stream_identity(stream)
    return {
        'kernel_org': {
            **{key: lock[key] for key in (
                'version', 'source_root', 'kernel_release', 'package_version', 'url', 'sha256',
                'signature_url', 'signature_sha256', 'signer_fingerprint', 'key_authority',
                'key_sha256')},
            'notice_sha256': lock['notices'],
            'signature': 'PASS',
        },
        'stream_archive_sha256': stream['archive_sha256'],
        'stream_nested_member': stream['nested_member'],
        'stream_nested_sha256': stream['nested_sha256'],
    }


def _verify_version(root, expected):
    """Read assignments as data before executing anything in the source tree."""
    makefile = root / 'Makefile'
    if root.is_symlink() or makefile.is_symlink() or not makefile.is_file():
        raise ValueError('Pinned kernel release root/Makefile is missing')
    text = makefile.read_text()
    values = {}
    for key in ('VERSION', 'PATCHLEVEL', 'SUBLEVEL', 'EXTRAVERSION'):
        items = re.findall(r'^' + key + r'[ \t]*=[ \t]*([^\r\n]*?)[ \t]*$', text, re.M)
        if len(items) != 1:
            raise ValueError('Ambiguous kernel version assignments')
        values[key] = items[0]
    if ('.'.join(values[k] for k in ('VERSION', 'PATCHLEVEL', 'SUBLEVEL')) != expected
            or values['EXTRAVERSION']):
        raise ValueError('Kernel Makefile version does not match the pinned source')


def retained_paths(lock, root):
    return {
        'sha256': root / (lock['source_root'] + '.tar.xz'),
        'signature_sha256': root / (lock['source_root'] + '.tar.sign'),
        'key_sha256': root / KEY_FILE,
    }


def verify_retained(lock, root):
    paths = retained_paths(lock, root)
    if root.is_symlink() or any(not path.is_file() or path.is_symlink()
                                 or sha256(path) != lock[key] for key, path in paths.items()):
        raise ValueError('Kernel.org retained source, signature or key checksum differs')
    return paths


def verify_signature(paths, scratch, fingerprint):
    home = scratch / 'gnupg'
    home.mkdir(mode=0o700)
    # Stream signed bytes to the isolated verifier to avoid a second, large
    # on-disk kernel tar. Extract in a separate pass only after it succeeds.
    command = ['gpgv', '--homedir', str(home), '--keyring', str(paths['key_sha256'].resolve()),
               '--status-fd', '1', str(paths['signature_sha256']), '-']
    with tempfile.TemporaryFile() as status:
        with (lzma.open(paths['sha256'], 'rb') as compressed,
              subprocess.Popen(command, stdin=subprocess.PIPE, stdout=status,
                               stderr=subprocess.DEVNULL) as verifier):
            shutil.copyfileobj(compressed, verifier.stdin)
            verifier.stdin.close()
            if verifier.wait() != 0:
                raise ValueError('Kernel.org detached release signature failed')
        status.seek(0)
        valid = [line.split()[2:] for line in status.read().decode('ascii', errors='replace').splitlines()
                 if line.startswith('[GNUPG:] VALIDSIG ')]
    if (len(valid) != 1 or len(valid[0]) != 10
            or valid[0][0] != fingerprint or valid[0][-1] != fingerprint):
        raise ValueError('Kernel.org release signature does not match its pinned signing key')


def _scan_regular(name, source, source_sha256, output=None):
    digest = hashlib.sha256()
    key_match = False
    tail = b''
    while data := source.read(1024**2):
        digest.update(data)
        key_match |= SECRET.search(tail + data) is not None
        tail = (tail + data)[-1024:]
        if output is not None:
            output.write(data)
    if key_match and digest.hexdigest() != PUBLIC_KERNEL_FIXTURES.get((source_sha256, name)):
        raise ValueError('Private signing key in nested kernel.org source')


def scan_original(stream, source_root, source_sha256):
    """Equivalent per-file key audit for a lossless, nested kernel.org source."""
    seen = set()
    regular = 0
    with tarfile.open(fileobj=stream, mode='r|xz') as archive:
        for member in archive:
            name = PurePosixPath(member.name)
            if (not name.parts or name.parts[0] != source_root or '..' in name.parts
                    or '.git' in name.parts or name.as_posix() != member.name
                    or '\\' in member.name or member.name in seen
                    or not (member.isfile() or member.isdir() or member.issym() or member.islnk())):
                raise ValueError('Unsafe nested kernel.org source member')
            seen.add(member.name)
            if not member.isfile():
                continue
            regular += 1
            with archive.extractfile(member) as source:
                _scan_regular(member.name, source, source_sha256)
    if not regular:
        raise ValueError('Empty nested kernel.org source')
    return regular


def locked_source():
    project = Path(__file__).resolve().parents[1]
    lock = json.loads((project / 'source-lock.json').read_text()).get('kernel')
    validate(lock)
    return lock


def acquire(lock, sources, stream, *, verified_input=None):
    """Check pinned bytes + official detached signature before safe extraction."""
    receipt = provenance(lock, stream)
    sources = Path(sources)
    tree = sources / lock['source_root']
    retained = sources / RETAINED
    if not sources.is_dir() or sources.is_symlink() or any(p.exists() or p.is_symlink() for p in (tree, retained)):
        raise ValueError('Fresh kernel source destination required')
    with tempfile.TemporaryDirectory(prefix='.kernel-source-', dir=sources.parent) as scratch:
        scratch = Path(scratch)
        inputs = scratch / 'retained'
        inputs.mkdir()
        paths = retained_paths(lock, inputs)
        cached = [Path(verified_input) / paths[key].name for key in ('sha256', 'signature_sha256')] if verified_input else []
        if any(path.exists() or path.is_symlink() for path in cached):
            if any(not path.is_file() or path.is_symlink() for path in cached):
                raise ValueError('Incomplete or unsafe cached kernel.org source')
            for path, key in zip(cached, ('sha256', 'signature_sha256')):
                shutil.copyfile(path, paths[key])
        else:
            download(lock['url'], paths['sha256'])
            download(lock['signature_url'], paths['signature_sha256'])
        paths['key_sha256'].write_bytes(base64.b64decode(lock['key_base64'], validate=True))
        verify_retained(lock, inputs)

        # The publisher signs the uncompressed .tar, not the .tar.xz bytes.
        verify_signature(paths, scratch, lock['signer_fingerprint'])
        unpacked = scratch / 'unpacked'
        extract(paths['sha256'], unpacked, audit=lambda name, data, output:
                _scan_regular(name, data, lock['sha256'], output))
        root = unpacked / lock['source_root']
        if list(unpacked.iterdir()) != [root]:
            raise ValueError('Unexpected root in pinned kernel.org source')
        _verify_version(root, lock['version'])
        # Expose the exact principal legal notices separately; the complete
        # upstream LICENSES hierarchy and every file header stay in the tarball.
        for name, digest in lock['notices'].items():
            notice = root / name
            if not notice.is_file() or notice.is_symlink() or sha256(notice) != digest:
                raise ValueError('Pinned kernel.org legal notice is missing')
            retained_notice = inputs / 'notices' / name
            retained_notice.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(notice, retained_notice)
        (inputs / VERIFICATION).write_text(json.dumps(receipt, indent=2, sort_keys=True) + '\n')
        # Expose neither unauthenticated nor wrong-version content to a recipe.
        inputs.rename(retained)
        root.rename(tree)
    return receipt


def build_environment(lock):
    return {
        'SHIRUBE_KERNEL_VERSION': lock['version'],
        'SHIRUBE_KERNEL_RELEASE': lock['kernel_release'],
        'SHIRUBE_KERNEL_PACKAGE_VERSION': lock['package_version'],
    }


def copy_receipts(output, exported):
    digests = {}
    for name in BUILD_RECEIPTS:
        path = output / name
        if not path.is_file() or path.is_symlink() or not path.stat().st_size:
            raise ValueError('Required kernel build receipt is missing: ' + name)
        shutil.copyfile(path, exported / name)
        digests[name] = sha256(exported / name)
    return digests


def compiler_source_identity():
    # Used from the kernel-only cache namespace, never by the shared library
    # toolchain. The host still authenticates the seed writer independently.
    lock = locked_source()
    return {key: lock[key] for key in ('version', 'sha256', 'signature_sha256',
                                      'signer_fingerprint', 'key_sha256')}


def _regular(path):
    if path.is_symlink() or not path.is_file():
        raise ValueError('Missing or unsafe retained kernel evidence: ' + path.name)
    return path


def corresponding_inputs(lock, stream_lock, retained, package_output, source_name, source_version):
    """Recover exact Linux, Stream and patch identities without permission fallback."""
    validate(lock)
    if source_name != 'linux-upstream' or source_version != lock['package_version']:
        raise ValueError('Installed Linux source identity does not match the kernel build')
    receipt_path = _regular(retained / VERIFICATION)
    receipt = json.loads(receipt_path.read_text())
    if receipt != provenance(lock, {
            'archive_sha256': stream_lock['sha256'],
            'nested_member': f"vyos-{stream_lock['version']}-sources/vyos-tarballs/vyos_tarballs.tar.gz",
            'nested_sha256': receipt['stream_nested_sha256'],
    }):
        raise ValueError('Linux/Stream source verification receipt differs')
    manifest_path = _regular(package_output / 'manifest.json')
    manifest = json.loads(manifest_path.read_text())
    if (manifest['status'] != 'PASS' or manifest['source_sha256'] != lock['sha256']
            or manifest['kernel_input'] != receipt):
        raise ValueError('Linux source verification differs from the package build')
    receipts = {name: _regular(package_output / name) for name in BUILD_RECEIPTS}
    if manifest['kernel_build_receipts'] != {name: sha256(path) for name, path in receipts.items()}:
        raise ValueError('Linux build recipe/config/patch evidence differs')
    paths = verify_retained(lock, retained)
    members = [(paths['sha256'], SOURCE_MEMBER_PREFIX + paths['sha256'].name, 'sources')]
    for key in ('signature_sha256', 'key_sha256'):
        members.append((paths[key], SOURCE_MEMBER_PREFIX + paths[key].name, 'build_inputs'))
    for name, digest in lock['notices'].items():
        notice = _regular(retained / 'notices' / name)
        if sha256(notice) != digest:
            raise ValueError('Retained kernel.org legal notice checksum differs')
        members.append((notice, SOURCE_MEMBER_PREFIX + 'notices/' + name, 'license_files'))
    members.extend((path, 'native-build/' + name, 'build_inputs') for name, path in receipts.items())
    members.extend([(receipt_path, SOURCE_MEMBER_PREFIX + VERIFICATION, 'build_inputs'),
                    (manifest_path, 'native-build/manifest.json', 'build_inputs')])
    return members, receipt
