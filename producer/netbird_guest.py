"""Real guest configuration/lifecycle checks; run only on a disposable clone."""
import base64
import json
from pathlib import Path
import shlex
import time
from urllib.parse import urlsplit


class Management:
    """Ephemeral fixture access passed privately by the host orchestrator."""
    def __init__(self, payload):
        from .netbird import validate
        import ipaddress
        validate({'management_url':payload['url']})
        if not ipaddress.ip_address(payload['address']).is_private:
            raise ValueError('A local fixture address is required')
        self.url=payload['url'];self.address=payload['address']
        self.token=payload['token'];self.certificate=Path('/fixture/cert.pem')
        if not isinstance(self.token,str) or not self.token or not self.certificate.is_file():
            raise ValueError('Private fixture authorization and public CA are required')

    def verify(self, vm):
        import ssl
        import urllib.request
        body={'name':'Ephemeral guest acceptance key','type':'one-off','expires_in':3600,
              'usage_limit':1,'auto_groups':[],'ephemeral':False}
        request=urllib.request.Request(self.url+'/api/setup-keys',data=json.dumps(body).encode(),
            headers={'Content-Type':'application/json','Authorization':'Token '+self.token})
        try:
            with urllib.request.urlopen(request,context=ssl.create_default_context(cafile=str(self.certificate)),timeout=15) as response:
                key=json.load(response)['key']
        except Exception:
            raise RuntimeError('Local guest fixture key issuance failed') from None
        hostname=urlsplit(self.url).hostname
        # The QEMU user network need not forward Docker's embedded DNS. This is
        # public fixture routing data, installed in a disposable clone only.
        vm.command('printf %s '+shlex.quote(self.address+' '+hostname+'\n')+
                   ' | sudo -n tee -a /etc/hosts >/dev/null')
        return verify(vm,self.url,self.certificate,key)


def configure(vm, commands):
    vm.child.sendline('configure');vm.child.expect(vm.prompt)
    for command in commands:vm.command(command)
    vm.command('commit',timeout=180);vm.command('save')
    vm.child.sendline('exit');vm.child.expect(vm.prompt)


def connected(vm):
    deadline=time.monotonic()+120
    while True:
        status=json.loads(vm.command('sudo -n /usr/libexec/shirube/netbird status --json'))
        if status.get('management',{}).get('connected') and status.get('signal',{}).get('connected'):
            if not status.get('publicKey'):raise ValueError('Connected guest has no peer identity')
            return status['publicKey']
        if time.monotonic()>=deadline:raise RuntimeError('Guest NetBird connectivity timed out')
        time.sleep(2)


def inactive(vm):
    vm.command('test "$(sudo -n systemctl is-active shirube-netbird.service)" = inactive')


def verify(vm, url, certificate, key):
    """Exercise native OS commands, keeping setup material out of shell input."""
    from .netbird import validate
    validate({'management_url':url})
    inactive(vm)
    # The fixture CA is public and lives only in this disposable test clone.
    certificate=base64.b64encode(Path(certificate).read_bytes()).decode()
    vm.command('printf %s '+certificate+' | base64 -d | sudo -n tee /usr/local/share/ca-certificates/shirube-fixture.crt >/dev/null')
    vm.command('sudo -n update-ca-certificates >/dev/null')
    configure(vm,['set service netbird management-url '+shlex.quote(url)])
    inactive(vm)
    vm.secret_command('generate netbird enrollment','NetBird one-shot enrollment key: ',key,timeout=150)
    identity=connected(vm)
    vm.command('show netbird')
    scan='''import getpass
from pathlib import Path
key=getpass.getpass('Secret scan input: ').encode()
assert key
assert not Path('/run/shirube-netbird/enrollment-key').exists()
assert Path('/config/config.boot').is_file()
for root in ['/config','/home','/root','/var/log']:
 for path in Path(root).rglob('*'):
  if path.is_file() and not path.is_symlink():
   with path.open('rb') as stream:
    tail=b''
    while chunk:=stream.read(1024*1024):
     assert key not in tail+chunk
     tail=chunk[-len(key):]
print('SECRET_ABSENT')
'''
    scan_command='sudo -n python3 -c '+shlex.quote(scan)
    if 'SECRET_ABSENT' not in vm.secret_command(scan_command,'Secret scan input: ',key):
        raise ValueError('Guest secret audit did not complete')
    configure(vm,['set service netbird disable']);inactive(vm)
    vm.command('sudo -n test -f /config/netbird/enrolled.json')
    configure(vm,['delete service netbird disable'])
    if connected(vm)!=identity:raise ValueError('Disable/enable changed the peer identity')
    vm.reboot()
    if connected(vm)!=identity:raise ValueError('Saved configuration reboot changed the peer identity')
    vm.command('show netbird')
    if 'SECRET_ABSENT' not in vm.secret_command(scan_command,'Secret scan input: ',key):
        raise ValueError('Post-reboot secret audit did not complete')
    configure(vm,['delete service netbird']);inactive(vm)
    vm.command('sudo -n test ! -e /run/shirube-netbird/settings.json && sudo -n test -f /config/netbird/enrolled.json')
    vm.reboot();inactive(vm)
    return {'native_config_commit_save':'PASS','real_enrollment':'PASS',
        'management_and_signal':'PASS','disable_enable_identity':'PASS',
        'saved_configuration_reboot_identity':'PASS','removed_configuration_reboot_inactive':'PASS',
        'setup_key_absence_in_config_state_history_logs':'PASS'}
