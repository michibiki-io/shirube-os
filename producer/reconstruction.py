"""Restore the complete hash-bound source set without certifying a release."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tarfile
from .release import ARCHES, LIMIT, filename, verify_source_integrity
from .sources import sha256


def restore(root, destination, architecture, expected_commit):
    root, destination = Path(root), Path(destination)
    manifest_bytes = (root/'manifest.json').read_bytes()
    manifest_digest = hashlib.sha256(manifest_bytes).hexdigest()
    manifest = json.loads(manifest_bytes)
    if (architecture not in ARCHES or not re.fullmatch('[0-9a-f]{40}', expected_commit)
            or manifest.get('source_commit') != expected_commit
            or manifest.get('project') != 'Shirube OS'
            or manifest.get('provider') != 'michibiki-io'
            or manifest.get('manifest_kind') not in {'release', 'diagnostic-source-set'}):
        raise ValueError('Source restoration identity differs from the selected release')
    assets = [item for item in manifest['assets']
              if item.get('architecture') in {architecture, 'all'}
              and item.get('role') in {'source', 'licenses'}]
    names = set()
    for item in assets:
        name = filename(item['name']); path = root/name
        if (name in names or path.is_symlink() or not path.is_file()
                or not 0 < path.stat().st_size < LIMIT
                or path.stat().st_size != item.get('size')
                or sha256(path) != item.get('sha256')):
            raise ValueError('Missing, duplicate or differing source restoration input')
        names.add(name)
    inventories = [item for item in assets if item['role'] == 'licenses']
    if len(inventories) != 1:
        raise ValueError('One exact architecture component inventory is required')
    inventory = json.loads((root/inventories[0]['name']).read_text())
    if any(item.get('architecture') not in {architecture, 'all'}
           for item in inventory['shipped_packages']):
        raise ValueError('Source inventory belongs to another architecture')
    members = verify_source_integrity(root, inventory, assets)
    by_archive = {}
    for key in members:by_archive.setdefault(key[0], set()).add(key)
    sources = [item for item in assets if item['role'] == 'source']
    if not sources:raise ValueError('No corresponding-source archives')
    # mkdir is outside cleanup: a preexisting user directory is never removed.
    destination.mkdir(mode=0o700, parents=False, exist_ok=False)
    restored = 0
    try:
        for item in sources:
            path = root/item['name']
            if sha256(path) != item['sha256']:
                raise ValueError('Source archive changed before restoration')
            target = destination/item['name']; target.mkdir()
            observed = set()
            with tarfile.open(path) as archive:
                for member in archive:
                    if member.isdir():continue
                    key = (item['name'], member.name)
                    # Recheck after reopening: integrity verification never
                    # authorizes a changed archive, alias or duplicate member.
                    if (not member.isfile() or key not in members or key in observed
                            or Path(member.name).is_absolute()
                            or '..' in Path(member.name).parts
                            or '.git' in Path(member.name).parts
                            or str(Path(member.name)) != member.name or '\\' in member.name):
                        raise ValueError('Unsafe or changed source restoration member')
                    observed.add(key)
                    output = target/member.name; output.parent.mkdir(parents=True, exist_ok=True)
                    digest = hashlib.sha256()
                    with archive.extractfile(member) as source, output.open('xb') as stream:
                        while chunk := source.read(1024**2):
                            digest.update(chunk);stream.write(chunk)
                    if digest.hexdigest() != members[key]:
                        raise ValueError('Restored source member differs')
                    output.chmod(0o755 if member.mode & 0o111 else 0o644)
                    os.utime(output, (member.mtime, member.mtime))
                    restored += 1
            if observed != by_archive.get(item['name'], set()):
                raise ValueError('Restored source archive is incomplete')
        if (sha256(root/'manifest.json') != manifest_digest
                or sha256(root/inventories[0]['name']) != inventories[0]['sha256']):
            raise ValueError('Source restoration metadata changed')
        proof = {'kind':'complete-source-set-restoration', 'status':'PASS_SOURCE_SET_ONLY',
                 'architecture':architecture, 'source_commit':expected_commit,
                 'manifest_sha256':manifest_digest,
                 'inventory_sha256':inventories[0]['sha256'],
                 'source_archives':len(sources), 'restored_regular_members':restored,
                 'shipped_packages':len(inventory['shipped_packages']),
                 'archive_byte_integrity':'PASS', 'all_source_license_recipe_references':'PASS',
                 'binary_reconstruction':'NOT_RUN', 'license_clearance':'NOT_ESTABLISHED_BY_RESTORATION',
                 'release_clearance':'NOT_CLEARED', 'oci':'NOT_RUN'}
        (destination/'restoration.json').write_text(json.dumps(proof, indent=2)+'\n')
        return proof
    except BaseException:
        shutil.rmtree(destination)
        raise
