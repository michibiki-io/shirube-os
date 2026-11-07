#!/usr/bin/python3
"""Non-secret service settings and a separate root-only, stdin enrollment path."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit

STATE = Path('/config/netbird')
RUNTIME = Path('/run/shirube-netbird')
BINARY = '/usr/libexec/shirube/netbird'
UNIT = 'shirube-netbird.service'


def validate(config):
    if config is None:
        return
    if not isinstance(config, dict) or set(config) - {'management_url', 'disable'}:
        raise ValueError('Only management_url and disable are persistent settings')
    url = config.get('management_url', 'https://api.netbird.io:443')
    try:
        parsed = urlsplit(url)
        valid = (parsed.scheme == 'https' and parsed.hostname and not parsed.username
                 and not parsed.password and not parsed.query and not parsed.fragment
                 and parsed.port != 0 and not re.search(r'[\s\x00-\x1f\x7f]', url))
    except (ValueError, TypeError):
        valid = False
    if not valid:
        raise ValueError('Management URL must be HTTPS without embedded credentials')


def private_directory(path):
    if path.is_symlink():
        raise ValueError('State and runtime directories must not be symlinks')
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.chmod(0o700)


def write_json(path, value):
    private_directory(path.parent)
    with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            os.fchmod(stream.fileno(), 0o600)
            json.dump(value, stream)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def unit(*args):
    subprocess.run(['systemctl', *args], check=True, timeout=45,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def apply(config):
    validate(config)
    unit('stop', UNIT)
    settings = RUNTIME / 'settings.json'
    settings.unlink(missing_ok=True)
    if config is None or 'disable' in config:
        return
    private_directory(STATE)
    write_json(settings, {'management_url': config.get('management_url', 'https://api.netbird.io:443')})
    # An unregistered service remains inactive until explicit enrollment.
    receipt = STATE / 'enrolled.json'
    if receipt.is_file() and json.loads(receipt.read_text()).get('management_url') == config.get('management_url', 'https://api.netbird.io:443'):
        unit('start', UNIT)


def enroll(stream):
    """Read one key from a pipe/secure prompt; never serialize it in settings."""
    settings_file = RUNTIME / 'settings.json'
    if not settings_file.is_file():
        raise ValueError('Configure and enable service netbird before enrollment')
    settings = json.loads(settings_file.read_text())
    validate(settings)
    private_directory(STATE)
    private_directory(RUNTIME)
    # An exclusive task file also serializes concurrent enrollments.
    key_file = RUNTIME / 'enrollment-key'
    descriptor = os.open(key_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    success = False
    try:
        with os.fdopen(descriptor, 'w') as output:
            key = stream.readline(514).rstrip('\r\n')
            if not re.fullmatch(r'[A-Za-z0-9._=~-]{1,512}', key):
                raise ValueError('Invalid enrollment input')
            output.write(key)
            key = None
        unit('stop', UNIT)
        # Connect the client to the daemon without persisting setup material.
        # Client uses NB_STATE_DIR for its generated peer identity.
        unit('start', UNIT)
        result = subprocess.run([BINARY, 'up', '--management-url', settings['management_url'],
                                 '--setup-key-file', str(key_file)],
                                env=dict(os.environ, NB_STATE_DIR=str(STATE)),
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=90)
        if result.returncode:
            raise RuntimeError('Enrollment failed')
        write_json(STATE / 'enrolled.json', {'management_url': settings['management_url']})
        success = True
    finally:
        key_file.unlink(missing_ok=True)
        if not success:
            unit('stop', UNIT)


def daemon():
    settings_file = RUNTIME / 'settings.json'
    if not settings_file.is_file():
        return
    validate(json.loads(settings_file.read_text()))
    private_directory(STATE)
    os.execve(BINARY, [BINARY, 'service', 'run', '--log-file', 'console'],
              dict(os.environ, NB_STATE_DIR=str(STATE)))


def main():
    try:
        if os.geteuid() != 0:
            raise ValueError('Root is required')
        if sys.argv[1:] == ['daemon']:
            daemon()
        elif sys.argv[1:] == ['enroll']:
            if sys.stdin.isatty():
                import getpass
                import io
                enroll(io.StringIO(getpass.getpass('NetBird one-shot enrollment key: ')))
            else:
                enroll(sys.stdin)
        else:
            raise ValueError('Expected daemon or enroll')
    except Exception:
        print('NetBird operation failed; secret details suppressed', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
