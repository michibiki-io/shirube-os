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
ENROLLED = Path('/config/shirube-enrolled')


def configured_administrators():
    """Inspect native persisted authentication after the router has applied it."""
    from vyos.configtree import ConfigTree
    config = ConfigTree(Path('/config/config.boot').read_text())
    users = ['system', 'login', 'user']
    result = []
    if not config.exists(users):
        return result
    for username in config.list_nodes(users):
        if not re.fullmatch(r'[a-z][a-z0-9_]{0,30}', username) or username in {'root', 'vyos'}:
            continue
        auth = users+[username, 'authentication']
        hashed = config.return_value(auth+['encrypted-password']) if config.exists(auth+['encrypted-password']) else ''
        keys = auth+['public-keys']
        has_key = config.exists(keys) and any(
            config.exists(keys+[key, 'key']) and config.return_value(keys+[key, 'key'])
            and config.exists(keys+[key, 'type']) and config.return_value(keys+[key, 'type'])
            for key in config.list_nodes(keys))
        if (hashed and not hashed.startswith(('!', '*'))) or has_key:
            try:
                account = pwd.getpwnam(username)
            except KeyError:
                continue
            if account.pw_uid >= 1000 and account.pw_shell not in {'/bin/false', '/usr/sbin/nologin'}:
                result.append(username)
    return sorted(result)


def cloud_administrator():
    # The native router is ordered after cloud-config.service, so it has
    # already applied configuration before this check. Never wait on
    # cloud-init.target: that target is ordered after multi-user.target.
    state = subprocess.run(['systemctl', 'show', 'cloud-config.service',
        '--property=Result', '--property=ExecMainStatus', '--property=SubState'],
        text=True, capture_output=True, check=True)
    properties = dict(line.split('=', 1) for line in state.stdout.splitlines() if '=' in line)
    if (properties.get('SubState') != 'exited' or properties.get('Result') != 'success'
            or properties.get('ExecMainStatus') != '0'):
        return None
    users = configured_administrators()
    return users[0] if users else None


def finish_enrollment(factory, username):
    subprocess.run(['systemd-machine-id-setup'], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(['ssh-keygen', '-A'], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    ENROLLED.write_text(username+'\n')
    ENROLLED.chmod(0o600)
    MARKER.unlink(missing_ok=True)
    factory.unlink(missing_ok=True)
    enable_logins()


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



def saved_password(username):
    result=subprocess.run(['/usr/libexec/vyos/read-saved-value.py','--path',
        'system','login','user',username,'authentication','encrypted-password'],
        text=True,capture_output=True,check=True)
    return result.stdout.strip()


def enable_logins():
    subprocess.run(['systemctl','--no-block','restart','getty.target'],check=True,
                   stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)


def main():
    factory=Path('/usr/share/shirube/needs-enrollment')
    if not MARKER.exists() and not factory.exists():
        return
    wait_for_router()
    if ENROLLED.is_file():
        username=ENROLLED.read_text().strip()
        if not re.fullmatch(r'[a-z][a-z0-9_]{0,30}',username) or username in {'root','vyos'}:
            raise RuntimeError('Invalid saved enrollment state')
        if username not in configured_administrators():
            raise RuntimeError('Saved administrator authentication is unavailable')
        pwd.getpwnam(username)
        MARKER.unlink(missing_ok=True)
        factory.unlink(missing_ok=True)
        enable_logins()
        print('Saved administrator verified. Continue with the normal login prompt.',flush=True)
        return
    username = cloud_administrator()
    if username:
        finish_enrollment(factory, username)
        print('Cloud administrator verified. Continue with the configured authentication.', flush=True)
        return
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
    if saved_password(username)!=hashed:
        raise RuntimeError('Administrator configuration was not saved')
    pwd.getpwnam(username)
    ENROLLED.write_text(username+'\n')
    ENROLLED.chmod(0o600)
    MARKER.unlink(missing_ok=True)
    factory.unlink(missing_ok=True)
    enable_logins()
    print('Instance administrator created. Continue with the normal login prompt.')


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print('Instance enrollment failed. No reusable distribution password is available; retry at the console.')
        raise SystemExit(1)
