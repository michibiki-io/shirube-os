"""Real NoCloud boot, SSH-only access and reboot persistence on disposable clones."""
import base64
import json
from pathlib import Path
import secrets
import shutil
import subprocess
import threading
import time
from pexpect import EOF, TIMEOUT


def monitor_console(vm):
    """Drain the serial socket while SSH drives this guest; retain no raw log."""
    stop=threading.Event()
    state={'enrollment_prompt':False}
    def read():
        tail=''
        while not stop.is_set():
            try:
                chunk=vm.child.read_nonblocking(4096,timeout=1)
                tail=(tail+chunk)[-1024:]
                if 'Administrator name:' in tail:state['enrollment_prompt']=True
            except TIMEOUT:continue
            except (EOF,OSError):break
    thread=threading.Thread(target=read,daemon=True);thread.start()
    return stop,thread,state


def verify(image, arch, work, accelerator):
    from .vm import VM
    work=Path(work);work.mkdir(mode=0o700)
    key=work/'instance-key'
    subprocess.run(['ssh-keygen','-q','-t','ed25519','-N','','-f',str(key)],check=True)
    public=(work/'instance-key.pub').read_text().strip()
    seed=work/'seed';seed.mkdir()
    (seed/'meta-data').write_text('instance-id: shirube-test-'+secrets.token_hex(16)+
        '\nlocal-hostname: shirube-cloud-test\npublic-keys:\n  - '+json.dumps(public)+'\n')
    (seed/'user-data').write_text('#cloud-config\nusers:\n  - default\n  - name: cloud-admin\n'
        '    ssh_authorized_keys:\n      - '+json.dumps(public)+'\n'
        'vyos_config_commands:\n  - "set system host-name \'shirube-native-command-test\'"\n'
        'write_files:\n  - path: /opt/vyatta/etc/config/shirube-cloud-init-test\n'
        '    permissions: "0600"\n    content: "native-cloud-config-pass"\n')
    media=work/'seed.iso'
    subprocess.run(['xorriso','-as','mkisofs','-quiet','-V','cidata','-J','-r','-o',str(media),str(seed)],
        check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    clone=work/'instance.qcow2'
    subprocess.run(['qemu-img','create','-f','qcow2','-F',image.suffix.removeprefix('.'),
        '-b',str(image),str(clone)],check=True,stdout=subprocess.DEVNULL)
    vm=None;monitor=None
    try:
        vm=VM(arch,clone,work/'boot',accelerator=accelerator,cloud_seed=media)
        monitor=monitor_console(vm)
        ssh=['ssh','-i',str(key),'-p',str(vm.ssh_port),'-o','BatchMode=yes',
             '-o','ConnectTimeout=5','-o','StrictHostKeyChecking=accept-new',
             '-o','ServerAliveInterval=5','-o','ServerAliveCountMax=2',
             '-o','UserKnownHostsFile='+str(work/'known-hosts')]
        last_phase=None
        def command(user, text, phase):
            nonlocal last_phase
            if phase!=last_phase:
                print('CLOUD_INIT_SSH_PHASE: '+phase,flush=True)
                last_phase=phase
            try:
                return subprocess.run(ssh+[user+'@127.0.0.1',text],text=True,capture_output=True,
                                      timeout=180 if accelerator=='tcg' else 30)
            except subprocess.TimeoutExpired:
                # Exception strings can contain full argv and captured input.
                # Fixed operation names identify the failure without that data.
                print('CLOUD_INIT_SSH_TIMEOUT: '+phase,flush=True)
                raise
        check="""from pathlib import Path
from vyos.configtree import ConfigTree
assert Path('/config/shirube-enrolled').read_text().strip() == 'cloud-admin'
assert not Path('/config/shirube-first-boot').exists()
assert not Path('/usr/share/shirube/needs-enrollment').exists()
assert Path('/config/shirube-cloud-init-test').read_text().strip() == 'native-cloud-config-pass'
c=ConfigTree(Path('/config/config.boot').read_text())
assert c.return_value(['system','host-name']) == 'shirube-native-command-test'
for user in ['cloud-admin','shirube']:
    p=['system','login','user',user,'authentication','public-keys']
    assert c.exists(p) and c.list_nodes(p)
print(Path('/etc/machine-id').read_text().strip())
print(Path('/etc/ssh/ssh_host_ed25519_key.pub').read_text().strip())
print(Path('/proc/sys/kernel/random/boot_id').read_text().strip())
"""
        encoded=base64.b64encode(check.encode()).decode()
        probe='sudo -n python3 -c '+"\"import base64; exec(base64.b64decode('"+encoded+"'))\""
        def wait_access(previous_boot_id=None, on_same_boot=None):
            deadline=time.monotonic()+(1200 if accelerator=='tcg' else 300)
            phase='post_reboot' if previous_boot_id is not None else 'initial'
            while time.monotonic()<deadline:
                if monitor[2]['enrollment_prompt']:
                    raise ValueError('Cloud-init unexpectedly requested console enrollment')
                try:
                    result=command('cloud-admin',probe,phase+'_admin_access')
                except subprocess.TimeoutExpired:
                    continue
                if result.returncode==0:
                    identity=result.stdout.strip().splitlines()
                    if len(identity)!=3 or not all(identity):raise ValueError('Cloud identity proof missing')
                    if identity[2]==previous_boot_id:
                        if on_same_boot is not None:on_same_boot(deadline)
                        time.sleep(3)
                        continue
                    try:
                        metadata=command('shirube','true',phase+'_metadata_access')
                    except subprocess.TimeoutExpired:
                        continue
                    if metadata.returncode:
                        time.sleep(3)
                        continue
                    return identity
                if vm.process.poll() is not None:raise RuntimeError('Cloud-init guest exited before access')
                time.sleep(3)
            raise RuntimeError('Cloud-init SSH access/native configuration did not pass before deadline')
        def request_transition(text, phase):
            try:
                result=command('cloud-admin',text,phase)
            except subprocess.TimeoutExpired:
                # Shutdown can interrupt SSH before it acknowledges the request.
                # Never expose the exception's argv or captured guest output.
                print('CLOUD_INIT_TRANSITION_UNCERTAIN: '+phase,flush=True)
                return False
            if result.returncode:
                # Fixed categories aid diagnosis without retaining guest text.
                stderr=(result.stderr or '').lower()
                categories=[name for name,needle in [
                    ('broken_pipe','broken pipe'),
                    ('connection_closed_by_remote','closed by remote host'),
                    ('kex','kex_exchange_identification'),
                    ('command_not_found','command not found'),
                ] if needle in stderr]
                print('CLOUD_INIT_SSH_RESULT: '+phase+' exit='+str(result.returncode)+
                      ' category='+(','.join(categories) or 'other'),flush=True)
                if result.returncode==255:
                    print('CLOUD_INIT_TRANSITION_UNCERTAIN: '+phase,flush=True)
                    return False
                raise RuntimeError('Cloud-init '+phase+' failed (exit '+str(result.returncode)+')')
            return True
        original=wait_access()
        # SSH was accepted without touching the console or providing a password.
        vm.eject_cloud_seed()
        reboot_command='sudo -n systemctl reboot --no-block'
        acknowledged=request_transition(reboot_command,'reboot_request')
        retries=0
        def retry_unaccepted_reboot(deadline):
            nonlocal acknowledged,retries
            if acknowledged or retries>=2:return
            # The access probe has just proved that the original boot is still
            # serving SSH. Retry only if systemd also proves no reboot is pending.
            try:
                state=command('cloud-admin',
                    'sudo -n systemctl show reboot.target --property=ActiveState --property=Job',
                    'reboot_pending_check')
            except subprocess.TimeoutExpired:
                return
            if state.returncode:return
            lines=state.stdout.strip().splitlines()
            if len(lines)!=2:return
            properties=dict(line.split('=',1) for line in lines if '=' in line)
            if (properties.get('ActiveState')!='inactive'
                    or properties.get('Job') not in {'','0'}):return
            if time.monotonic()>=deadline:return
            retries+=1
            acknowledged=request_transition(reboot_command,'reboot_retry')
        if wait_access(previous_boot_id=original[2],on_same_boot=retry_unaccepted_reboot)[:2]!=original[:2]:
            raise ValueError('Cloud administrator identity changed on reboot')
        request_transition('sudo -n systemctl poweroff --no-block',
                        'shutdown_request')
        # Do not terminate a guest that is still performing graceful shutdown.
        # The request's acknowledgement (or lost transport) is not exit proof.
        try:
            shutdown_status=vm.process.wait(timeout=180 if accelerator=='tcg' else 30)
        except subprocess.TimeoutExpired:
            raise RuntimeError('Cloud-init shutdown did not finish before deadline') from None
        if shutdown_status!=0:
            raise RuntimeError('Cloud-init guest failed during shutdown')
        return 'PASS'
    finally:
        if monitor is not None:
            monitor[0].set();monitor[1].join(timeout=2)
        if vm is not None:vm.close()
        # Only disposable test state is removed; the immutable export is never
        # mounted writable. No seed, authorized key or login identity is shipped.
        key.unlink(missing_ok=True)
        (work/'instance-key.pub').unlink(missing_ok=True)
        (work/'known-hosts').unlink(missing_ok=True)
        media.unlink(missing_ok=True)
        shutil.rmtree(seed)
        clone.unlink(missing_ok=True)
