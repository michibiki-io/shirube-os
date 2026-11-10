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
from .guest_diagnostics import GuestCommandError


FIRMWARE = {
    'arm64': [('/usr/share/AAVMF/AAVMF_CODE.fd', '/usr/share/AAVMF/AAVMF_VARS.fd')],
    'amd64': [('/usr/share/OVMF/OVMF_CODE_4M.fd', '/usr/share/OVMF/OVMF_VARS_4M.fd'),
              ('/usr/share/OVMF/OVMF_CODE.fd', '/usr/share/OVMF/OVMF_VARS.fd')],
}


def prepare_firmware(arch, mode, work):
    """Select explicit firmware; AMD64 BIOS never attaches EFI flash/state."""
    if arch not in FIRMWARE or mode not in {'uefi', 'bios'}:
        raise ValueError('Explicit supported architecture/firmware required')
    if mode == 'bios':
        if arch != 'amd64':
            raise ValueError('Legacy BIOS is supported only on AMD64')
        return []
    firmware = next(((Path(c), Path(v)) for c, v in FIRMWARE[arch]
                     if Path(c).is_file() and Path(v).is_file()), None)
    if not firmware:
        raise ValueError('Architecture-specific UEFI firmware missing')
    code, variables = firmware
    nvram = Path(work)/'nvram.fd'
    shutil.copy2(variables, nvram)
    if sha256(nvram) != sha256(variables):
        raise ValueError('NVRAM is not fresh')
    return ['-drive', f'if=pflash,format=raw,unit=0,readonly=on,file={code}',
            '-drive', f'if=pflash,format=raw,unit=1,file={nvram}']


class VM:
    def __init__(self, arch, disk, work, iso=None, accelerator='kvm', boot_console='serial',
                 firmware_mode='uefi', cloud_seed=None, ssh_forward=False):
        native(arch);self.work=Path(work);self.work.mkdir()
        if accelerator not in {'kvm','tcg'}:raise ValueError('Unsupported QEMU accelerator')
        if boot_console not in {'serial','local'}:raise ValueError('Unsupported boot console')
        self.accelerator=accelerator
        self.disk=Path(disk);self.iso=iso;self.arch=arch
        self.cloud_seed=cloud_seed
        firmware_args=prepare_firmware(arch,firmware_mode,self.work)
        self.firmware_mode=firmware_mode
        executable='qemu-system-aarch64' if arch=='arm64' else 'qemu-system-x86_64'
        machine='virt' if arch=='arm64' else 'q35'
        serial=self.work/'serial.sock'
        mac='52:54:'+':'.join(f'{byte:02x}' for byte in secrets.token_bytes(4))
        cpu='host' if accelerator=='kvm' else ('cortex-a72' if arch=='arm64' else 'max')
        # Bound translated-code memory as well as guest RAM. QEMU's host-sized
        # default cache can exhaust a 3 GiB container during a live install.
        accel='kvm' if accelerator=='kvm' else 'tcg,thread=multi,tb-size=128'
        network='user,id=net0'
        self.ssh_port=None
        if cloud_seed is not None or ssh_forward:
            with socket.socket() as reservation:
                reservation.bind(('127.0.0.1',0));self.ssh_port=reservation.getsockname()[1]
            network+=f',hostfwd=tcp:127.0.0.1:{self.ssh_port}-:22'
        args=[executable,'-machine',machine,'-accel',accel,'-cpu',cpu,'-smp','2','-m','2048','-display','none',
              '-serial',f'unix:{serial},server=on,wait=on','-qmp',f'unix:{self.work}/qmp.sock,server=on,wait=off',
              *firmware_args,
              '-drive',f'if=none,id=osdisk,format=qcow2,file={disk}',
              '-device','virtio-blk-pci,drive=osdisk,bootindex=1',
              '-netdev',network,'-device',f'virtio-net-pci,netdev=net0,mac={mac},romfile=']
        if cloud_seed is not None:
            args+=['-drive',f'if=none,id=seed,format=raw,media=cdrom,readonly=on,file={cloud_seed}',
                   '-device','virtio-scsi-pci,id=seedscsi', '-device','scsi-cd,drive=seed,bus=seedscsi.0']
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
        if iso and boot_console=='serial':
            try:
                self.child.expect(r'Live \(serial console\)',timeout=120 if accelerator=='tcg' else 30)
                self.child.send('s')
            except Exception:
                self.close()
                raise

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
        if not isinstance(secret,str) or any(char in secret for char in '\r\n\0'):
            raise ValueError('Hidden console input must be a single line')
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
        if status:raise GuestCommandError(status,data)
        return re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]','',data).strip()

    def reboot(self):
        self.child.sendline('sudo -n systemctl reboot --no-block')
        self.login()

    def eject_cloud_seed(self):
        """Remove only this test VM's metadata CD before persistence checks."""
        if self.cloud_seed is None:raise ValueError('No metadata seed is attached')
        with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as peer:
            peer.settimeout(10);peer.connect(str(self.work/'qmp.sock'))
            with peer.makefile('rwb',buffering=0) as channel:
                if 'QMP' not in json.loads(channel.readline()):raise RuntimeError('Missing QMP greeting')
                for identifier,command in enumerate([
                    {'execute':'qmp_capabilities'},
                    {'execute':'eject','arguments':{'device':'seed','force':True}}]):
                    channel.write((json.dumps({**command,'id':identifier})+'\n').encode())
                    while True:
                        response=json.loads(channel.readline())
                        if response.get('id')==identifier:break
                    if 'return' not in response:raise RuntimeError('Could not eject metadata seed')
        self.cloud_seed=None

    def install(self):
        self.child.sendline('install image')
        steps=[(r'Would you like to continue.*','y'),(r'What would you like to name this image.*',''),
               (r'What console should be used.*','S'),
               (r'Which one should be used for installation.*Default: /dev/vda','/dev/vda'),
               (r'Installation will delete all data.*','y'),(r'Would you like to use all the free space.*','y'),
               (r'Which file would you like as boot config.*','1')]
        for prompt,answer in steps:self.child.expect(prompt);self.child.sendline(answer)
        self.child.expect('The image installed successfully; please reboot now.',timeout=600);self.child.expect(self.prompt)
        return None

    def poweroff(self):
        self.child.sendline('sudo -n systemctl poweroff --no-block')
        self.wait_for_poweroff()

    def wait_for_poweroff(self):
        try:
            # Drain serial output while shutdown proceeds; an unread socket can
            # block the guest console and prevent QEMU from terminating.
            self.child.expect(EOF,timeout=600 if self.accelerator=='tcg' else 120)
            self.process.wait(timeout=30)
        except (TIMEOUT,subprocess.TimeoutExpired):
            raise RuntimeError('Guest did not shut down cleanly') from None
        self.close()

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:self.process.wait(timeout=15)
            except subprocess.TimeoutExpired:self.process.kill();self.process.wait()
        if hasattr(self,'socket'):self.socket.close()
        self.log.close()
        self.password=None


