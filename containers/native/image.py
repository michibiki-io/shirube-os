#!/usr/bin/python3
"""Assemble and inspect an independently branded ISO from native source packages."""
import functools
import gzip
import hashlib
import http.server
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import threading
sys.path.insert(0,'/src')
from producer.architecture import check_elf, check_kernel, check_pe
from producer.branding import install as install_brand
from producer.sources import download, sha256
from producer.packages import audit
from producer.notices import snapshot, mark_changed
from producer.sanitize import HOSTS


def run(*args, **kwargs):
    return subprocess.run(args,check=True,**kwargs)


def generate(arch):
    root=Path('/opt/stream'); root.mkdir(parents=True)
    multi=subprocess.check_output(['dpkg-architecture','-qDEB_HOST_MULTIARCH'],text=True).strip()
    for template in Path('/src/native').glob('*.py.in'):
        (root/template.name.removesuffix('.in')).write_text(template.read_text().replace('@ARCH@',arch).replace('@MULTIARCH@',multi))
    shutil.copy2('/src/native/external-sources.lock.json',root/'external-sources.lock.json')
    return root


def guest_package(arch, version):
    debs=Path('/debs'); work=Path('/guest-package'); work.mkdir()
    source=Path('/sources/vyos-1x/vyos-1x')
    package=work/'overlay'; (package/'DEBIAN').mkdir(parents=True)
    lib=package/'usr/libexec/shirube'; lib.mkdir(parents=True)
    lock=json.loads(Path('/src/source-lock.json').read_text())['netbird']
    archive=work/'netbird.tar.gz'; download(lock['assets'][arch]['url'],archive)
    if sha256(archive)!=lock['assets'][arch]['sha256']:
        raise ValueError('NetBird asset checksum mismatch')
    with tarfile.open(archive) as upstream:
        binary=upstream.getmember('netbird')
        if not binary.isfile():raise ValueError('Missing NetBird headless binary')
        data=upstream.extractfile(binary).read(); check_elf(data,arch)
        (lib/'netbird').write_bytes(data)
        for entry in upstream:
            if entry.isfile() and Path(entry.name).name.lower().startswith('license'):
                path=package/'usr/share/doc/shirube-integration'/Path(entry.name).name
                path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(upstream.extractfile(entry).read())
    (lib/'netbird').chmod(0o755)
    source_archive=work/'netbird-source.tar.gz';download(lock['source_url'],source_archive)
    if sha256(source_archive)!=lock['source_sha256']:raise ValueError('NetBird source archive checksum mismatch')
    with tarfile.open(source_archive) as upstream:
        license_data=upstream.extractfile('netbird-'+lock['source_commit']+'/LICENSE').read()
    copyright_path=package/'usr/share/doc/shirube-integration/copyright'
    copyright_path.parent.mkdir(parents=True,exist_ok=True)
    copyright_path.write_bytes(b'Shirube OS integration: Copyright (c) 2026 michibiki-io. MIT License.\n\n'+Path('/src/LICENSE').read_bytes()+b'\n\nNetBird headless client upstream notice (directory exceptions retained):\n'+license_data)
    shutil.copy2('/src/LICENSE',copyright_path.parent/'distributor-LICENSE')
    shutil.copy2('/src/NOTICE',copyright_path.parent/'NOTICE')
    shutil.copytree('/src/LICENSES',copyright_path.parent/'distribution-licenses')
    from producer.vpp_legal import install as install_vpp_notices
    install_vpp_notices(Path('/extra/vpp/vpp/build/external/downloads'),
                       Path('/src/native'), copyright_path.parent/'vpp-inputs')
    actual=subprocess.check_output([str(lib/'netbird'),'version'],text=True).strip()
    if actual!=lock['version']:raise ValueError('NetBird version mismatch')
    from producer.netbird_sources import prepare
    correspondence=work/'netbird-correspondence'
    print('STAGE: NetBird exact linked sources, notices and restored offline rebuild',flush=True)
    prepare(lib/'netbird',archive,arch,work/'netbird-source-build',correspondence)
    notices=copyright_path.parent/'netbird-licenses';notices.mkdir()
    shutil.copytree(correspondence/'linked/notices',notices/'notices')
    shutil.copy2(correspondence/'go-copyright',notices/'go-copyright')
    shutil.copy2(correspondence/'components.json',copyright_path.parent/'netbird-components.json')
    with copyright_path.open('a') as target:
        target.write('\nExact linked Go dependency and standard-library notices are retained under netbird-licenses/.\n')
    for source_file,target in [('/src/producer/netbird.py','netbird_control.py'),('/src/producer/netbird.py','netbird-control'),('/src/guest/first_boot.py','first-boot')]:
        shutil.copy2(source_file,lib/target);(lib/target).chmod(0o755)
    conf=package/'usr/libexec/vyos/conf_mode/service_netbird.py';conf.parent.mkdir(parents=True)
    shutil.copy2('/src/guest/service_netbird.py',conf);conf.chmod(0o755)
    wants=package/'etc/systemd/system/multi-user.target.wants'
    wants.mkdir(parents=True)
    (wants/'shirube-first-boot.service').symlink_to('/lib/systemd/system/shirube-first-boot.service')
    for name in ['shirube-netbird.service','shirube-first-boot.service']:
        target=package/'lib/systemd/system'/name;target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(Path('/src/guest')/name,target)
    for unit in ['getty@.service','serial-getty@.service']:
        dropin=package/'etc/systemd/system'/(unit+'.d')/'shirube-enrollment.conf'
        dropin.parent.mkdir(parents=True)
        dropin.write_text('[Unit]\nConditionPathExists=!/config/shirube-first-boot\nConditionPathExists=!/usr/share/shirube/needs-enrollment\n')
    # Keep native unit names/dependencies and upstream notices; override only
    # the product descriptions and journal display identifiers.
    displays={
        'vyos-router.service':'Router',
        'vyos.target':'target',
        'vyos-configd.service':'configuration daemon',
        'vyos-hostsd.service':'DNS configuration keeper',
        'vyos-event-handler.service':'event handler',
        'vyos-wan-load-balance.service':'WAN Load Balancer',
        'vyos-system-update.service':'system update-check service',
        'vyos-domain-resolver.service':'firewall domain resolver',
        'vyos-network-event-logger.service':'network-event logger daemon',
        'vyos-conntrack-logger.service':'conntrack logger daemon',
        'root-partition-auto-resize.service':'root partition auto resizing',
    }
    for unit,description in displays.items():
        dropin=package/'etc/systemd/system'/(unit+'.d')/'shirube-branding.conf'
        dropin.parent.mkdir(parents=True)
        identifier='shirube-'+unit.removeprefix('vyos-').removesuffix('.service')
        dropin.write_text('[Unit]\nDescription=Shirube OS '+description+'\n'+
            ('[Service]\nSyslogIdentifier='+identifier+'\n' if unit.endswith('.service') else ''))
    templates=work/'templates';templates.mkdir()
    run(str(source/'scripts/build-command-templates'),'/src/guest/service_netbird.xml',str(source/'schema/interface_definition.rng'),str(templates))
    shutil.copytree(templates/'service/netbird',package/'opt/vyatta/share/vyatta-cfg/templates/service/netbird')
    op_templates=work/'templates-op';op_templates.mkdir()
    run(str(source/'scripts/build-command-op-templates'),'/src/guest/netbird_op.xml',str(source/'schema/op-mode-definition.rng'),str(op_templates))
    for branch in ['generate/netbird','show/netbird']:
        shutil.copytree(op_templates/branch,package/'opt/vyatta/share/vyatta-op/templates'/branch)
    candidates=list(debs.glob(f'vyos-1x_*_{arch}.deb'))
    if len(candidates)!=1:raise ValueError('Exactly one native core package required')
    original=candidates[0];core=work/'core';run('dpkg-deb','-R',str(original),str(core))
    original_core=snapshot(core)
    native=work/'native';native.mkdir()
    libraries=list(debs.glob(f'libvyosconfig0_*_{arch}.deb'))
    if len(libraries)!=1:raise ValueError('Exactly one native configuration library required')
    run('dpkg-deb','-x',str(libraries[0]),str(native))
    for library in native.rglob('libvyosconfig.so*'):
        shutil.copy2(library,Path('/usr/lib')/library.name,follow_symlinks=True)
    from producer.geoip import install as install_geoip
    install_geoip(Path('/src'), source, package)
    cache=package/'usr/lib/python3/dist-packages/vyos/xml_ref/pkg_cache';cache.mkdir(parents=True)
    xml=work/'xml';xml.mkdir();shutil.copy2('/src/guest/service_netbird.xml',xml/'service_netbird.xml')
    run('python3',str(source/'python/vyos/xml_ref/generate_cache.py'),'--xml-dir',str(xml),'--package-name','shirube-integration','--output-path',str(cache),env=dict(os.environ,PYTHONPATH=str(source/'python')))
    includes=core/'usr/share/vyos/configd-include.json'
    includes.write_text(json.dumps(sorted(set(json.loads(includes.read_text())+['service_netbird.py'])),indent=2)+'\n')
    if arch=='arm64':
        sys.path.insert(0,'/src/native')
        from arm_console import patch_core
        from arm_grub import patch_grub
        patch_core(core);patch_grub(core)
    install_brand(core,version,Path('/src/shirube-os-logo.svg'))
    from producer.branding import defer_factory_identity,runtime_displays
    defer_factory_identity(core)
    runtime_displays(core)
    from producer.installer_admin import patch_installer
    patch_installer(core)
    # Change display strings only in UI/installer files. Keep copyright/license
    # headers and compatibility module/CLI/filesystem names intact.
    for base in ['usr/share/vyos/templates/grub','usr/libexec/vyos/op_mode','usr/lib/python3/dist-packages/vyos/system']:
        directory=core/base
        if not directory.exists():continue
        for file in directory.rglob('*'):
            if not file.is_file() or file.is_symlink():continue
            try:text=file.read_text()
            except (UnicodeError,OSError):continue
            if file.suffix not in {'.py','.j2','.cfg'}:continue
            if file.suffix=='.py':
                if file.name not in {'version.py','show_version.py','image.py','image_installer.py'}:continue
                import tokenize,io
                tokens=[]
                for token in tokenize.generate_tokens(io.StringIO(text).readline):
                    if token.type==tokenize.STRING:
                        value=''.join(line if 'copyright' in line.lower() else line.replace('VyOS','Shirube OS') for line in token.string.splitlines(True))
                        token=token._replace(string=value)
                    tokens.append(token)
                file.write_text(tokenize.untokenize(tokens))
                continue
            lines=[]
            for line in text.splitlines(True):
                if not line.lstrip().startswith('#') and 'copyright' not in line.lower():
                    line=line.replace('VyOS','Shirube OS')
                    for url in ['https://support.vyos.io','https://vyos.dev','https://vyos.io']:
                        line=line.replace(url,'https://github.com/michibiki-io/shirube-os/issues')
                lines.append(line)
            file.write_text(''.join(lines))
    for file in (core/'usr/share/vyos').rglob('*'):
        if file.is_file() and file.suffix.lower() in {'.png','.svg','.jpg','.jpeg','.ico'}:
            if file.suffix=='.svg':shutil.copyfile('/src/shirube-os-logo.svg',file)
            elif file.suffix=='.png':shutil.copyfile(core/'usr/share/shirube/boot-background.png',file)
            else:file.unlink()
    old_version=subprocess.check_output(['dpkg-deb','-f',str(original),'Version'],text=True).strip()
    changed=mark_changed(original_core)
    copyright_file=core/'usr/share/doc/vyos-1x/copyright'
    if not copyright_file.resolve().is_relative_to(core.resolve()) or not copyright_file.is_file():
        raise ValueError('Core copyright notice must be available within the extracted package')
    if copyright_file.is_symlink():
        original_notice=copyright_file.read_bytes();copyright_file.unlink();copyright_file.write_bytes(original_notice)
    with copyright_file.open('a') as notice:
        notice.write('\nShirube OS modifications by michibiki-io, 2026-11-08.\n'
                     'Upstream copyright and license terms remain applicable.\n')
        for path in changed:notice.write('Modified: '+str(path.relative_to(core))+'\n')
    new_version=old_version+'+shirube3'
    control=core/'DEBIAN/control';control.write_text(control.read_text().replace('Version: '+old_version+'\n','Version: '+new_version+'\n'))
    (core/'DEBIAN/md5sums').unlink(missing_ok=True)
    repaired=debs/f'vyos-1x_{new_version}_{arch}.deb';run('dpkg-deb','--root-owner-group','-b',str(core),str(repaired));original.unlink()
    (package/'DEBIAN/control').write_text(f'Package: shirube-integration\nVersion: {version[1:]}\nArchitecture: {arch}\nMaintainer: michibiki-io <maintainers@michibiki.io>\nDepends: vyos-1x (= {new_version}), python3, systemd, ca-certificates\nDescription: Shirube OS identity, first boot and NetBird integration\n')
    run('dpkg-deb','--root-owner-group','-b',str(package),str(debs/f'shirube-integration_{version[1:]}_{arch}.deb'))


