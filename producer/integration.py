"""Shared integration inputs and a network-free release approval preflight."""
import hashlib
import json
from pathlib import Path
import re

from .policy import require_stream_version
from .sources import sha256

REVISION_POLICY = 'shirube-integration-stream-revisions-v1'
NOTICE_PREFIX = b'Shirube OS integration: Copyright (c) 2026 michibiki-io. MIT License.\n\n'
NETBIRD_MARKER = b'\n\nNetBird headless client upstream notice (directory exceptions retained):\n'
LINKED_NOTICES = b'\nExact linked Go dependency and standard-library notices are retained under netbird-licenses/.\n'


def notice_bytes(repository_license, netbird_license):
    return NOTICE_PREFIX + repository_license + NETBIRD_MARKER + netbird_license + LINKED_NOTICES


def provenance(lock, architecture, repository_license):
    if architecture not in {'amd64', 'arm64'}:
        raise ValueError('Unsupported integration architecture')
    netbird, geoip = lock['netbird'], lock['geoip']
    return {'netbird_source_commit': netbird['source_commit'],
            'netbird_source_sha256': netbird['source_sha256'],
            'headless_asset_sha256': netbird['assets'][architecture]['sha256'],
            'integration_license_sha256': hashlib.sha256(repository_license).hexdigest(),
            'geoip_source_sha256': geoip['sha256'],
            'geoip_notice_sha256': geoip['notice_sha256']}


def source_identity(inputs):
    return hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()


def revision_approval(approvals, stream, identity, digest):
    """Select one explicitly opted-in exact record, never an arbitrary older one."""
    candidates = []
    for key, record in approvals.items():
        if (record.get('product_version_policy') != REVISION_POLICY or
                record.get('source_identity') != identity or
                record.get('copyright_sha256') != digest):
            continue
        prefix, separator, binding = key.partition('#')
        if not prefix.startswith('shirube-integration@') or not separator:
            continue
        approved_version = prefix.removeprefix('shirube-integration@')
        try:
            require_stream_version('v'+approved_version, stream)
        except ValueError:
            continue
        if binding != identity+'#'+digest:
            continue
        candidates.append(record)
    return candidates[0] if len(candidates) == 1 else {}


def preflight(version, root=Path('.')):
    """Check local reviewed inputs only; downloaded bytes and children need final checks."""
    from .collect import approval_for
    root = Path(root)
    lock = json.loads((root/'source-lock.json').read_text())
    require_stream_version(version, lock['stream']['version'])
    approvals = json.loads((root/'license-audit.json').read_text())['components']
    repository_license = (root/'LICENSE').read_bytes()
    for architecture in ['amd64', 'arm64']:
        identity = source_identity(provenance(lock, architecture, repository_license))
        # Only explicitly reviewed, hash-bound retained integration notices may
        # supply the offline NetBird input. The generator still uses today's LICENSE.
        records = [record for record in approvals.values()
                   if record.get('source_identity') == identity
                   and record.get('product_version_policy') == REVISION_POLICY]
        digests = {record.get('copyright_sha256') for record in records}
        if len(digests) != 1:
            raise ValueError('Missing or ambiguous integration preflight notice: '+architecture)
        digest = digests.pop()
        if not isinstance(digest, str) or not re.fullmatch('[0-9a-f]{64}', digest):
            raise ValueError('Invalid retained integration notice digest')
        retained = root/'LICENSES/Module-client-original-notices/shirube-integration'/f'{digest}.txt'
        if retained.is_symlink() or not retained.is_file() or sha256(retained) != digest:
            raise ValueError('Missing or changed retained integration notice: '+architecture)
        data = retained.read_bytes()
        if data.count(NETBIRD_MARKER) != 1 or not data.endswith(LINKED_NOTICES):
            raise ValueError('Invalid retained integration notice framing')
        netbird_license = data.split(NETBIRD_MARKER)[1][:-len(LINKED_NOTICES)]
        actual = hashlib.sha256(notice_bytes(repository_license, netbird_license)).hexdigest()
        reviewed = approval_for(approvals, 'shirube-integration', version[1:], identity,
                                root, copyright_sha256=actual)
        if (reviewed.get('source_identity') != identity or
                reviewed.get('copyright_sha256') != actual or
                reviewed.get('redistributable') is not True or
                not isinstance(reviewed.get('license'), str) or not reviewed['license']):
            raise ValueError('Integration approval preflight failed: '+architecture)
