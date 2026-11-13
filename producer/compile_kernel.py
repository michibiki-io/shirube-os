"""kernel compile source/export recipe. Copyright (c) 2026 michibiki.io; MIT."""
import json
from pathlib import Path
import subprocess
import shutil
from .source_selectors import files, tracked_source_files
from .native_stages import copy_selected, snapshot_files
EXTRAS = {'kernel': ('modules', 'accel')}
ROOTS = {'kernel': ('jool-4.1.9+bf4c7e3669', 'ovpn-dco-v0.2.20231117', 'accel-ppp-ng-3e30d9b', 'host-sflow_v2.1.21-1')}
SNAPSHOTS = {'modules/ovpn': 'ovpn-dco-v0.2.20231117/ovpn-dco', 'modules/jool': 'jool-4.1.9+bf4c7e3669/Jool-7f08c42c615ed63cf0fdc1522d91aa0809f6d990', 'accel/accel': 'accel-ppp-ng-3e30d9b/accel-ppp-ng', 'accel/sflow': 'host-sflow_v2.1.21-1/host-sflow'}
GIT_SOURCES = {'kernel': ('modules/rtsp', 'modules/netflow')}

def export_material(stage, sources, packages, destination, architecture):
    material = destination / "material"
    for location, original in SNAPSHOTS.items():
        if location.split('/')[0] in EXTRAS['kernel']:
            copy_selected(snapshot_files(Path('/extra') / location, sources / original, rename_pam=location == 'base/pam'), material / 'extra' / location)
    for location in GIT_SOURCES.get('kernel', ()):
        root = Path('/extra') / location
        metadata = {'kind': 'tracked'}
        target = material / 'extra' / location
        metadata['members'] = copy_selected(tracked_source_files(root), target)
        (target / 'shirube-stage-source.json').write_text(json.dumps(metadata) + '\n')
    copy_selected([(Path('/kernel-signing/cert.der'), 'cert.der')], destination / 'signing')
    # finish_compile has audited every package/module before this export. The
    # workflow uploads compiler results only if the whole stage succeeds.
    from .kernel_compile_cache import finish
    finish()


def prepare_runtime(debs):
    """Prepare only this fresh kernel job's verified VPP SDK and private signing workspace."""
    from .stage_artifacts import inventory
    dependency = Path('/stage-dependencies/vpp')
    if not (dependency / 'vpp-sdk/sdk.json').is_file(): raise ValueError('Inventoried VPP SDK required')
    inventory(dependency)
    for package in (dependency / 'debs').glob('*.deb'): shutil.copy2(package, debs / package.name)
    shutil.copytree(dependency / 'vpp-sdk', Path('/vpp-sdk'))
    signing = Path('/kernel-signing'); signing.mkdir(mode=0o700)
    certs = Path('/build-private/kernel/source/certs')
    shutil.copy2(certs / 'signing_key.pem', signing / 'key.pem'); (signing / 'key.pem').chmod(0o600)
    shutil.copy2(certs / 'signing_key.x509', signing / 'cert.der')


def certificate():
    return Path('/kernel-signing/cert.der')
