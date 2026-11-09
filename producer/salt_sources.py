"""Retain exact Salt SDK dependency sources; this never grants license clearance."""
from email.parser import BytesParser
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import tarfile
import zipfile
import tomllib
import subprocess

from .bundles import bundle
from .sanitize import path_in_root
from .sources import download, sha256


def seed_sdk(architecture, data_directory, lock_path, cache=None):
    lock = json.loads(Path(lock_path).read_text())
    triplets = {'amd64': 'x86_64-linux-gnu', 'arm64': 'aarch64-linux-gnu'}
    if architecture not in triplets:
        raise ValueError('Unsupported Salt SDK architecture')
    name = lock['python'] + '-' + triplets[architecture] + '.tar.xz'
    selected = [item for item in lock['sdk_assets'] if item['name'] == name]
    if len(selected) != 1:
        raise ValueError('Exact architecture-specific Salt SDK input missing')
    record = selected[0]
    expected_url = 'https://github.com/saltstack/relenv/releases/download/v' + lock['relenv'] + '/' + name
    if (record['url'] != expected_url or not re.fullmatch('sha256:[0-9a-f]{64}', record['digest'])
            or type(record['size']) is not int or record['size'] <= 0):
        raise ValueError('Invalid fixed Salt SDK asset declaration')
    directory = Path(data_directory) / 'build'
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / name
    if destination.is_symlink():
        raise ValueError('Salt SDK input must be a regular private-staging file')
    if not destination.exists():
        original=Path(cache)/name if cache else None
        if original is not None and original.is_file() and not original.is_symlink():
            shutil.copyfile(original,destination)
        else:download(record['url'], destination)
    if (not destination.is_file() or destination.stat().st_size != record['size']
            or sha256(destination) != record['digest'][7:]):
        raise ValueError('Salt SDK asset hash/size mismatch')
    return destination


def pin_build(source, architecture, lock_path):
    """Replace only unverified SDK acquisition, preserving upstream features."""
    if architecture not in {'amd64', 'arm64'}:
        raise ValueError('Unsupported Salt SDK architecture')
    source = Path(source)
    lock = json.loads(Path(lock_path).read_text())
    rules = source / 'pkg/debian/rules'
    text = rules.read_text()
    original = '\tbuild/onedir/venv/bin/relenv fetch --python=$${SALT_PYTHON_VERSION}'
    if text.count(original) != 1:
        raise ValueError('Upstream Salt SDK acquisition recipe differs')
    replacement = ('\tPYTHONPATH=/src python3 -m producer.salt_sources seed-sdk --architecture '
                   + architecture + ' --data-directory "$${RELENV_DATA}"'
                   + ' --lock /src/native/salt-sources.lock.json --cache /public-inputs/salt-sdk')
    text = text.replace(original, replacement)
    text = text.replace('#!/usr/bin/make -f\n', '#!/usr/bin/make -f\n'
                        '# Modified by michibiki-io 2026-11-08: verify the fixed public SDK before use.\n', 1)
    rules.write_text(text)
    constraints = source / 'requirements/constraints.txt'
    pins = ['# Shirube OS: fixed runtime SDK inputs; upstream component versions retained.']
    # The upstream SDK package requirements already pin every other runtime
    # distribution. CI/build tools intentionally have a different dependency
    # environment (for example certifi), so do not constrain it to SDK versions.
    pins += [name + '==' + version for name, version in sorted(lock['installed_distributions'].items())
             if name in {'pip', 'relenv', 'setuptools', 'wheel'}]
    pins.append('ppbt==0.1.8')
    constraints.write_text(constraints.read_text() + '\n' + '\n'.join(pins) + '\n')


def normalized(name):
    return re.sub('[-_.]+', '-', name).lower()


