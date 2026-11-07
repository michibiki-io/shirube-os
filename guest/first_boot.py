#!/usr/bin/python3
"""Secure local-console enrollment; no reusable distribution account/password."""
import crypt
import getpass
from pathlib import Path
import re
import pwd
import subprocess

MARKER = Path('/config/shirube-first-boot')


def main():
    if not MARKER.exists():
        return
    print('Shirube OS — built by michibiki-io\nCreate this instance administrator at the local console.')
    username = input('Administrator name: ')
    if not re.fullmatch(r'[a-z][a-z0-9_]{0,30}', username) or username in {'root', 'vyos'}:
        raise ValueError('Use a new lowercase administrator name')
    try:
        pwd.getpwnam(username)
    except KeyError:
        pass
    else:
        raise ValueError('Administrator name must not reuse an installed system account')
    password = getpass.getpass('New instance password (at least 16 characters): ')
    confirmation = getpass.getpass('Repeat password: ')
    if len(password) < 16 or password != confirmation or any(c in password for c in '\r\n\0'):
        raise ValueError('Passwords must match and have at least 16 characters')
    hashed = crypt.crypt(password, crypt.mksalt(crypt.METHOD_SHA512))
    password = confirmation = None
    if not hashed or not hashed.startswith('$6$'):
        raise ValueError('Password hashing failed')
    # Secret/password hash stays out of argv, console output, shell history and logs.
    # Only this instance saved configuration receives the resulting password hash.
    script = ('source /opt/vyatta/etc/functions/script-template\n'
              'configure\n'
              f"set system login user {username} authentication encrypted-password '{hashed}'\n"
              f'set system login user {username} level admin\n'
              'set service ssh\ncommit\nsave\nexit\n')
    subprocess.run(['/bin/vbash', '-se'], input=script, text=True, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(['systemd-machine-id-setup'], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(['ssh-keygen', '-A'], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    MARKER.unlink()
    print('Instance administrator created. Continue with the normal login prompt.')


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print('Instance enrollment failed. No reusable distribution password is available; retry at the console.')
        raise SystemExit(1)
