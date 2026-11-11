"""Strict regular-file transfers for native build stages, never compiler layers.

Content identities omit release attribution; transfer receipts bind that reusable
content to the current resolved commit/version. A cache is only an input hint.
"""
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import tarfile

from .sources import sha256


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def member_name(value):
    p = PurePosixPath(value)
    if (not value or p.is_absolute() or '..' in p.parts or str(p) != value
            or '\\' in value or any(ord(c) < 32 for c in value)):
        raise ValueError('Unsafe stage member')
    return value


def scope(stage, architecture, inputs, compiler, dependencies):
    if (not re.fullmatch('[a-z][a-z0-9-]*', stage) or architecture not in {'amd64', 'arm64'}
            or not re.fullmatch('sha256:[0-9a-f]{64}', compiler) or not inputs
            or any(not re.fullmatch('[0-9a-f]{64}', v) for v in inputs.values())
            or any(not re.fullmatch('[0-9a-f]{64}', v) for v in dependencies.values())):
        raise ValueError('Incomplete stage content identity')
    record = dict(schema_version=1, stage=stage, architecture=architecture,
                  inputs=inputs, compiler=compiler, dependencies=dependencies)
    return {**record, 'input_id': digest(record)}


def attribution(commit, version):
    from .policy import version_parts
    if not isinstance(commit,str) or not re.fullmatch('[0-9a-f]{40}', commit):
        raise ValueError('Exact stage commit required')
    version_parts(version)
    return {'source_commit': commit, 'version': version}


def inventory(root):
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        raise ValueError('Regular stage directory required')
    result = []
    for path in sorted(root.rglob('*')):
        name = member_name(path.relative_to(root).as_posix())
        if path.is_symlink() or not (path.is_file() or path.is_dir()):
            raise ValueError('Links/special files cannot enter stage transfers')
        if path.is_dir():
            continue
        from .source_fixtures import KNOWN
        public_fixture = name.startswith('material/') and any(
            ((member.startswith('source/') and name.endswith('/'+member.removeprefix('source/')))
             or (member.startswith('netbird/') and name=='material/guest-package/netbird-correspondence/'+member.removeprefix('netbird/')))
            and sha256(path)==value
            for declarations in KNOWN.values() for member,value in declarations.items())
        if (path.name in {'signing_key.pem', 'machine-id', 'random-seed'}
                or path.suffix in {'.o', '.ko'} or '.git' in path.parts):
            raise ValueError('Private identity or raw compiler state in stage transfer')
        # Reuse the release scanner: parser source may contain a delimiter,
        # but a private key's armored bytes require an exact public fixture.
        from .release import SECRET
        with path.open('rb') as stream:
            tail = b''
            while data := stream.read(1024 * 1024):
                data = tail + data
                if not public_fixture and SECRET.search(data):
                    raise ValueError('Private key in stage transfer')
                tail = data[-1024:]
        result.append({'name': name, 'sha256': sha256(path), 'size': path.stat().st_size,
                       'executable': bool(path.stat().st_mode & 0o111)})
    if not result:
        raise ValueError('Empty stage transfer')
    return result


def pack(root, archive, expected, commit, version, *, content_source_commit=None):
    root, archive = Path(root), Path(archive)
    records = inventory(root)
    built = attribution(content_source_commit or commit,version)['source_commit']
    receipt = {**attribution(commit, version), 'scope': expected, 'files': records,
               'payload_id': digest(records), 'content_source_commit':built}
    with tarfile.open(archive, 'x') as output:
        data = (json.dumps(receipt, sort_keys=True) + '\n').encode()
        import io
        header = tarfile.TarInfo('receipt.json'); header.size = len(data); header.mode = 0o644
        output.addfile(header, io.BytesIO(data))
        for record in records:
            header = tarfile.TarInfo('payload/' + record['name'])
            header.size = record['size']; header.mode = 0o755 if record['executable'] else 0o644
            with (root / record['name']).open('rb') as stream:
                output.addfile(header, stream)
    return {'sha256': sha256(archive), 'bytes': archive.stat().st_size, **receipt}


def unpack(archive, destination, expected, commit, version, archive_sha256):
    """Validate the entire archive before writing any member, including extras."""
    archive, destination = Path(archive), Path(destination)
    if (destination.exists() or destination.is_symlink()
            or archive.is_symlink() or sha256(archive) != archive_sha256):
        raise ValueError('Stage transfer hash or fresh destination differs')
    with tarfile.open(archive, 'r:') as source:
        members = source.getmembers()
        names = [member_name(m.name) for m in members]
        if len(names) != len(set(names)) or any(not m.isfile() for m in members):
            raise ValueError('Nonregular or duplicate stage archive member')
        if 'receipt.json' not in names or source.getmember('receipt.json').size > 64 * 1024**2:
            raise ValueError('Missing/oversized stage receipt')
        receipt = json.load(source.extractfile('receipt.json'))
        if not isinstance(receipt,dict) or set(receipt)!={'source_commit','version','scope','files','payload_id','content_source_commit'}:
            raise ValueError('Invalid stage receipt schema')
        if (receipt.get('scope') != expected
                or any(receipt.get(k) != v for k, v in attribution(commit, version).items())
                or receipt.get('payload_id') != digest(receipt.get('files')) or not receipt.get('files')):
            raise ValueError('Stage provenance differs')
        attribution(receipt.get('content_source_commit',''),version)
        files = receipt['files']
        if not isinstance(files,list) or any(
                not isinstance(r,dict) or set(r)!={'name','sha256','size','executable'}
                or not isinstance(r['name'],str) or not isinstance(r['sha256'],str)
                or not re.fullmatch('[0-9a-f]{64}',r['sha256'])
                or type(r['size']) is not int or r['size']<0 or type(r['executable']) is not bool
                for r in files):
            raise ValueError('Invalid stage file inventory schema')
        wanted = ['payload/' + member_name(r['name']) for r in files]
        if len(wanted) != len(set(wanted)) or set(names) != set(wanted) | {'receipt.json'}:
            raise ValueError('Missing/extra stage inventory members')
        for record, name in zip(files, wanted):
            member = source.getmember(name)
            h = hashlib.sha256()
            with source.extractfile(member) as stream:
                while block := stream.read(1024 * 1024): h.update(block)
            if member.size != record['size'] or h.hexdigest() != record['sha256']:
                raise ValueError('Stage payload differs')
        destination.mkdir(parents=True)
        try:
            for record, name in zip(files, wanted):
                target = destination / record['name']; target.parent.mkdir(parents=True, exist_ok=True)
                with source.extractfile(name) as stream, target.open('xb') as output:
                    import shutil
                    shutil.copyfileobj(stream, output)
                target.chmod(0o755 if record['executable'] else 0o644)
            if inventory(destination) != files:
                raise ValueError('Restored stage inventory differs')
        except BaseException:
            import shutil
            shutil.rmtree(destination)
            raise
    return receipt