def installed_distributions(rootfs):
    site = path_in_root(rootfs, 'opt/saltstack/salt/lib/python3.11/site-packages')
    result = {}
    for metadata in sorted(site.glob('*.dist-info/METADATA')):
        # Resolve according to guest semantics before opening installed files.
        metadata = path_in_root(rootfs, str(metadata.relative_to(Path(rootfs).resolve())))
        data = BytesParser().parsebytes(metadata.read_bytes())
        name, version = data['Name'], data['Version']
        if not name or not version or normalized(name) in result:
            raise ValueError('Missing or duplicate Salt SDK distribution identity')
        result[normalized(name)] = version
    if not result:
        raise ValueError('Salt SDK distribution metadata is missing')
    return result


def notice_members(archive, declarations):
    """Read only explicitly pinned regular legal files, without extraction."""
    expected = {item['member']: item for item in declarations}
    if not expected or len(expected) != len(declarations):
        raise ValueError('Missing or duplicate Salt source legal notice')
    for member in expected:
        path = PurePosixPath(member)
        if path.is_absolute() or '..' in path.parts or str(path) != member or '\\' in member:
            raise ValueError('Unsafe Salt source notice path')
    found = {}
    def accept(name, size, regular, open_member):
        if name not in expected:
            return
        declaration = expected[name]
        length = declaration.get('prefix_bytes', size)
        maximum = 32 * 1024**2 if 'prefix_bytes' in declaration else 4 * 1024**2
        if (name in found or not regular or not 0 < size <= maximum
                or type(length) is not int or not 0 < length <= min(size,4 * 1024**2)):
            raise ValueError('Invalid or duplicate Salt source legal file')
        # Large amalgamated sources can carry a short legal header. Verify the
        # entire pinned original member while retaining only its bounded prefix.
        digest=hashlib.sha256();chunks=[];total=0
        with open_member() as stream:
            while chunk:=stream.read(1024**2):
                digest.update(chunk)
                if total<length:chunks.append(chunk[:length-total])
                total+=len(chunk)
                if total>size:raise ValueError('Salt source legal file size differs')
        if total!=size or digest.hexdigest() != declaration['sha256']:
            raise ValueError('Salt source legal notice hash differs')
        content=b''.join(chunks)
        if 'prefix_bytes' in declaration:
            if hashlib.sha256(content).hexdigest() != declaration['notice_sha256']:
                raise ValueError('Embedded SDK legal notice hash differs')
        found[name] = content
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as source:
            for member in source.infolist():
                mode = member.external_attr >> 16
                regular = not member.is_dir() and mode & 0o170000 in {0, 0o100000}
                accept(member.filename, member.file_size, regular, lambda: source.open(member))
    else:
        with tarfile.open(archive, mode='r|*') as source:
            for member in source:
                accept(member.name, member.size, member.isfile(),
                       lambda: source.extractfile(member))
    if found.keys() != expected.keys():
        raise ValueError('Salt source legal notice is incomplete')
    return found


def validate_bootstrap_python_sources(rootfs, lock, work):
    """Bind source Python files supplied by the SDK asset to retained archives."""
    for declaration in lock.get('bootstrap_python_sources', []):
        selected = [r for r in lock['components'] if r['name'] == declaration['component']]
        if len(selected) != 1:
            raise ValueError('SDK bootstrap source identity is missing')
        record = selected[0]
        archive = Path(work) / (record['sha256'] + '.source')
        if sha256(archive) != record['sha256']:
            raise ValueError('SDK bootstrap source archive differs')
        prefix = declaration['source_prefix']
        prefix_path = PurePosixPath(prefix)
        if prefix_path.is_absolute() or '..' in prefix_path.parts or '\\' in prefix or not prefix.endswith('/'):
            raise ValueError('Unsafe SDK bootstrap source prefix')
        expected = {}
        with tarfile.open(archive, mode='r|*') as source:
            for member in source:
                if not member.name.startswith(prefix) or not member.name.endswith('.py'):
                    continue
                relative = PurePosixPath(member.name[len(prefix):])
                if (not member.isfile() or not 0 <= member.size <= 4 * 1024**2
                        or relative.is_absolute() or '..' in relative.parts
                        or '\\' in str(relative) or str(relative) in expected):
                    raise ValueError('Invalid SDK bootstrap Python source member')
                expected[str(relative)] = hashlib.sha256(source.extractfile(member).read()).hexdigest()
        package = path_in_root(rootfs, declaration['installed_prefix'])
        actual = {}
        for path in package.rglob('*'):
            if path.is_symlink():
                raise ValueError('Installed SDK bootstrap source contains a link')
            if path.is_file() and path.suffix == '.py':
                actual[str(path.relative_to(package))] = sha256(path)
        if not expected or actual != expected:
            raise ValueError('Installed SDK bootstrap Python source differs from retained input')


