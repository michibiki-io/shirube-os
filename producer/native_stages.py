"""Native compilation tranches and explicit key-free source/SDK exports.

These are compiler entry points, not release clearance. No raw working tree is
exported: source selectors reuse the corresponding-source inventory contracts.
"""
import json
from pathlib import Path
import shutil
import subprocess

from .collect import (files, tracked_source_files, vpp_source_files, kea_source_files,
                      i2util_revision_evidence, staged_i2util_files)
from .sources import sha256

COMPILE_STAGES = ('native', 'vpp', 'kea', 'salt', 'runtime', 'kernel')
EXTRAS = {'native': (), 'vpp': ('vpp',), 'kea': ('kea',), 'salt': ('salt',),
          'runtime': ('base', 'go', 'podman'), 'kernel': ('modules', 'accel')}
DEPENDENCIES = {name: (('vpp',) if name == 'kernel' else ()) for name in COMPILE_STAGES}
ROOTS = {
    'vpp': (), 'kea': ('isc-kea_Kea-3.0.2',), 'salt': ('salt_v3006.21',),
    'runtime': ('libpam-radius-auth_1.5.0-cl3u7', 'humps_v3.8.0', 'ddclient_debian_3.11.2-1',
                'owamp_v4.4.6', 'openvpn-otp_9781ff1',
                'udp-broadcast-relay_9a0f13b4b5938bfe0af0d0d4e99dfc127c068e28',
                'node_exporter_v1.9.1', 'frr_exporter_v1.5.0', 'blackbox_exporter_v0.26.0',
                'telegraf_v1.28.3', 'podman_v4.9.5'),
    'kernel': ('jool-4.1.9+bf4c7e3669', 'ovpn-dco-v0.2.20231117',
               'accel-ppp-ng-3e30d9b', 'host-sflow_v2.1.21-1'), 'native': (),
}
# Explicit source mappings; snapshot inventories supply the original member set.
SNAPSHOTS = {
    'base/pam': 'libpam-radius-auth_1.5.0-cl3u7/libpam-radius-auth',
    'base/humps': 'humps_v3.8.0/humps', 'base/ddclient': 'ddclient_debian_3.11.2-1/ddclient',
    'base/owamp': 'owamp_v4.4.6/owamp', 'base/otp': 'openvpn-otp_9781ff1/openvpn-otp',
    'base/relay': 'udp-broadcast-relay_9a0f13b4b5938bfe0af0d0d4e99dfc127c068e28/udp-broadcast-relay',
    'go/node-exporter': 'node_exporter_v1.9.1/node_exporter',
    'go/frr-exporter': 'frr_exporter_v1.5.0/frr_exporter',
    'go/blackbox-exporter': 'blackbox_exporter_v0.26.0/blackbox_exporter',
    'go/telegraf': 'telegraf_v1.28.3/telegraf', 'podman/podman': 'podman_v4.9.5/podman',
    'modules/ovpn': 'ovpn-dco-v0.2.20231117/ovpn-dco',
    'modules/jool': 'jool-4.1.9+bf4c7e3669/Jool-7f08c42c615ed63cf0fdc1522d91aa0809f6d990',
    'accel/accel': 'accel-ppp-ng-3e30d9b/accel-ppp-ng',
    'accel/sflow': 'host-sflow_v2.1.21-1/host-sflow',
}
GIT_SOURCES = {'runtime': ('base/utils', 'base/i2util'),
               'kernel': ('modules/rtsp', 'modules/netflow'),
               'salt': ('salt/salt', 'salt/salt/python-tools-scripts')}


def select_jobs(catalog, stage):
    if stage not in COMPILE_STAGES: raise ValueError('Unknown compile stage')
    return [job for job in catalog if (job['name'] == 'kernel') == (stage == 'kernel')] if stage in {'native', 'kernel'} else []


