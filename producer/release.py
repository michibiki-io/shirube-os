"""Fail-closed validation of a complete, coherent release directory."""
import json
from pathlib import Path
import re
import tarfile
from .sources import sha256
from .policy import version_parts,require_stream_version

ARCHES = {'amd64', 'arm64'}
CHECKS = {'build', 'architecture', 'module_signatures', 'branding', 'licenses',
          'corresponding_source', 'sanitization', 'disk_equivalence', 'local_boot',
          'unprovisioned_boot', 'provisioned_access', 'identity_regeneration', 'netbird'}
ROLES = {'iso', 'qcow2', 'vmdk', 'source', 'licenses', 'evidence'}
LIMIT = 2 * 1024**3
# A format delimiter is also ordinary text in SSH/key-parser source and ELF
# executables. Require following armored key bytes, including encrypted PEM
# headers, so those programs remain buildable without allowing actual keys.
SECRET = re.compile(
    rb'-----BEGIN (?:RSA |EC |DSA |OPENSSH |ENCRYPTED )?PRIVATE KEY-----[ \t]*'
    rb'(?:\r?\n|\\n)(?:(?:Proc-Type|DEK-Info):[^\r\n]{1,256}\r?\n)*'
    rb'(?:\r?\n)*[A-Za-z0-9/+]{32}')


def filename(name):
    if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,199}', name):
        raise ValueError('Unsafe asset name')
    return name


def scan_secrets(path):
    """Streaming PEM/OpenSSH detection; does not claim to find every credential."""
    tail = b''
    with Path(path).open('rb') as source:
        while chunk := source.read(1024**2):
            data = tail + chunk
            if SECRET.search(data):
                raise ValueError('Private key material in release content')
            tail = data[-1024:]


def verify_sources(root, inventory, assets):
    """Every shipped package needs exact source/license bytes, not only a URL."""
    if not inventory.get('components'):
        raise ValueError('Empty component inventory')
    components = set()
    members = {}
    from .source_fixtures import allowances
    public_vectors=allowances(inventory)
    for asset in assets:
        if asset['role'] != 'source':
            continue
        with tarfile.open(root / asset['name']) as archive:
            for member in archive:
                if member.isdir():
                    continue
                if (not member.isfile() or Path(member.name).is_absolute() or '..' in Path(member.name).parts
                        or str(Path(member.name)) != member.name or '\\' in member.name):
                    raise ValueError('Unsafe source archive member')
                key = (asset['name'], member.name)
                if key in members:
                    raise ValueError('Duplicate source member')
                import hashlib
                digest = hashlib.sha256()
                tail = b''
                key_match=False
                with archive.extractfile(member) as stream:
                    while chunk := stream.read(1024**2):
                        if SECRET.search(tail + chunk):key_match=True
                        digest.update(chunk)
                        tail = (tail + chunk)[-1024:]
                if key_match and (asset['name'],member.name,digest.hexdigest()) not in public_vectors:
                    raise ValueError('Private signing key in source bundle')
                members[key] = digest.hexdigest()
    def check_references(component):
        if not isinstance(component.get('license'),str) or not component.get('license') or component.get('redistributable') is not True:
            raise ValueError('Missing or unresolved component license clearance')
        for category in ('sources', 'license_files', 'build_inputs'):
            if not component.get(category):
                raise ValueError('Missing exact corresponding source, license or build inputs')
            for reference in component[category]:
                key = (filename(reference['asset']), reference['member'])
                if members.get(key) != reference['sha256']:
                    raise ValueError('Missing or differing corresponding-source member')
        children=component.get('bundled_components',[])
        if (component['name'] in {'vyos-http-api-tools','shirube-integration','vyos-1x',
                'node-exporter','frr-exporter','blackbox-exporter','telegraf','podman','salt'}
                or component['name'].startswith(('libvyosconfig','owamp','twamp','salt-'))) and not children:
            raise ValueError('Bundled dependencies need their own source/license inventory')
        identities=set()
        for child in children:
            identity=(child['name'],child['version'])
            if identity in identities:raise ValueError('Duplicate bundled component')
            identities.add(identity);check_references(child)
    for component in inventory['components']:
        identity = (component['name'], component['version'], component['architecture'])
        if identity in components or component['architecture'] not in ARCHES | {'all'}:
            raise ValueError('Duplicate or invalid component identity')
        components.add(identity)
        check_references(component)
    shipped = {(x['name'], x['version'], x['architecture']) for x in inventory['shipped_packages']}
    if not shipped or shipped != components:
        raise ValueError('Component inventory does not cover the exact shipped package set')


