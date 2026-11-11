"""Export pinned Go sources while excluding separately restricted site/artwork.

Compilation continues to verify the original module inputs. These exports are
preferred source deliveries, rather than replacement module-proxy ZIPs: their
Go h1 sums differ. Runtime Go code and dependency declarations remain unchanged.
"""
import hashlib
import json
from pathlib import Path, PurePosixPath
import stat
import zipfile

from .sources import sha256


POLICIES = {
    ('github.com/hashicorp/serf', 'v0.10.1'): {
        'upstream_sha256': '661b6ad5df497dcda0f581607b003e40646ef9f3ca09d12bdeec7cb3d16ad370',
        'exclude': ['website/'],
        'reason': 'The separately licensed website design is excluded; runtime sources remain unchanged.',
        'export_sha256': '5436476de809de43d50b0e89cb7546e5fac063c80e9fe8811a5c802bebb94e9f',
    },
    ('github.com/quic-go/quic-go', 'v0.64.0'): {
        'upstream_sha256': '0890cee71e7bf68691a3cd9072d99f2054419a1b76fa9621eba1626a91339dc1',
        'exclude': ['assets/'],
        'reason': 'Separately restricted branding assets are excluded; runtime sources remain unchanged.',
        'export_sha256': '629bb00d19fc6041a0d0a26a2606ef67723a2058c8d8163ae26dc25dd29c7b15',
    },
}


def _write_export(original, module, version, policy, destination):
    """Write one task-owned export, retaining every other source byte."""
    prefix = module + '@' + version + '/'
    retained, omitted = [], []
    seen = set()
    with zipfile.ZipFile(original) as source:
        for member in sorted(source.infolist(), key=lambda item: item.filename):
            name = member.filename
            path = PurePosixPath(name)
            if (not name.startswith(prefix) or path.is_absolute()
                    or '..' in path.parts or '\\' in name or name in seen
                    or str(path) != name.rstrip('/') or '\n' in name or '\r' in name
                    or stat.S_IFMT(member.external_attr >> 16) not in {0, stat.S_IFREG}
                    or member.file_size > 32 * 1024**2):
                raise ValueError('Unsafe Go source export member')
            seen.add(name)
            if member.is_dir():
                continue
            relative = name[len(prefix):]
            if relative == 'SHIRUBE-SOURCE-EXPORT.json':
                raise ValueError('Go source already contains an export marker')
            data = source.read(member)
            entry = {'path': relative, 'sha256': hashlib.sha256(data).hexdigest(),
                     'size': len(data),
                     'mode': 0o755 if (member.external_attr >> 16) & 0o111 else 0o644}
            if any(relative.startswith(item) for item in policy['exclude']):
                if (relative.endswith(('.go', '.c', '.h', '.s', '.S'))
                        or path.name in {'go.mod', 'go.sum'}):
                    raise ValueError('Go export must not remove runtime source or declarations')
                omitted.append(entry)
            else:
                retained.append((name, data, entry))
    if not omitted or not retained:
        raise ValueError('Go source export scope differs from its pinned policy')
    manifest = {
        'schema_version': 1, 'module': module, 'version': version,
        'modified_by': 'michibiki-io', 'modified_on': '2026-11-10',
        'original_archive_sha256': policy['upstream_sha256'],
        'reason': policy['reason'], 'excluded_prefixes': policy['exclude'],
        'omitted_members': omitted,
        'retained_member_count': len(retained),
        'retained_member_inventory_sha256': hashlib.sha256(json.dumps(
            [entry for _, _, entry in retained], sort_keys=True,
            separators=(',', ':')).encode()).hexdigest(),
        'reconstruction': 'Use the retained source tree directly or as vendored source. '
            'This is not a module-proxy ZIP with the original Go h1 sum. '
            'The original public input hash and all omissions are recorded; '
            'compilation verifies original module inputs separately.',
    }
    content = (json.dumps(manifest, sort_keys=True, indent=2) + '\n').encode()
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    target = destination / 'source.zip'
    with zipfile.ZipFile(target, 'x', compression=zipfile.ZIP_STORED) as output:
        for name, data, mode in [(name, data, metadata['mode'])
                                 for name, data, metadata in retained] + [
                (prefix + 'SHIRUBE-SOURCE-EXPORT.json', content, 0o644)]:
            entry = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            entry.create_system = 3
            entry.external_attr = (stat.S_IFREG | mode) << 16
            output.writestr(entry, data)
    notice = destination / 'source-export.json'
    notice.write_bytes(content)
    return {'archive': target, 'manifest': notice, 'details': manifest}


def export_source(original, module, version, destination):
    """Only the two independently inspected exact inputs have export policies."""
    policy = POLICIES.get((module, version))
    if policy is None:
        return None
    original = Path(original)
    if original.is_symlink() or not original.is_file() or sha256(original) != policy['upstream_sha256']:
        raise ValueError('Restricted Go source input differs from its pinned policy')
    result = _write_export(original, module, version, policy, destination)
    if sha256(result['archive']) != policy['export_sha256']:
        raise ValueError('Preferred Go source export differs from the verified output')
    return result


def replace_delivery_inputs(inputs, dependencies, work):
    """Replace only delivery copies after original binary/input verification.

    The caller's installed inventory and private compilation inputs stay bound
    to the original archives. Each delivered source leaf instead records its
    exact preferred archive, original identity and mandatory omission manifest.
    """
    from .go_modules import module_hash
    for dependency in dependencies:
        name = dependency['name']
        if not name.startswith('go:'):
            continue
        module, version = name[3:], dependency['version']
        if (module, version) not in POLICIES:
            continue
        member = dependency.get('source_member')
        if not member:
            raise ValueError('Scoped Go export requires its complete original module archive')
        matches = [(index, path) for index, (path, relative, _) in enumerate(inputs)
                   if relative == member]
        if len(matches) != 1:
            raise ValueError('Go export original delivery input is missing or duplicated')
        index, original = matches[0]
        if sha256(original) != dependency['source_sha256']:
            raise ValueError('Go export differs from the original dependency binding')
        original_h1 = module_hash(original, module, version)
        identity = dependency['source_identity']
        if identity.startswith('h1:') and identity != original_h1:
            raise ValueError('Go export original module sum differs from the shipped binary')
        directory = Path(work) / hashlib.sha256((module + '@' + version).encode()).hexdigest()
        result = export_source(original, module, version, directory)
        digest = sha256(result['archive'])
        preferred_member = member.removesuffix('.zip') + '.preferred-source.zip'
        manifest_member = preferred_member + '.source-export.json'
        manifest_digest = sha256(result['manifest'])
        inputs[index] = (result['archive'], preferred_member, inputs[index][2])
        inputs.append((result['manifest'], manifest_member, 'build_inputs'))
        dependency.update(source_identity=digest, source_sha256=digest,
                          source_member=preferred_member,
                          source_export={
                              'original_source_identity': identity,
                              'original_archive_member': member,
                              'original_archive_sha256': POLICIES[module, version]['upstream_sha256'],
                              'original_module_h1': original_h1,
                              'preferred_archive_sha256': digest,
                              'manifest_member': manifest_member,
                              'manifest_sha256': manifest_digest,
                              'excluded_prefixes': POLICIES[module, version]['exclude'],
                          })
        dependency.setdefault('build_input_members', []).append(
            {'member': manifest_member, 'sha256': manifest_digest})