def copy_selected(selected, destination):
    """Materialize only selected regular preferred sources, not directory aliases."""
    destination = Path(destination); destination.mkdir(parents=True, exist_ok=True)
    inventory = []
    for source, member in selected:
        from .stage_artifacts import member_name
        member_name(member)
        target = destination / member
        if target.exists():
            if sha256(target) != sha256(source): raise ValueError('Conflicting source selection')
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        target.chmod(0o755 if source.stat().st_mode & 0o111 else 0o644)
        inventory.append(member)
    if not inventory: raise ValueError('Empty preferred source selection')
    return inventory


def snapshot_files(actual, preferred, *, rename_pam=False):
    """Retain actual patched source bytes only at authenticated original paths.

    Generated dependency inventories and exact build identity are explicit additions;
    package/, build/, virtualenvs and arbitrary new compiler outputs are excluded.
    """
    actual, preferred = Path(actual), Path(preferred)
    for _, member in files(preferred, exclude=('build', 'debian/.debhelper')):
        if rename_pam and member.startswith('debian/libpam-radius-auth.'):
            member=member.replace('debian/libpam-radius-auth.','debian/vyos-libpam-radius-auth.',1)
        path = actual / member
        if path.is_file():
            if not path.resolve().is_relative_to(actual.resolve()): raise ValueError('External source alias')
            yield path.resolve(), member
    for path, member in files(actual / 'shirube-corresponding-inputs'):
        yield path, 'shirube-corresponding-inputs/' + member
    if (actual / 'SHIRUBE-BUILD.json').is_file(): yield actual / 'SHIRUBE-BUILD.json', 'SHIRUBE-BUILD.json'


def export_vpp_sdk(source, destination, multiarch, architecture):
    """Accel needs generated and upstream API headers beyond the four VPP DEBs."""
    source, destination = Path(source), Path(destination)
    # Export only header files from these precise SDK roots. Link libraries are
    # already supplied by libvppinfra/vpp DEBs and extracted by accel().
    roots = {'source': source / 'src',
             'generated': source / 'build-root/build-vpp-native/vpp/CMakeFiles/vpp-api'}
    counts = {}
    for label, root in roots.items():
        selected = [(p, str(p.relative_to(root))) for p in sorted(root.rglob('*.h')) if p.is_file()]
        if any(not p.resolve().is_relative_to(source.resolve()) for p,_ in selected):
            raise ValueError('External VPP SDK header link')
        counts[label] = len(copy_selected(selected, destination / label))
    install = source / 'build-root/install-vpp-native/vpp/lib' / multiarch
    libraries = [(p.resolve(), p.name) for p in sorted(install.glob('*.so*')) if p.is_file()]
    if not libraries: raise ValueError('VPP SDK link libraries missing')
    if any(not p.is_relative_to(source.resolve()) for p, _ in libraries): raise ValueError('External SDK library link')
    from .architecture import check_elf
    for path,_ in libraries:check_elf(path.read_bytes(),architecture)
    copy_selected(libraries, destination / 'lib')
    (destination / 'sdk.json').write_text(json.dumps({'schema_version': 1, 'multiarch': multiarch,
        'header_counts': counts, 'libraries': [name for _, name in libraries]}, sort_keys=True) + '\n')


