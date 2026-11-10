"""Exact delivery-only omissions of unused third-party inputs."""
import hashlib
import json
from pathlib import Path
import shutil
from .debian_sources import descriptor
from .sources import sha256

POLICIES = {
    'chardet': ('5.1.0+dfsg-2', 'd8743a4bacfdd5c133b32e4f96192a83f9f71a16fed4c928ad13b62147548c39'),
    'openvpn': ('2.6.14-0+deb12u2', 'cf2bb6b6ecb0dbd35830b1ba269a2f9375b1c8610e3720d6ff475b61efedf98e'),
}


def policy_hash(rule):
    return hashlib.sha256(json.dumps(rule, sort_keys=True,
        separators=(',', ':')).encode()).hexdigest()


def prepare(source, version, originals, tree, rule):
    expected = POLICIES.get(source)
    if expected != (version, policy_hash(rule)):
        raise ValueError('Unreviewed optional-input source export')
    dsc = descriptor(originals, source, version)
    if sha256(dsc) != rule['descriptor_sha256']:
        raise ValueError('Optional-input descriptor differs')
    tree = Path(tree).resolve()

    def regular(member):
        parts = Path(member).parts
        if not parts or Path(member).is_absolute() or '..' in parts:
            raise ValueError('Unsafe optional source member')
        path = tree
        for part in parts:
            path = path/part
            if path.is_symlink():
                raise ValueError('Aliased optional source member')
        if not path.is_file() or not path.resolve().is_relative_to(tree):
            raise ValueError('Missing regular optional source member')
        return path

    # Bind the entire policy and inspect every member before any mutation.
    # Runtime implementation and the original Linux/Python build controls
    # remain unchanged; only exact optional inputs named by the policy differ.
    for row in rule['unchanged_controls'] + rule['excluded']:
        if sha256(regular(row['member'])) != row['sha256']:
            raise ValueError('Optional export member or build scope differs')
    for row in rule['replacements']:
        if (sha256(regular(row['member'])) != row['original_sha256'] or
                hashlib.sha256(row['content'].encode()).hexdigest() !=
                row['replacement_sha256']):
            raise ValueError('Optional source replacement differs')
    marker = tree/'SHIRUBE-SOURCE-EXPORT.json'
    origin = tree/'SHIRUBE-ORIGINAL-SOURCE.dsc'
    if any(p.exists() or p.is_symlink() for p in (marker, origin)):
        raise ValueError('Optional export destination already exists')
    for row in rule['excluded']:
        regular(row['member']).unlink()
    for row in rule['replacements']:
        regular(row['member']).write_bytes(row['content'].encode())
    shutil.copyfile(dsc, origin)
    record = {
        'kind': rule['kind'], 'source': source, 'version': version,
        'original_descriptor_sha256': rule['descriptor_sha256'],
        'policy_sha256': policy_hash(rule), 'excluded': rule['excluded'],
        'replacements': [{k: v for k, v in row.items() if k != 'content'}
                         for row in rule['replacements']],
        'unchanged_controls': rule['unchanged_controls'],
        'modified_by': 'michibiki-io', 'modified_on': '2026-11-10',
        'reconstruction': rule['reconstruction']}
    marker.write_text(json.dumps(record, indent=2)+'\n')
    return {'kind': record['kind'], 'record': 'source/SHIRUBE-SOURCE-EXPORT.json',
            'sha256': sha256(marker), 'original_descriptor_sha256': sha256(dsc)}
