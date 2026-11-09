"""Pinned public GeoIP data, imported through the router's native interface."""
import csv
import gzip
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess

from .sources import sha256


def row_digest(rows):
    digest = hashlib.sha256()
    counts = {4: 0, 6: 0}
    for country, interval, version in rows:
        if version not in counts or len(country) != 2 or not country.islower():
            raise ValueError('Invalid native GeoIP row')
        counts[version] += 1
        digest.update((json.dumps([country, interval, version], separators=(',', ':'))+'\n').encode())
    return digest.hexdigest(), {str(k): v for k, v in counts.items()}


def verify_source(project):
    project = Path(project)
    lock = json.loads((project/'source-lock.json').read_text())['geoip']
    root = project/'third_party/geoip'
    for name, expected in [(lock['filename'], lock['sha256']),
                           ('copyright', lock['notice_sha256']),
                           ('CC-BY-4.0.txt', lock['license_sha256'])]:
        path = root/name
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root.resolve()) or sha256(path) != expected:
            raise ValueError('Pinned GeoIP source or attribution mismatch')
    if (root/lock['filename']).stat().st_size != lock['compressed_size']:
        raise ValueError('Pinned GeoIP source size mismatch')
    raw = gzip.decompress((root/lock['filename']).read_bytes())
    if len(raw) != lock['uncompressed_size'] or hashlib.sha256(raw).hexdigest() != lock['uncompressed_sha256']:
        raise ValueError('Uncompressed GeoIP source mismatch')
    def rows():
        for start, end, country in csv.reader(raw.decode('utf-8').splitlines()):
            first, last = ipaddress.ip_address(start), ipaddress.ip_address(end)
            if first.version != last.version or first > last or len(country) != 2 or not country.isupper():
                raise ValueError('Invalid original GeoIP range')
            yield country.lower(), start+'-'+end, first.version
    digest, counts = row_digest(rows())
    if digest != lock['logical_rows_sha256'] or counts != lock['ip_versions'] or sum(counts.values()) != lock['rows']:
        raise ValueError('Pinned GeoIP logical content mismatch')
    return lock


def verify_database(database, lock):
    with sqlite3.connect(Path(database).resolve().as_uri()+'?mode=ro', uri=True) as conn:
        if conn.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
            raise ValueError('Invalid GeoIP SQLite database')
        columns = conn.execute('PRAGMA table_info(geoip_ranges)').fetchall()
        if [(row[1], row[2], row[3]) for row in columns] != [('country_code','TEXT',1),('range','TEXT',1),('version','INT',1)]:
            raise ValueError('Native GeoIP schema changed')
        digest, counts = row_digest(conn.execute('SELECT country_code, range, version FROM geoip_ranges ORDER BY rowid'))
        if digest != lock['logical_rows_sha256'] or counts != lock['ip_versions']:
            raise ValueError('Native GeoIP import does not match pinned CSV')
        if conn.execute('PRAGMA freelist_count').fetchone()[0] != 0:
            raise ValueError('Factory GeoIP template contains stale pages')
    return {'rows':sum(counts.values()), 'ip_versions':counts,
            'logical_rows_sha256':digest, 'database_sha256':sha256(Path(database))}


def install(project, native_source, package):
    project, native_source, package = map(Path, (project, native_source, package))
    lock = verify_source(project)
    api = native_source/'python/vyos/geoip.py'
    if sha256(api) != lock['native_api_sha256']:
        raise ValueError('Native GeoIP importer differs from pinned source')
    data = package/'usr/share/shirube/geoip'
    docs = package/'usr/share/doc/shirube-integration/geoip'
    data.mkdir(parents=True); docs.mkdir(parents=True)
    for name in ['copyright', 'CC-BY-4.0.txt']:
        shutil.copy2(project/'third_party/geoip'/name, docs/name)
    raw = data/lock['filename']; shutil.copy2(project/'third_party/geoip'/lock['filename'], raw)
    database = data/'geoip-lookup.db'
    code = '''import sys
from vyos import geoip
geoip.dbip_database_raw = sys.argv[1]
geoip.geoip_database_path = sys.argv[2]
geoip.db_initialise()
if not geoip.db_import_dbip_ranges(delete_file=False):
    raise RuntimeError('Native GeoIP import failed')
if not geoip.db_return_ranges(['us'], 4):
    raise RuntimeError('Native GeoIP country lookup failed')
'''
    subprocess.run(['python3','-c',code,str(raw),str(database)], check=True,
                   env=dict(os.environ, PYTHONPATH=str(native_source/'python')))
    result = verify_database(database, lock)
    (data/'components.json').write_text(json.dumps({'source':lock, 'import':result}, indent=2)+'\n')
    tmpfiles = package/'usr/lib/tmpfiles.d/shirube-geoip.conf'
    tmpfiles.parent.mkdir(parents=True, exist_ok=True)
    tmpfiles.write_text('d /var/cache/vyos 0755 root root -\n'
        'C /var/cache/vyos/geoip-lookup.db 0644 root root - /usr/share/shirube/geoip/geoip-lookup.db\n')
    return result


def replace_hook(hook, project):
    hook = Path(hook)
    lock = verify_source(project)
    if sha256(hook) != lock['upstream_hook_sha256']:
        raise ValueError('Upstream GeoIP initialization hook changed')
    hook.write_text('#!/bin/sh\n# Geolocation data provided by DB-IP.com, CC-BY-4.0.\n'
        '# Shirube OS: use the immutable, source-bound native database template.\n'
        'set -eu\n'
        'test -s /usr/share/shirube/geoip/geoip-lookup.db\n'
        'systemd-tmpfiles --create /usr/lib/tmpfiles.d/shirube-geoip.conf\n'
        'test -s /var/cache/vyos/geoip-lookup.db\n')
    hook.chmod(0o755)