def export_compile(stage, sources, packages, destination, architecture):
    """Export an explicit key-free material layout consumed by collect()."""
    from .stage_artifacts import inventory
    destination = Path(destination)
    material = destination / 'material'
    catalog = json.loads(Path('/src/native/packages.json').read_text())['jobs']
    for job in select_jobs(catalog, stage):
        copy_selected(files(sources / job['source'], exclude=(
            'debian', 'include/generated', 'arch/arm64/include/generated', 'arch/x86/include/generated',
            'certs/signing_key.x509', 'arch/arm64/boot/Image', 'arch/arm64/boot/Image.gz',
            'arch/x86/boot/bzImage', 'vmlinux.btf', 'Module.symvers', 'modules.order',
            'modules.builtin', 'modules.builtin.modinfo') if job['name'] == 'kernel' else ()),
            material / 'sources' / job['source'])
        if job['name'] == 'vyos-http-api-tools':
            copy_selected(files(Path('/build-private') / job['name'] / 'source'),
                          material / 'native' / job['name'])
        for path in (packages / job['name']).iterdir():
            if path.is_file(): copy_selected([(path, path.name)], destination / 'packages' / job['name'])
    # The builder recipes are authenticated original inputs, not compiled trees.
    copy_selected(files(sources / 'vyos-build/vyos-build'), material / 'sources/vyos-build/vyos-build')
    if stage == 'kea':
        tool = json.loads(Path('/src/source-lock.json').read_text())['kea_build_tool']
        root = Path('/extra/kea/isc-kea')
        members=copy_selected(kea_source_files(root, sources / 'isc-kea_Kea-3.0.2/isc-kea', Path('/extra/kea/packaging'), tool),
                      material / 'extra/kea/isc-kea')
        # Already prepared recipe inventory: collect must not require .git.
        (material / 'extra/kea/isc-kea/shirube-stage-source.json').write_text(json.dumps({'kind':'kea','members':members})+'\n')
    if stage=='salt':
        compiler={'version':subprocess.check_output(['rustc','--version'],text=True).strip(),
                  'source':subprocess.check_output(['dpkg-query','-W','-f=${source:Package}\t${source:Version}','libstd-rust-dev'],text=True).strip()}
        lock=json.loads(Path('/src/native/salt-sources.lock.json').read_text())['rust_compiler']
        if compiler!={'version':lock['version_output'],'source':lock['source_package']+'\t'+lock['source_version']}:
            raise ValueError('Actual Salt Rust compiler differs')
        target=material/'salt-compiler.json';target.parent.mkdir(parents=True,exist_ok=True)
        target.write_text(json.dumps(compiler,sort_keys=True)+'\n')
    for location, original in SNAPSHOTS.items():
        if location.split('/')[0] in EXTRAS[stage]:
            copy_selected(snapshot_files(Path('/extra') / location, sources / original,rename_pam=location=='base/pam'), material / 'extra' / location)
    for location in GIT_SOURCES.get(stage, ()):
        root=Path('/extra') / location
        metadata={'kind':'tracked'}
        if location=='base/i2util':
            lock=json.loads(Path('/src/native/external-sources.lock.json').read_text())['i2util']
            metadata['revision_evidence']=i2util_revision_evidence(root,lock)
        target=material / 'extra' / location
        metadata['members']=copy_selected(tracked_source_files(root),target)
        (target/'shirube-stage-source.json').write_text(json.dumps(metadata)+'\n')
        if location=='base/i2util':staged_i2util_files(target,lock)
    if stage == 'vpp':
        members=copy_selected(vpp_source_files(Path('/extra/vpp/vpp')), material / 'extra/vpp/vpp')
        (material / 'extra/vpp/vpp/shirube-stage-source.json').write_text(json.dumps({'kind':'tracked','members':members})+'\n')
        copy_selected(tracked_source_files(Path('/extra/vpp/patches')), material / 'extra/vpp/patches')
        multi = subprocess.check_output(['dpkg-architecture', '-qDEB_HOST_MULTIARCH'], text=True).strip()
        export_vpp_sdk(Path('/extra/vpp/vpp'), destination / 'vpp-sdk', multi, architecture)
    for path in Path('/debs').glob('*.deb'):
        # Dependency DEBs consumed by this job remain owned by their producer.
        if stage == 'kernel' and not (packages / 'kernel' / path.name).exists() and path.name in {
                p.name for p in Path('/stage-dependencies/vpp/debs').glob('*.deb')}:
            continue
        copy_selected([(path, path.name)], destination / 'debs')
    if stage == 'kernel':
        copy_selected([(Path('/kernel-signing/cert.der'), 'cert.der')], destination / 'signing')
    if stage == 'native':
        # Preserve the actual native switch's source/notices, never the switch.
        from .ocaml_sources import retain
        ocaml = material / 'ocaml'; ocaml.mkdir()
        records, components = retain(Path('/opt/opam'), ocaml / 'sources.tar.gz', Path('/build-private/ocaml-export'), files)
        copy_selected(files(Path('/build-private/ocaml-export')), ocaml / 'notices')
        (ocaml / 'receipt.json').write_text(json.dumps({'records': records, 'components': components}, default=str) + '\n')
    inventory(destination)  # Fail before any transfer if selectors expose a key.


