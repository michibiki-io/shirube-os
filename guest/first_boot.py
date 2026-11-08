#!/usr/bin/python3
"""Secure local-console enrollment; no reusable distribution account/password."""
import crypt
import getpass
from pathlib import Path
import re
import pwd
import subprocess
import time

MARKER = Path('/config/shirube-first-boot')


def wait_for_router(timeout=300):
    """The upstream Type=simple service starts before configuration is loaded."""
    print('Waiting for router configuration...', flush=True)
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        result=subprocess.run(['systemctl','show','vyos-router.service',
            '--property=SubState','--property=ExecMainStatus'],text=True,capture_output=True,check=True)
        state=dict(line.split('=',1) for line in result.stdout.splitlines() if '=' in line)
        if state.get('SubState')=='failed':raise RuntimeError('Router initialization failed')
        if state.get('SubState')=='exited':
            status=Path('/tmp/vyos-config-status')
            if state.get('ExecMainStatus')!='0' or not status.is_file() or status.read_text().strip()!='0':
                raise RuntimeError('Native router configuration was not loaded')
            return
        time.sleep(1)
    raise RuntimeError('Router initialization timed out')


def main():
    factory=Path('/usr/share/shirube/needs-enrollment')
    if not MARKER.exists() and not factory.exists():
        return
    wait_for_router()
    print('Shirube OS — built by michibiki-io\nCreate this instance administrator at the local console.')
    while True:
        username = input('Administrator name: ')
        if not re.fullmatch(r'[a-z][a-z0-9_]{0,30}', username) or username in {'root', 'vyos'}:
            print('Enter a new lowercase administrator name; the name cannot be empty, root or vyos.', flush=True)
            continue
        try:
            pwd.getpwnam(username)
        except KeyError:
            break
        print('That name belongs to an installed system account. Choose another name.', flush=True)
    while True:
        password = getpass.getpass('New instance password (at least 16 characters): ')
        confirmation = getpass.getpass('Repeat password: ')
        if len(password) >= 16 and password == confirmation and not any(c in password for c in '\r\n\0'):
            break
        password = confirmation = None
        print('Passwords must match and have at least 16 characters. Please try again.', flush=True)
    hashed = crypt.crypt(password, crypt.mksalt(crypt.METHOD_SHA512))
    password = confirmation = None
    if not hashed or not hashed.startswith('$6$'):
        raise ValueError('Password hashing failed')
    # Secret/password hash stays out of argv, console output, shell history and logs.
    # Native configuration owns persistent credentials; this caller supplies the
    # hash through private stdin rather than command arguments or diagnostics.
    script = ('printf "SHIRUBE_ENROLL_SOURCE\\n"\n'
              'source /opt/vyatta/etc/functions/script-template || builtin exit 1\n'
              'printf "SHIRUBE_ENROLL_CONFIGURE\\n"\nconfigure || builtin exit 1\n'
              'printf "SHIRUBE_ENROLL_ADMINISTRATOR\\n"\n'
              f"set system login user {username} authentication encrypted-password '{hashed}' || builtin exit 1\n"
              'printf "SHIRUBE_ENROLL_SSH\\n"\nset service ssh || builtin exit 1\n'
              'printf "SHIRUBE_ENROLL_COMMIT\\n"\ncommit || builtin exit 1\n'
              'printf "SHIRUBE_ENROLL_SAVE\\n"\nsave /config/config.boot || builtin exit 1\n'
              'printf "SHIRUBE_ENROLL_DONE\\n"\nexit\n')
    print('Preparing this instance identity...', flush=True)
    subprocess.run(['systemd-machine-id-setup'], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print('Generating this instance SSH host keys...', flush=True)
    subprocess.run(['ssh-keygen', '-A'], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print('Applying administrator configuration...', flush=True)
    # Upstream shell initialization contains optional failing probes. Global
    # errexit terminates it prematurely; check each configuration command instead.
    result = subprocess.run(['/usr/bin/sg', 'vyattacfg', '-c', '/bin/vbash -s'],
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
    saved=subprocess.run(['/usr/libexec/vyos/read-saved-value.py','--path',
        'system','login','user',username,'authentication','encrypted-password'],
        text=True,capture_output=True,check=True)
    if saved.stdout.strip()!=hashed:
        raise RuntimeError('Administrator configuration was not saved')
    pwd.getpwnam(username)
    MARKER.unlink(missing_ok=True)
    factory.unlink(missing_ok=True)
    subprocess.run(['systemctl','--no-block','restart','getty.target'],check=True,
                   stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    print('Instance administrator created. Continue with the normal login prompt.')


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print('Instance enrollment failed. No reusable distribution password is available; retry at the console.')
        raise SystemExit(1)