def validate_cargo_sources(lock,work):
    parent=lock.get('cargo_parent')
    if parent is None:return
    records=[r for r in lock['components'] if r['name']==parent['component']]
    if len(records)!=1 or records[0]['sha256']!=parent['source_sha256']:
        raise ValueError('Rust dependency parent source differs')
    archive=Path(work)/(parent['source_sha256']+'.source')
    found=[]
    with tarfile.open(archive,mode='r|*') as source:
        for member in source:
            if member.name!=parent['member']:continue
            if not member.isfile() or not 0<member.size<=4*1024**2:
                raise ValueError('Invalid SDK Cargo lock member')
            found.append(source.extractfile(member).read())
    if len(found)!=1 or hashlib.sha256(found[0]).hexdigest()!=parent['lock_sha256']:
        raise ValueError('SDK Cargo lock missing or differs')
    expected=set()
    for package in tomllib.loads(found[0].decode())['package']:
        if not package.get('source'):continue
        if package['source']!='registry+https://github.com/rust-lang/crates.io-index':
            raise ValueError('Unsupported non-registry SDK Rust source')
        expected.add(('cargo:'+package['name'],package['version'],package['checksum']))
    provided={(r['name'],r['version'],r['sha256']) for r in lock['components'] if r['name'].startswith('cargo:')}
    if not expected or provided!=expected:
        raise ValueError('SDK Rust source coverage differs from actual Cargo lock')