def assemble(sources, packages, arch, version, output):
    from producer.debootstrap import configure_retry
    configure_retry()
    root=generate(arch)
    Path('/sources').symlink_to(sources)
    debs=Path('/debs');debs.mkdir()
    for package in packages.rglob('*.deb'):shutil.copy2(package,debs/package.name)
    signing=Path('/kernel-signing');signing.mkdir(mode=0o700)
    kernel=Path('/build-private/kernel/source/certs')
    shutil.copy2(kernel/'signing_key.pem',signing/'key.pem');(signing/'key.pem').chmod(0o600)
    shutil.copy2(kernel/'signing_key.x509',signing/'cert.der')
    for job in ['vpp','base','go','kea','salt','podman','modules','accel']:
        print('STAGE: native runtime '+job,flush=True)
        run('python3',str(root/'extras.py'),job)
    from producer.package_notices import retain_all
    retain_all(debs,Path('/build-private/runtime-package-notices'))
    guest_package(arch,version)
    audited=list(debs.glob('*.deb'))
    signed_modules=sum(audit(package,arch,signing/'cert.der')['signed_modules_verified'] for package in audited)
    if not signed_modules:raise ValueError('No signed kernel/module payload was verified')
    sys.path.insert(0,str(root));from repo_metadata import mapuser_compatibility
    mapuser_compatibility(debs)
    work=Path('/iso-work');work.mkdir();repo=work/'repo';pool=repo/'pool/main';pool.mkdir(parents=True)
    for package in debs.glob('*.deb'):shutil.copy2(package,pool/package.name)
    index=repo/f'dists/circinus/main/binary-{arch}/Packages';index.parent.mkdir(parents=True)
    with index.open('w') as target:run('dpkg-scanpackages','--multiversion','pool','/dev/null',cwd=repo,stdout=target)
    release=repo/'dists/circinus/Release'
    with release.open('w') as target:run('apt-ftparchive','-o','APT::FTPArchive::Release::Origin=Shirube OS','-o','APT::FTPArchive::Release::Suite=circinus','-o','APT::FTPArchive::Release::Codename=circinus','-o',f'APT::FTPArchive::Release::Architectures={arch}','-o','APT::FTPArchive::Release::Components=main','release','dists/circinus',cwd=repo,stdout=target)
    from producer.signing import build_keys
    with build_keys(Path('/build-private')) as keys:
        env=dict(os.environ,GNUPGHOME=str(keys['repository_home']),VPP_INSTALL_SKIP_SYSCTL='1')
        public=work/'repository.asc';shutil.copy2(keys['repository_public_key'],public)
        run('gpg','--batch','--yes','--armor','--detach-sign','--output',str(release)+'.gpg',str(release),env=env)
        run('gpg','--batch','--yes','--clearsign','--output',str(release.parent/'InRelease'),str(release),env=env)
        source=work/'vyos-build';shutil.copytree(sources/'vyos-build/vyos-build',source,symlinks=True)
        original_build=snapshot(source)
        from producer.geoip import replace_hook
        replace_hook(source/'data/live-build-config/hooks/live/40-init-geoip-database.chroot', Path('/src'))
        one=work/'vyos-1x';shutil.copytree(sources/'vyos-1x/vyos-1x',one,symlinks=True)
        for tree in [source,one]:
            run('git','init','-q',cwd=tree);run('git','add','.',cwd=tree)
            run('git','-c','user.name=Shirube source import','-c','user.email=maintainers@michibiki.io','commit','-qm','Import authenticated Stream snapshot',cwd=tree,env=dict(env,GIT_AUTHOR_DATE='2026-03-18T00:00:00Z',GIT_COMMITTER_DATE='2026-03-18T00:00:00Z'))
            run('git','branch','-M','circinus',cwd=tree)
        # Keep original notices and the exact dependency/source inventories
        # installed under package documentation directories in the SquashFS.
        excludes=source/'data/live-build-config/rootfs/excludes'
        excluded_docs='usr/share/doc/*/!(copyright*|README*)'
        text=excludes.read_text()
        if text.splitlines().count(excluded_docs)!=1:
            raise ValueError('Unexpected package-documentation exclusion policy')
        excludes.write_text(text.replace(excluded_docs+'\n',
            '# Shirube OS retains package documentation, legal notices and source inventories.\n'))
        from producer.strip_policy import preserve_runtime_bindings
        preserve_runtime_bindings(source/'data/live-build-config/hooks/live/92-strip-symbols.chroot',
                                  Path('/extra'))
        env['VYOS1X_REPO_URL']=str(one)
        defaults=source/'data/defaults.toml';text=defaults.read_text()
        text=text.replace('http://deb.debian.org/debian-security','http://snapshot.debian.org/archive/debian-security/20261105T000000Z/').replace('http://deb.debian.org/debian','http://snapshot.debian.org/archive/debian/20261105T000000Z/')
        for url in ['https://vyos.io','https://support.vyos.io','https://vyos.dev','https://docs.vyos.io/en/latest','https://blog.vyos.io']:
            text=text.replace(url,'https://github.com/michibiki-io/shirube-os')
        for key,suffix in [('support_url','/issues'),('bugtracker_url','/issues'),('documentation_url','/tree/main/docs'),('project_news_url','/releases')]:
            text=re.sub(r'^'+key+r' = .*$',key+' = "https://github.com/michibiki-io/shirube-os'+suffix+'"',text,flags=re.M)
        defaults.write_text(text)
        build=source/'build-vyos-image';text=build.read_text().replace('NAME="VyOS"','NAME="Shirube OS"').replace('PRETTY_NAME="VyOS ', 'PRETTY_NAME="Shirube OS ').replace('ID=vyos\n','ID=shirube-os\nID_LIKE="vyos debian"\n').replace('--iso-application "VyOS"','--iso-application "Shirube OS"').replace('--iso-volume "VyOS"','--iso-volume "Shirube OS"');
        if arch=='arm64':text=text.replace('console=ttyS0,115200','console=ttyAMA0,115200')
        build.write_text(text)
        flavor=source/'data/build-flavors/generic.toml';flavor.write_text(flavor.read_text().replace('packages = []','packages = ["shirube-integration"]'))
        for image in (source/'data/live-build-config/bootloaders').rglob('*'):
            if image.is_file() and image.suffix.lower() in {'.png','.svg','.jpg','.jpeg'}:
                if image.suffix=='.png':shutil.copy2('/guest-package/core/usr/share/shirube/boot-background.png',image)
                elif image.suffix=='.svg':shutil.copy2('/src/shirube-os-logo.svg',image)
                else:image.unlink()
            elif image.is_file() and image.suffix in {'.cfg','.txt'}:
                image.write_text(image.read_text().replace('VyOS','Shirube OS'))
        if arch=='amd64':
            (source/'data/architectures/amd64.toml').write_text('packages = ["grub-efi-amd64", "grub-pc-bin", "openvpn-dco", "telegraf", "vyos-ipt-netflow"]\nbootloaders = "grub-efi"\nsquashfs_compression_type = "xz -processors 2 -mem 1G -b 256k -always-use-fragments -no-recovery"\n')
        if arch=='arm64':
            (source/'data/live-build-config/bootloaders/grub-pc/grub.cfg').write_text('set default=0\nset timeout=5\nterminal_input console\nterminal_output console\nLINUX_LIVE\nLINUX_INSTALL\n')
        config=source/f'data/architectures/{arch}.toml';config.write_text(config.read_text().replace('xz -b 256k','xz -processors 2 -mem 1G -b 256k').replace('xz -Xbcj x86','xz -processors 2 -mem 1G -Xbcj x86'))
        hook=source/'data/live-build-config/hooks/live/099-shirube.chroot'
        # live-build restores /etc/hosts after chroot hooks. Its supported
        # includes override must carry the factory value through that cleanup.
        hosts=source/'data/live-build-config/includes.chroot/etc/hosts'
        hosts.parent.mkdir(parents=True,exist_ok=True)
        hosts.write_text(HOSTS)
        factory=source/'data/live-build-config/includes.chroot/usr/libexec/shirube/factory_prepare.py'
        factory.parent.mkdir(parents=True,exist_ok=True);shutil.copy2('/src/guest/factory_prepare.py',factory)
        hook.write_text('#!/bin/sh\nset -eu\npython3 /usr/lib/python3/dist-packages/vyos/xml_ref/update_cache.py\ndepmod -a 6.6.128-vyos\nSHIRUBE_BUILD_CHROOT=1 python3 /usr/libexec/shirube/factory_prepare.py '+arch+'\nrm /usr/libexec/shirube/factory_prepare.py\n')
        hook.chmod(0o755)
        binary_hook=source/'data/live-build-config/hooks/live/100-shirube.binary'
        binary_hook.write_text('#!/usr/bin/python3\nimport sys\nfrom pathlib import Path\nsys.path.insert(0,"/src")\nfrom producer.branding import boot_menus\nboot_menus(Path.cwd(),'+repr(arch)+','+repr(version)+')\n')
        binary_hook.chmod(0o755)
        with (source/'data/live-build-config/environment.chroot').open('a') as target:target.write('\nVPP_INSTALL_SKIP_SYSCTL=1\n')
        mark_changed(original_build)
        handler=functools.partial(http.server.SimpleHTTPRequestHandler,directory=str(repo))
        server=http.server.ThreadingHTTPServer(('127.0.0.1',18080),handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            run('./build-vyos-image','--architecture',arch,'--build-type','release','--version',version[1:],'--build-by','michibiki-io','--vyos-mirror','http://127.0.0.1:18080','--custom-apt-key',str(public),'generic',cwd=source,env=env)
        finally:server.shutdown();server.server_close();thread.join()
        candidates=list((source/'build').glob(f'vyos-{version[1:]}-generic-{arch}.iso'))
        if len(candidates)!=1:raise ValueError('Expected exactly one assembled ISO')
        image=output/f'shirube-os-{version}-{arch}.iso';shutil.copy2(candidates[0],image)
        verify=work/'verify';verify.mkdir()
        for item,name in [('/boot/grub/efi.img','efi.img'),('/live/vmlinuz-6.6.128-vyos','vmlinuz'),('/live/filesystem.packages','filesystem.packages')]:
            run('xorriso','-osirrox','on','-indev',str(image),'-extract',item,str(verify/name))
        if any(line.split()[0]=='vyos-1x-smoketest' for line in (verify/'filesystem.packages').read_text().splitlines() if line.strip()):
            raise ValueError('On-image smoketest fixtures must not enter the distribution')
        efi='BOOTAA64.EFI' if arch=='arm64' else 'BOOTX64.EFI'
        run('mcopy','-i',str(verify/'efi.img'),f'::/EFI/BOOT/{efi}',str(verify/'boot.efi'));check_pe((verify/'boot.efi').read_bytes(),arch)
        kernel=verify/'vmlinuz';data=kernel.read_bytes()
        if arch=='arm64' and data[:2]==b'\x1f\x8b':data=gzip.decompress(data)
        check_kernel(data,arch)
        return {'iso':image,'packages':(verify/'filesystem.packages').read_text(),
                'signed_modules_verified':signed_modules,'audited_packages':len(audited),'kernel_certificate_sha256':sha256(signing/'cert.der'),'repository_fingerprint':keys['repository_fingerprint'],
                'source':source,'sources':sources,'local_boot':'NOT_RUN','oci':'NOT_RUN'}
