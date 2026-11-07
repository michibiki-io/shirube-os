"""Native QEMU installation and access; KVM default, explicit TCG probe support."""
import base64
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import subprocess
import time
from pexpect import EOF, TIMEOUT
from pexpect.fdpexpect import fdspawn
from .architecture import native
from .disks import convert
from .sources import sha256
from .sanitize import audit, sanitize


class VM:
    def __init__(self, arch, disk, work, iso=None, accelerator='kvm'):
        native(arch);self.work=Path(work);self.work.mkdir()
        if accelerator not in {'kvm','tcg'}:raise ValueError('Unsupported QEMU accelerator')
        self.accelerator=accelerator
        self.disk=Path(disk);self.iso=iso;self.arch=arch
        options=([('/usr/share/AAVMF/AAVMF_CODE.fd','/usr/share/AAVMF/AAVMF_VARS.fd')] if arch=='arm64' else
                 [('/usr/share/OVMF/OVMF_CODE_4M.fd','/usr/share/OVMF/OVMF_VARS_4M.fd'),('/usr/share/OVMF/OVMF_CODE.fd','/usr/share/OVMF/OVMF_VARS.fd')])
        firmware=next(((Path(c),Path(v)) for c,v in options if Path(c).is_file() and Path(v).is_file()),None)
        if not firmware:raise ValueError('Architecture-specific UEFI firmware missing')
        code,variables=firmware;nvram=self.work/'nvram.fd';shutil.copy2(variables,nvram)
        if sha256(nvram)!=sha256(variables):raise ValueError('NVRAM is not fresh')
        executable='qemu-system-aarch64' if arch=='arm64' else 'qemu-system-x86_64'
        machine='virt' if arch=='arm64' else 'q35'
        serial=self.work/'serial.sock'
        mac='52:54:'+':'.join(f'{byte:02x}' for byte in secrets.token_bytes(4))
        cpu='host' if accelerator=='kvm' else ('cortex-a72' if arch=='arm64' else 'max')
        accel='kvm' if accelerator=='kvm' else 'tcg,thread=multi'
        args=[executable,'-machine',machine,'-accel',accel,'-cpu',cpu,'-smp','2','-m','2048','-display','none',
              '-serial',f'unix:{serial},server=on,wait=on','-qmp',f'unix:{self.work}/qmp.sock,server=on,wait=off',
              '-drive',f'if=pflash,format=raw,unit=0,readonly=on,file={code}',
              '-drive',f'if=pflash,format=raw,unit=1,file={nvram}',
              '-drive',f'if=none,id=osdisk,format=qcow2,file={disk}',
              '-device','virtio-blk-pci,drive=osdisk,bootindex=1',
              '-netdev','user,id=net0','-device',f'virtio-net-pci,netdev=net0,mac={mac},romfile=']
        if iso:
            args+=['-device','virtio-scsi-pci,id=scsi0','-drive',f'if=none,id=cd0,format=raw,media=cdrom,readonly=on,file={iso}',
                   '-device','scsi-cd,drive=cd0,bus=scsi0.0,bootindex=0']
        self.log=(self.work/'qemu.log').open('wb')
        self.process=subprocess.Popen(args,stdout=self.log,stderr=subprocess.STDOUT)
        deadline=time.monotonic()+15
        while not serial.exists():
            if self.process.poll() is not None or time.monotonic()>deadline:
                self.close()
                raise RuntimeError('QEMU failed to start with '+accelerator)
            time.sleep(.1)
        self.socket=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);self.socket.connect(str(serial))
        self.child=fdspawn(self.socket.fileno(),encoding='utf-8',codec_errors='replace',timeout=1200 if accelerator=='tcg' else 300)
        self.child.logfile=None
        self.username=None;self.password=None

    def enrollment_prompt(self):
        self.child.expect('Administrator name:')

    def enroll(self):
        self.enrollment_prompt();self.username='sbtest'+secrets.token_hex(4);self.password=secrets.token_urlsafe(24)
        self.child.sendline(self.username);self.child.expect('New instance password');self.child.sendline(self.password)
        self.child.expect('Repeat password:');self.child.sendline(self.password)
        self.child.expect('Instance administrator created.')
        self.login()

    def login(self):
        self.child.expect('login:');self.child.sendline(self.username);self.child.expect('Password:');self.child.sendline(self.password)
        self.prompt=re.escape(self.username)+r'@[^\r\n]+[#$] '
        self.child.expect(self.prompt);self.child.sendline('stty -echo');self.child.expect(self.prompt)

    def command(self, command, timeout=180):
        return self._command(command,timeout)

    def secret_command(self, command, prompt, secret, timeout=180):
        """Deliver a secret only after a program's hidden-input prompt."""
        return self._command(command,timeout,(prompt,secret))

    def _command(self, command, timeout, secret_input=None):
        start='START_'+secrets.token_hex(12);end='END_'+secrets.token_hex(12)
        script="printf '\\n"+start+"\\n'; "+command+"; printf '\\n"+end+"%s\\n' \"$?\""
        encoded=base64.b64encode(script.encode()).decode()
        self.child.sendline('eval "$(printf %s '+encoded+' | base64 -d)"')
        self.child.expect(start)
        if secret_input is not None:
            prompt,secret=secret_input
            self.child.expect_exact(prompt,timeout=timeout);self.child.sendline(secret)
        self.child.expect(end+r'(\d+)',timeout=timeout)
        data=self.child.before;status=int(self.child.match.group(1));self.child.expect(self.prompt)
        if status:raise RuntimeError('Private guest command failed')
        return re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]','',data).strip()

    def reboot(self):
        self.child.sendline('sudo -n systemctl reboot --no-block')
        self.login()

    def install(self):
        self.child.sendline('install image')
        password=secrets.token_urlsafe(24)
        steps=[(r'Would you like to continue.*','y'),(r'What would you like to name this image.*',''),
               (r'Please enter a password.*',password),(r'Please confirm password.*',password),
               (r'What console should be used.*','S'),
               (r'Which one should be used for installation.*Default: /dev/vda','/dev/vda'),
               (r'Installation will delete all data.*','y'),(r'Would you like to use all the free space.*','y'),
               (r'Which file would you like as boot config.*','1')]
        for prompt,answer in steps:self.child.expect(prompt);self.child.sendline(answer)
        self.child.expect('The image installed successfully; please reboot now.',timeout=600);self.child.expect(self.prompt)
        return password

    def poweroff(self):
        self.child.sendline('sudo -n systemctl poweroff --no-block')
        try:self.process.wait(timeout=120)
        except subprocess.TimeoutExpired:raise RuntimeError('Guest did not shut down cleanly') from None
        self.close()

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:self.process.wait(timeout=15)
            except subprocess.TimeoutExpired:self.process.kill();self.process.wait()
        if hasattr(self,'socket'):self.socket.close()
        self.log.close()
        self.password=None


