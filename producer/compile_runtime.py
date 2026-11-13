"""runtime compile source/export recipe. Copyright (c) 2026 michibiki.io; MIT."""
import json
from pathlib import Path
import subprocess
from .source_selectors import files, tracked_source_files
from .native_stages import copy_selected, snapshot_files
from .select_runtime import i2util_revision_evidence, staged_i2util_files
EXTRAS = {'runtime': ('base', 'go', 'podman')}
ROOTS = {'runtime': ('libpam-radius-auth_1.5.0-cl3u7', 'humps_v3.8.0', 'ddclient_debian_3.11.2-1', 'owamp_v4.4.6', 'openvpn-otp_9781ff1', 'udp-broadcast-relay_9a0f13b4b5938bfe0af0d0d4e99dfc127c068e28', 'node_exporter_v1.9.1', 'frr_exporter_v1.5.0', 'blackbox_exporter_v0.26.0', 'telegraf_v1.28.3', 'podman_v4.9.5')}
SNAPSHOTS = {'base/pam': 'libpam-radius-auth_1.5.0-cl3u7/libpam-radius-auth', 'base/humps': 'humps_v3.8.0/humps', 'base/ddclient': 'ddclient_debian_3.11.2-1/ddclient', 'base/owamp': 'owamp_v4.4.6/owamp', 'base/otp': 'openvpn-otp_9781ff1/openvpn-otp', 'base/relay': 'udp-broadcast-relay_9a0f13b4b5938bfe0af0d0d4e99dfc127c068e28/udp-broadcast-relay', 'go/node-exporter': 'node_exporter_v1.9.1/node_exporter', 'go/frr-exporter': 'frr_exporter_v1.5.0/frr_exporter', 'go/blackbox-exporter': 'blackbox_exporter_v0.26.0/blackbox_exporter', 'go/telegraf': 'telegraf_v1.28.3/telegraf', 'podman/podman': 'podman_v4.9.5/podman'}
GIT_SOURCES = {'runtime': ('base/utils', 'base/i2util')}

def export_material(stage, sources, packages, destination, architecture):
    material = destination / "material"
    for location, original in SNAPSHOTS.items():
        if location.split('/')[0] in EXTRAS['runtime']:
            copy_selected(snapshot_files(Path('/extra') / location, sources / original, rename_pam=location == 'base/pam'), material / 'extra' / location)
    for location in GIT_SOURCES.get('runtime', ()):
        root = Path('/extra') / location
        metadata = {'kind': 'tracked'}
        if location == 'base/i2util':
            lock = json.loads(Path('/src/native/external-sources.lock.json').read_text())['i2util']
            metadata['revision_evidence'] = i2util_revision_evidence(root, lock)
        target = material / 'extra' / location
        metadata['members'] = copy_selected(tracked_source_files(root), target)
        (target / 'shirube-stage-source.json').write_text(json.dumps(metadata) + '\n')
        if location == 'base/i2util':
            staged_i2util_files(target, lock)