def validate(root, expected_commit, expected_version, *, architectures=ARCHES):
    root = Path(root)
    manifest = json.loads((root / 'manifest.json').read_text())
    version_parts(expected_version)
    if (manifest.get('schema_version') != 1 or manifest.get('manifest_kind') != 'release'
            or manifest.get('provider') != 'michibiki-io' or manifest.get('project') != 'Shirube OS'
            or manifest.get('version') != expected_version
            or manifest.get('source_commit') != expected_commit or manifest.get('source_clean') is not True
            or not re.fullmatch('[0-9a-f]{40}', expected_commit)):
        raise ValueError('Release identity/clean-source mismatch')
    upstream = manifest.get('upstream', {})
    lock=json.loads((Path(__file__).resolve().parents[1]/'source-lock.json').read_text())
    require_stream_version(expected_version,lock['stream']['version'])
    if (upstream.get('version') != lock['stream']['version']
            or upstream.get('sha256') != lock['stream']['sha256']
            or upstream.get('signature') != 'PASS'
            or upstream.get('vpp_commit') != lock['external_git']['vpp']['commit']
            or upstream.get('vpp_patches_commit') != lock['external_git']['vpp-patches']['commit']
            or not upstream.get('substitution')):
        raise ValueError('Unexpected upstream baseline')
    if not architectures or not set(architectures) <= ARCHES or set(manifest.get('architectures', {})) != set(architectures):
        raise ValueError('Both native architecture candidates are mandatory')
    assets = manifest.get('assets', [])
    names = set()
    for item in assets:
        name = filename(item['name'])
        if name in names:
            raise ValueError('Duplicate asset')
        names.add(name)
        path = root / name
        if (path.is_symlink() or not path.is_file() or item.get('role') not in ROLES
                or not 0 < path.stat().st_size < LIMIT or path.stat().st_size != item.get('size')
                or sha256(path) != item.get('sha256')):
            raise ValueError('Missing, differing or oversized release asset')
        scan_secrets(path)
    for architecture, candidate in manifest['architectures'].items():
        from .architecture import release_accelerator
        release_accelerator(architecture,candidate.get('qemu_accelerator'))
        if set(candidate.get('checks', {})) != CHECKS or any(
                value != 'PASS' for value in candidate['checks'].values()):
            raise ValueError('A required check failed, was skipped or was not run')
        if candidate.get('oci') != 'NOT_RUN':
            raise ValueError('Live OCI evidence is not implemented; only NOT_RUN is allowed')
        if candidate.get('virtual_size') != 50 * 1024**3:
            raise ValueError('Export virtual size must be 50 GiB')
        if candidate.get('standalone') is not True or candidate.get('vmdk_subformat') != 'streamOptimized':
            raise ValueError('Standalone QCOW2 and streamOptimized VMDK required')
        if not re.fullmatch('[0-9a-f]{64}', candidate.get('signing_certificate_sha256', '')):
            raise ValueError('Missing build-specific public signing certificate identity')
        if not candidate.get('repository_fingerprint'):
            raise ValueError('Missing build-specific public repository key identity')
        owned = [x for x in assets if x.get('architecture') == architecture]
        if not ROLES <= {x['role'] for x in owned} | {
                x['role'] for x in assets if x.get('architecture') == 'all'}:
            raise ValueError('Incomplete per-architecture asset set')
        console = 'ttyAMA0' if architecture == 'arm64' else 'ttyS0'
        if candidate.get('firmware') != 'UEFI' or candidate.get('console') != {'device':console, 'baud':115200}:
            raise ValueError('Architecture-specific firmware/console requirements missing')
        if candidate.get('provisioning') != {'method':'console-enrollment-v1', 'default_credentials':False}:
            raise ValueError('Per-instance provisioning contract missing')
        generation = candidate.get('generation')
        if not re.fullmatch('[0-9a-f]{64}', generation or '') or any(
                x.get('generation') != generation for x in owned):
            raise ValueError('Mixed or missing architecture candidate signing generation')
        for role in ('iso', 'qcow2', 'vmdk', 'evidence'):
            matching = [x for x in owned if x['role'] == role]
            if len(matching) != 1:
                raise ValueError('Exactly one binary/evidence asset per architecture required')
            if role in {'iso','qcow2','vmdk'} and matching[0]['name'] != f'shirube-os-{expected_version}-{architecture}.{role}':
                raise ValueError('Binary asset name must identify product, version and architecture')
        evidence_asset = next(x for x in owned if x['role'] == 'evidence')
        evidence = json.loads((root / evidence_asset['name']).read_text())
        if (evidence.get('source_commit') != expected_commit or evidence.get('version') != expected_version
                or evidence.get('architecture') != architecture or evidence.get('generation') != generation
                or set(evidence.get('checks', {})) != CHECKS):
            raise ValueError('Evidence is not bound to this candidate')
        if evidence.get('qemu_accelerator')!=candidate['qemu_accelerator']:
            raise ValueError('Evidence accelerator differs from this candidate')
        for check, result in evidence['checks'].items():
            if result.get('status') != 'PASS' or not result.get('observations'):
                raise ValueError('Missing actual observations for a required check')
        inventory_asset = next((x for x in owned if x['role'] == 'licenses'), None)
        if inventory_asset is None:
            raise ValueError('Per-architecture shipped-package inventory required')
        verify_sources(root, json.loads((root / inventory_asset['name']).read_text()), assets)
    actual = {p.name for p in root.iterdir()}
    if actual != names | {'manifest.json', 'SHA256SUMS'}:
        raise ValueError('Unlisted files or missing release metadata')
    expected = {name: sha256(root / name) for name in names | {'manifest.json'}}
    sums = {}
    for line in (root / 'SHA256SUMS').read_text().splitlines():
        match = re.fullmatch(r'([0-9a-f]{64})  ([A-Za-z0-9][A-Za-z0-9._-]{0,199})', line)
        if not match or match[2] in sums:
            raise ValueError('Invalid or duplicate checksum entry')
        sums[match[2]] = match[1]
    if sums != expected:
        raise ValueError('Checksum list does not exactly cover the complete release')
    return manifest


def reconcile_published(recorded, live):
    """Published reruns are verification-only: no rebuilding or asset repair."""
    if not recorded or not {'manifest.json', 'SHA256SUMS'} <= set(recorded) or recorded != live:
        raise ValueError('Published assets differ or are missing; release a superseding version')
    return 'NO_OP'