def retain(rootfs, destination, work, lock_path):
    """Bind the installed SDK inventory to fixed sources and full notices.

    The upstream Salt tree and packaging scripts are collected separately.
    Keeping this archive does not establish SDK reconstruction or permission
    to redistribute; the normal per-component audit remains mandatory.
    """
    lock_path = Path(lock_path)
    lock = json.loads(lock_path.read_text())
    if lock.get('schema_version') != 1:
        raise ValueError('Unsupported Salt dependency source lock')
    actual = installed_distributions(rootfs)
    if actual != lock['installed_distributions']:
        raise ValueError('Salt SDK distributions differ from pinned source inventory')
    compiler=lock.get('rust_compiler')
    if compiler is not None:
        version=subprocess.check_output(['rustc','--version'],text=True).strip()
        package=subprocess.check_output(['dpkg-query','-W','-f=${source:Package}\t${source:Version}',
                                         'libstd-rust-dev'],text=True).strip()
        if version!=compiler['version_output'] or package!=compiler['source_package']+'\t'+compiler['source_version']:
            raise ValueError('Actual Rust standard-library compiler/source differs from SDK lock')
    work = Path(work)
    work.mkdir(parents=True, exist_ok=False)
    inputs = [(lock_path, 'salt-sdk/source-lock.json', 'build_inputs')]
    children = []
    identities = set()
    for record in lock['components']:
        identity = (record['name'], record['version'])
        if identity in identities or not re.fullmatch('[a-zA-Z0-9][a-zA-Z0-9+_.:-]*', record['name']):
            raise ValueError('Invalid or duplicate Salt source component')
        identities.add(identity)
        if record['name'].startswith('pypi:') and actual.get(record['name'][5:]) != record['version']:
            raise ValueError('Salt Python source version differs from installed distribution')
        digest = record['sha256']
        if not re.fullmatch('[0-9a-f]{64}', digest):
            raise ValueError('Invalid Salt source digest')
        source = work / (digest + '.source')
        download(record['url'], source)
        if sha256(source) != digest:
            raise ValueError('Salt dependency source SHA256 mismatch')
        exported, exported_digest = source, digest
        if 'source_export' in record:
            component = (record['name'], record['version'])
            if component == ('pypi:charset-normalizer', '3.2.0'):
                from .charset_source import write_export
            elif component == ('salt-sdk:libxcrypt', '4.5.2'):
                from .libxcrypt_source import write_export
            else:
                raise ValueError('Unexpected scoped SDK source export')
            exported = work / (digest + '.export.tar.gz')
            exported_digest = write_export(source, exported, record['source_export'])
            if exported_digest != record['source_export']['sha256']:
                raise ValueError('Scoped SDK source export hash differs')
        member = 'salt-sdk/inputs/' + exported_digest + '.source'
        inputs.append((exported, member, 'sources'))
        source_members=[member]
        extra_archives=[]
        source_set=[exported_digest]
        for extra in record.get('extra_sources', []):
            extra_digest=extra['sha256']
            if not re.fullmatch('[0-9a-f]{64}',extra_digest) or extra_digest in source_set:
                raise ValueError('Invalid or duplicate supplemental SDK source')
            target=work/(extra_digest+'.source')
            download(extra['url'],target)
            if sha256(target)!=extra_digest:raise ValueError('Supplemental SDK source SHA256 mismatch')
            extra_member='salt-sdk/inputs/'+target.name
            inputs.append((target,extra_member,'sources'));source_members.append(extra_member)
            source_set.append(extra_digest)
            if extra.get('notices'):extra_archives.append((target,extra['notices']))
        identity=exported_digest
        if extra_archives or record.get('extra_sources'):
            identity=hashlib.sha256(json.dumps(source_set,separators=(',',':')).encode()).hexdigest()
        notice = work / (digest + '.copyright')
        with notice.open('xb') as output:
            output.write((record['name'] + ' ' + record['version'] + '\n').encode())
            for name, content in sorted(notice_members(source, record['notices']).items()):
                output.write(('\nOriginal notice: ' + name + '\n').encode() + content + b'\n')
            for extra,declarations in extra_archives:
                for name,content in sorted(notice_members(extra,declarations).items()):
                    output.write(('\nOriginal supplemental notice: '+name+'\n').encode()+content+b'\n')
            for legal in record.get('additional_notices', []):
                if not re.fullmatch('[0-9a-f]{64}', legal['sha256']):
                    raise ValueError('Invalid additional Salt legal notice digest')
                path = work / (legal['sha256'] + '.notice')
                download(legal['url'], path)
                if sha256(path) != legal['sha256'] or not 0 < path.stat().st_size <= 4 * 1024**2:
                    raise ValueError('Additional Salt legal notice missing or differs')
                output.write(('\nAdditional upstream license: ' + legal['url'] + '\n').encode()
                             + path.read_bytes() + b'\n')
        notice_member = 'salt-sdk/notices/' + notice.name
        inputs.append((notice, notice_member, 'license_files'))
        children.append({'name': record['name'], 'version': record['version'],
                         'source_identity': identity, 'source_member': member,
                         'source_sha256': exported_digest, 'notice': notice,
                         'notice_member': notice_member,'retained_members':source_members})
    expected = {'pypi:' + name for name in actual if name != 'salt'}
    provided = {item['name'] for item in children if item['name'].startswith('pypi:')}
    if expected != provided:
        raise ValueError('Salt dependency source coverage is incomplete')
    validate_cargo_sources(lock,work)
    validate_bootstrap_python_sources(rootfs,lock,work)
    records = bundle(inputs, destination, 1773859634)
    for child in children:
        wanted=set(child.pop('retained_members'))
        child['sources'] = [ref for ref in records['sources'] if ref['member'] in wanted]
        child['license_files'] = [ref for ref in records['license_files'] if ref['member'] == child['notice_member']]
    return records, children


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['seed-sdk'])
    parser.add_argument('--architecture', choices=['amd64', 'arm64'], required=True)
    parser.add_argument('--data-directory', required=True)
    parser.add_argument('--lock', required=True)
    parser.add_argument('--cache')
    args = parser.parse_args()
    seed_sdk(args.architecture, args.data_directory, args.lock, args.cache)