def offline_cleanup(disk,work,arch):
    import guestfs
    os.environ['LIBGUESTFS_BACKEND']='direct'
    os.environ['LIBGUESTFS_BACKEND_SETTINGS']='force_kvm'
    g=guestfs.GuestFS(python_return_dict=True)
    g.add_drive_opts(str(disk),format='qcow2',discard='enable');g.launch()
    try:
        partitions=g.list_partitions()
        if len(partitions)!=3 or g.vfs_type('/dev/sda3')!='ext4':raise ValueError('Unexpected installed disk layout')
        g.mount('/dev/sda3','/')
        images=g.glob_expand('/boot/*/*.squashfs')
        if len(images)!=1:raise ValueError('Expected one installed squashfs')
        parent=str(Path(images[0]).parent)
        for path in [parent+'/rw',parent+'/work','/config','/opt/vyatta/etc/config']:
            if g.exists(path):g.rm_rf(path)
        for path in [parent+'/rw',parent+'/work']:g.mkdir_p(path)
        source=Path(work)/'sanitized.squashfs';g.download(images[0],str(source))
        root=Path(work)/'offline-root'
        subprocess.run(['unsquashfs','-d',str(root),str(source)],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        sanitize(root,arch);audit(root)
        rebuilt=Path(work)/'final.squashfs'
        subprocess.run(['mksquashfs',str(root),str(rebuilt),'-noappend','-comp','xz','-processors','2','-mem','512M','-no-progress'],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        g.upload(str(rebuilt),images[0])
        g.umount_all()
        # Clear the old journal before zeroing free blocks. Recreate an empty
        # journal afterwards. The appliance sees only this task-owned disk.
        features=g.tune2fs_l('/dev/sda3').get('Filesystem features','')
        if 'inline_data' in features:raise ValueError('Inline inode data requires additional erasure validation')
        g.debug('sh',['e2fsck -fy /dev/sda3'])
        g.debug('sh',['tune2fs -O ^has_journal /dev/sda3'])
        g.mount('/dev/sda3','/');g.zero_free_space('/');g.fstrim('/');g.umount_all()
        g.debug('sh',['tune2fs -j /dev/sda3']);g.debug('sh',['e2fsck -fy /dev/sda3'])
    finally:g.shutdown();g.close()


def build_disks(iso,arch,version,work,output,netbird_verify=None):
    work=Path(work);work.mkdir();disk=work/'fresh.qcow2'
    subprocess.run(['qemu-img','create','-f','qcow2',str(disk),'50G'],check=True,stdout=subprocess.DEVNULL)
    live=VM(arch,disk,work/'live',iso)
    try:
        live.enroll();live_user=live.username;live_password=live.password
        installer_password=live.install();live.poweroff()
    finally:live.close()
    installed=VM(arch,disk,work/'installed')
    try:
        installed.enroll()
        installed.command('sudo -n mkdir /tmp/shirube-export; sudo -n touch /run/shirube-private-export-session')
        for source,name in [('/src/producer/sanitize.py','sanitize.py'),('/src/guest/export_prepare.py','prepare.py')]:
            data=base64.b64encode(Path(source).read_bytes()).decode()
            # Transfer in bounded commands; never transfer a password or key.
            installed.command('sudo -n touch /tmp/shirube-export/'+name+'.b64')
            for offset in range(0,len(data),512):
                installed.command('printf %s '+data[offset:offset+512]+' | sudo -n tee -a /tmp/shirube-export/'+name+'.b64 >/dev/null')
            installed.command('sudo -n sh -c "base64 -d /tmp/shirube-export/'+name+'.b64 > /tmp/shirube-export/'+name+'"')
        if 'SHIRUBE_ROOT_REPACKED' not in installed.command('sudo -n python3 /tmp/shirube-export/prepare.py '+arch,timeout=1200):
            raise ValueError('Root sanitization/repacking did not finish')
        installed.process.wait(timeout=120)
        installed.close()
        live_password=installer_password=None
    finally:installed.close()
    offline_cleanup(disk,work,arch)
    converted=convert(disk,work/'converted')
    images={};identities=[];observations=[];netbird_results=[]
    for image in converted:
        target=Path(output)/f'shirube-os-{version}-{arch}{image.suffix}';shutil.copy2(image,target)
        images[image.suffix.removeprefix('.')]=target
        before=sha256(target)
        for provisioned in [False,True]:
            name=image.suffix.removeprefix('.')+('-provisioned' if provisioned else '-unprovisioned')
            clone=work/(name+'.qcow2')
            subprocess.run(['qemu-img','create','-f','qcow2','-F',image.suffix.removeprefix('.'),'-b',str(target),str(clone)],check=True,stdout=subprocess.DEVNULL)
            vm=VM(arch,clone,work/name)
            try:
                if not provisioned:
                    vm.enrollment_prompt();observations.append(name+': fresh firmware, no ISO, enrollment prompt with no reusable login')
                else:
                    vm.enroll()
                    actual=vm.command('uname -m')
                    if actual not in ({'aarch64','arm64'} if arch=='arm64' else {'x86_64','amd64'}):raise ValueError('Wrong guest architecture')
                    routes=json.loads(vm.command('ip -j -4 route'));addresses=json.loads(vm.command('ip -j -4 addr show eth0'))
                    if not any(x.get('dst')=='default' and x.get('gateway')=='10.0.2.2' for x in routes):raise ValueError('Default route missing')
                    if not any(a.get('dynamic') for x in addresses for a in x.get('addr_info',[])):raise ValueError('DHCP lease missing')
                    if not vm.command('readlink /sys/class/net/eth0/device/driver').endswith('/virtio_net'):raise ValueError('Wrong NIC driver')
                    identity=(vm.command('cat /etc/machine-id'),vm.command('cat /etc/ssh/ssh_host_ed25519_key.pub'))
                    if not all(identity):raise ValueError('New machine/SSH identity missing')
                    identities.append(identity);observations.append(name+': actual console login, sudo, DHCP, route, virtio NIC and identity checks passed')
                    version_text=vm.command('show version')
                    if version[1:] not in version_text or not re.search(r'Version:\s+Shirube OS',version_text):raise ValueError('Wrong installed product identity/version')
                    if netbird_verify is not None:
                        proof=netbird_verify(vm)
                        if not proof or any(value!='PASS' for value in proof.values()):
                            raise ValueError('Guest NetBird lifecycle proof incomplete')
                        netbird_results.append(proof)
                    vm.poweroff()
            finally:vm.close()
        if sha256(target)!=before:raise ValueError('Boot tests modified an immutable export')
    if identities[0][0]==identities[1][0] or identities[0][1]==identities[1][1]:raise ValueError('Per-instance identities were reused')
    return {'images':images,'observations':observations,'identity_regeneration':'PASS',
            'netbird':'PASS' if len(netbird_results)==2 else 'NOT_RUN'}
