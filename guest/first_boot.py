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
    factory=Path('/usr/share/shirube/needs-enrollment')
    if not MARKER.exists() and not factory.exists():
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
    script = ('printf "SHIRUBE_ENROLL_SOURCE\\n"\n'
              'source /opt/vyatta/etc/functions/script-template\n'
              'printf "SHIRUBE_ENROLL_CONFIGURE\\n"\nconfigure\n'
              'printf "SHIRUBE_ENROLL_ADMINISTRATOR\\n"\n'
              f"set system login user {username} authentication encrypted-password '{hashed}'\n"
              'printf "SHIRUBE_ENROLL_SSH\\n"\nset service ssh\n'
              'printf "SHIRUBE_ENROLL_COMMIT\\n"\ncommit\n'
              'printf "SHIRUBE_ENROLL_SAVE\\n"\nsave\n'
              'printf "SHIRUBE_ENROLL_DONE\\n"\nexit\n')
    print('Preparing this instance identity...', flush=True)
    subprocess.run(['systemd-machine-id-setup'], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print('Generating this instance SSH host keys...', flush=True)
    subprocess.run(['ssh-keygen', '-A'], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print('Applying administrator configuration...', flush=True)
    result = subprocess.run(['/usr/bin/sg', 'vyattacfg', '-c', '/bin/vbash -se'],
                            input=script, text=True, capture_output=True)
    if result.returncode or 'SHIRUBE_ENROLL_DONE' not in result.stdout.splitlines():
        # Native diagnostics may contain the password hash. Report only a fixed
        # phase and integer status; never print captured stdout/stderr.
        phases = {'SHIRUBE_ENROLL_'+name: name.lower() for name in
                  ['SOURCE', 'CONFIGURE', 'ADMINISTRATOR', 'SSH', 'COMMIT', 'SAVE']}
        completed = [phases[line] for line in result.stdout.splitlines() if line in phases]
        phase = completed[-1] if completed else 'session'
        if result.returncode:
            print(f'Administrator configuration failed at {phase} (exit {result.returncode}).', flush=True)
        else:
            print(f'Administrator configuration did not complete after {phase}.', flush=True)
        raise RuntimeError('Native administrator configuration failed')
    MARKER.unlink(missing_ok=True)
    factory.unlink(missing_ok=True)
    print('Instance administrator created. Continue with the normal login prompt.')


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print('Instance enrollment failed. No reusable distribution password is available; retry at the console.')
        raise SystemExit(1)