def finish_compile(stage, sources, packages, public_output, architecture):
    """All signing happens here, before the only export, in one private job."""
    from .stage_artifacts import inventory
    from .native_common import generate, run
    root = generate(architecture)
    Path('/sources').symlink_to(sources)
    debs = Path('/debs'); debs.mkdir(exist_ok=True)
    for package in packages.rglob('*.deb'): shutil.copy2(package, debs / package.name)
    if stage == 'kernel':
        dependency = Path('/stage-dependencies/vpp')
        if not (dependency / 'vpp-sdk/sdk.json').is_file(): raise ValueError('Inventoried VPP SDK required')
        inventory(dependency)
        for package in (dependency / 'debs').glob('*.deb'): shutil.copy2(package, debs / package.name)
        shutil.copytree(dependency / 'vpp-sdk', Path('/vpp-sdk'))
        signing = Path('/kernel-signing'); signing.mkdir(mode=0o700)
        certs = Path('/build-private/kernel/source/certs')
        shutil.copy2(certs / 'signing_key.pem', signing / 'key.pem'); (signing / 'key.pem').chmod(0o600)
        shutil.copy2(certs / 'signing_key.x509', signing / 'cert.der')
    if EXTRAS[stage]:
        run('apt-get', '-o', 'APT::Update::Error-Mode=any', 'update')
    for job in EXTRAS[stage]:
        print('STAGE: native runtime ' + job, flush=True)
        run('python3', str(root / 'extras.py'), job)
    from .package_notices import COMPONENTS, retain
    for package in debs.glob('*.deb'):
        name=subprocess.check_output(['dpkg-deb','-f',str(package),'Package'],text=True).strip()
        if name in COMPONENTS:
            retain(package,COMPONENTS[name][1],Path('/build-private')/('notices-'+name))
    from .packages import audit
    for package in debs.glob('*.deb'):
        audit(package, architecture, Path('/kernel-signing/cert.der') if stage == 'kernel' else None)
    export_compile(stage, sources, packages, public_output, architecture)
    from .library_cache import compiler_fingerprint
    observations=Path(public_output)/'provenance';observations.mkdir(exist_ok=True)
    shutil.copyfile(packages/'source-preparation.json',observations/(stage+'-source-preparation.json'))
    (observations/(stage+'-compiler.json')).write_text(json.dumps({
        'post_build_compiler_fingerprint':compiler_fingerprint(),
        'scope':'Actual installed compiler environment after this pinned stage recipe'},sort_keys=True)+'\n')


def restore_ocaml_evidence(material, destination, work):
    """Rebind producer source references, preserving the exact archived bytes."""
    material, destination, work = map(Path, (material, destination, work))
    receipt = json.loads((material / 'receipt.json').read_text())
    shutil.copyfile(material / 'sources.tar.gz', destination)
    work.mkdir()
    def rebind(value):
        if isinstance(value, list): return [rebind(v) for v in value]
        if isinstance(value, dict):
            result = {k: rebind(v) for k, v in value.items()}
            if result.get('asset') == 'sources.tar.gz': result['asset'] = destination.name
            if 'notice' in result:
                name = Path(result['notice']).name
                target = work / name
                shutil.copyfile(material / 'notices' / name, target)
                result['notice'] = target
            return result
        return value
    return rebind(receipt['records']), rebind(receipt['components'])