def require_factory_layer(guestfs, image, expected_sha256):
    """Reject a modified installed base before discarding the runtime overlay."""
    if not re.fullmatch(r'[0-9a-f]{64}',expected_sha256):
        raise ValueError('A factory root SHA256 is required')
    if guestfs.checksum('sha256',image)!=expected_sha256:
        raise ValueError('Installed immutable root differs from the input ISO')


def offline_cleanup(disk,work,arch,expected_root_sha256,accelerator='kvm'):
    import guestfs
    from .guestfs_kernel import appliance_kernel
    from .erasure import erase_ext4_free_space
    with appliance_kernel(Path('/debs'),arch,Path(work)/'guestfs-appliance',accelerator=accelerator):
        g=guestfs.GuestFS(python_return_dict=True)
        g.set_trace(False);g.set_verbose(False);g.set_memsize(768)
        launched=False
        try:
            g.add_drive_opts(str(disk),format='qcow2',discard='enable')
            # The pinned direct backend assigns our sole input disk hd0.
            # Preserve zero-write semantics while discarding all-zero extents.
            g.config('-set','drive.hd0.detect-zeroes=unmap')
            g.launch();launched=True
            partitions=g.list_partitions()
            if len(partitions)!=3 or g.vfs_type('/dev/sda3')!='ext4':raise ValueError('Unexpected installed disk layout')
            g.mount('/dev/sda3','/')
            images=g.glob_expand('/boot/*/*.squashfs')
            if len(images)!=1:raise ValueError('Expected one installed squashfs')
            require_factory_layer(g,images[0],expected_root_sha256)
            parent=str(Path(images[0]).parent)
            for path in [parent+'/rw',parent+'/work','/config','/opt/vyatta/etc/config']:
                if g.exists(path):g.rm_rf(path)
            for path in [parent+'/rw',parent+'/work']:g.mkdir_p(path)
            source=Path(work)/'sanitized.squashfs';g.download(images[0],str(source))
            root=Path(work)/'offline-root'
            subprocess.run(['unsquashfs','-processors','2','-d',str(root),str(source)],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            sanitize(root,arch);audit(root)
            rebuilt=Path(work)/'final.squashfs'
            subprocess.run(['mksquashfs',str(root),str(rebuilt),'-noappend','-comp','xz','-processors','2','-mem','512M','-no-progress'],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            g.upload(str(rebuilt),images[0])
            g.umount_all()
            erase_ext4_free_space(g)
        finally:
            try:
                if launched:g.shutdown()
            finally:g.close()


def build_disks(iso,arch,version,work,output,netbird_verify=None,accelerator='kvm'):
    from .architecture import release_accelerator
    accelerator=release_accelerator(arch,accelerator)
    work=Path(work);work.mkdir();disk=work/'fresh.qcow2'
    firmware_observations=[]
    if arch=='amd64':
        bios_disk=work/'bios-install.qcow2'
        subprocess.run(['qemu-img','create','-f','qcow2',str(bios_disk),'50G'],check=True,stdout=subprocess.DEVNULL)
        bios_live=None;bios_installed=None
        try:
            bios_live=VM(arch,bios_disk,work/'bios-live',iso,accelerator=accelerator,firmware_mode='bios')
            bios_live.enroll();bios_user=bios_live.username;bios_password=bios_live.password
            bios_live.install();bios_live.poweroff();bios_live.close();bios_live=None
            bios_installed=VM(arch,bios_disk,work/'bios-installed',accelerator=accelerator,firmware_mode='bios')
            bios_installed.username=bios_user;bios_installed.password=bios_password;bios_installed.login()
            if bios_installed.command('test ! -d /sys/firmware/efi && echo BIOS_PASS')!='BIOS_PASS':
                raise ValueError('BIOS installation unexpectedly used EFI')
            bios_installed.poweroff()
            firmware_observations.append('Same AMD64 ISO: actual BIOS install and installed-disk console login without EFI firmware passed')
        finally:
            if bios_live is not None:bios_live.close()
            if bios_installed is not None:bios_installed.close()
            bios_disk.unlink(missing_ok=True)
    subprocess.run(['qemu-img','create','-f','qcow2',str(disk),'50G'],check=True,stdout=subprocess.DEVNULL)
    live=VM(arch,disk,work/'live',iso,accelerator=accelerator)
    try:
        live.enroll();live_user=live.username;live_password=live.password
        live.install();live.poweroff()
    finally:live.close()
    installed=VM(arch,disk,work/'installed',accelerator=accelerator)
    try:
        installed.username=live_user;installed.password=live_password;installed.login()
        installed.command('test -d /sys/firmware/efi')
        installed.poweroff()
        live_password=None
    finally:installed.close()
    factory=work/'iso-factory.squashfs'
    subprocess.run(['xorriso','-osirrox','on','-indev',str(iso),'-extract',
                    '/live/filesystem.squashfs',str(factory)],check=True,
                   stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    offline_cleanup(disk,work,arch,sha256(factory),accelerator=accelerator)
    converted=convert(disk,work/'converted')
    images={};identities=[];observations=firmware_observations;netbird_results=[]
    observations.append('Actual UEFI ISO install and installed-disk console login with fresh firmware passed')
    for image in converted:
        target=Path(output)/f'shirube-os-{version}-{arch}{image.suffix}';shutil.copy2(image,target)
        images[image.suffix.removeprefix('.')]=target
        before=sha256(target)
        for provisioned in [False,True]:
            name=image.suffix.removeprefix('.')+('-provisioned' if provisioned else '-unprovisioned')
            clone=work/(name+'.qcow2')
            subprocess.run(['qemu-img','create','-f','qcow2','-F',image.suffix.removeprefix('.'),'-b',str(target),str(clone)],check=True,stdout=subprocess.DEVNULL)
            vm=VM(arch,clone,work/name,accelerator=accelerator)
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
                    geoip_code="import json,sqlite3; from pathlib import Path; from vyos import geoip; p=Path('/usr/share/shirube/geoip'); b=json.loads((p/'components.json').read_text()); c=sqlite3.connect('file:/var/cache/vyos/geoip-lookup.db?mode=ro',uri=True); assert c.execute('PRAGMA quick_check').fetchone()==('ok',); assert c.execute('SELECT count(*) FROM geoip_ranges').fetchone()[0]==b['source']['rows']; assert geoip.db_return_ranges(['us'],4); print('GEOIP_NATIVE_PASS')"
                    import shlex
                    if vm.command('sudo python3 -c '+shlex.quote(geoip_code)).strip()!='GEOIP_NATIVE_PASS':
                        raise ValueError('Native GeoIP cache was not restored on fresh disk boot')
                    observations.append(name+': native GeoIP cache restored and country lookup passed')
                    if netbird_verify is not None:
                        proof=netbird_verify(vm)
                        if not proof or any(value!='PASS' for value in proof.values()):
                            raise ValueError('Guest NetBird lifecycle proof incomplete')
                        netbird_results.append(proof)
                    vm.poweroff()
            finally:vm.close()
        from .cloud_init_guest import verify as verify_cloud_init
        if verify_cloud_init(target,arch,work/(image.suffix.removeprefix('.')+'-cloud-init'),accelerator)!='PASS':
            raise ValueError('Cloud-init actual access proof missing')
        observations.append(image.suffix.removeprefix('.')+': NoCloud metadata and user-data SSH-key-only access, native commands, write_files and reboot persistence passed without console enrollment')
        if sha256(target)!=before:raise ValueError('Boot tests modified an immutable export')
    if identities[0][0]==identities[1][0] or identities[0][1]==identities[1][1]:raise ValueError('Per-instance identities were reused')
    return {'images':images,'observations':observations,'identity_regeneration':'PASS','qemu_accelerator':accelerator,
            'cloud_init':'PASS','iso_boot_modes':'PASS',
            'netbird':'PASS' if len(netbird_results)==2 else 'NOT_RUN'}
