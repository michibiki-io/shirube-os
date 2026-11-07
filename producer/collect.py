"""Collect exact shipped-package sources, licenses and reconstruction recipes."""
import json
import hashlib
from pathlib import Path
import re
import subprocess
from .bundles import bundle
from .sources import sha256
from .sanitize import path_in_root

GENERATED={'.git','__pycache__','.pytest_cache','.debhelper'}
SUFFIXES={'.o','.ko','.a','.so','.cmi','.cmo','.cmx','.pyc','.deb'}


def files(root):
    root=Path(root).resolve()
    for path in sorted(root.rglob('*')):
        relative=path.relative_to(root)
        if any(part in GENERATED for part in relative.parts):continue
        if path.name in {'signing_key.pem','vmlinux','System.map'} or path.suffix in SUFFIXES:continue
        if path.is_file():
            resolved=path.resolve()
            if not resolved.is_relative_to(root):raise ValueError('Source symlink escapes component')
            yield resolved,str(relative)


def collect(rootfs, package_output, output, arch, version, work):
    rootfs,package_output,output,work=map(Path,[rootfs,package_output,output,work]);work.mkdir()
    listing=subprocess.check_output(['dpkg-query','--admindir='+str(rootfs/'var/lib/dpkg'),'-W',
        '-f=${Package}\t${Version}\t${Architecture}\t${source:Package}\t${source:Version}\n'],text=True)
    custom={}
    catalog=json.loads(Path('/src/native/packages.json').read_text())
    for job in catalog['jobs']:
        for deb in (package_output/job['name']).glob('*.deb'):
            name=subprocess.check_output(['dpkg-deb','-f',str(deb),'Package'],text=True).strip()
            custom[name]=Path('/build-private')/job['name']/'source'
    extras={
      'vpp':'vpp/vpp','libvpp':'vpp/vpp','nat-rtsp':'modules/rtsp','vyos-ipt-netflow':'modules/netflow',
      'openvpn-dco':'modules/ovpn','jool':'modules/jool','accel-ppp':'accel/accel','hsflowd':'accel/sflow',
      'isc-kea':'kea/isc-kea','salt':'salt/salt','podman':'podman/podman','telegraf':'go/telegraf',
      'node-exporter':'go/node-exporter','frr-exporter':'go/frr-exporter','blackbox-exporter':'go/blackbox-exporter',
      'vyos-utils':'base/utils','python3-humps':'base/humps','ddclient':'base/ddclient','i2util':'base/i2util',
      'owamp':'base/owamp','openvpn-otp':'base/otp','udp-broadcast-relay':'base/relay','vyos-libpam-radius-auth':'base/pam'}
    audit_path=Path('/src/license-audit.json')
    approvals=json.loads(audit_path.read_text())['components'] if audit_path.is_file() else {}
    shipped=[];components=[];assets=[];cached={}
    project=Path('/project-source.tar')
    if not project.is_file():raise ValueError('Exact project reconstruction source archive missing')
    source_identity=json.loads(Path('/src/source-lock.json').read_text())['stream']['sha256']
    for line in listing.splitlines():
        name,binary_version,architecture,source_name,source_version=line.split('\t')
        package={'name':name,'version':binary_version,'architecture':architecture};shipped.append(package)
        source_name=source_name or name;source_version=source_version or binary_version
        copyright_file=path_in_root(rootfs,'usr/share/doc/'+name+'/copyright')
        if not copyright_file.is_file():raise ValueError('Exact shipped copyright/license notice missing')
        key=(source_name,source_version,sha256(copyright_file))
        if key not in cached:
            component=work/re.sub('[^A-Za-z0-9_.-]','_',source_name+'-'+source_version+'-'+str(len(cached)));component.mkdir()
            source=custom.get(name)
            if source is None:
                match=next((path for prefix,path in extras.items() if name.startswith(prefix)),None)
                if match:source=Path('/extra')/match
            inputs=[]
            if name=='shirube-integration':
                source=Path('/src/guest');origin=source_identity
                # Include the pinned headless client's exact published source.
                # Licensing/linked-component clearance is still explicit policy.
                lock=json.loads(Path('/src/source-lock.json').read_text())['netbird']
                from .sources import download
                netbird=component/'netbird-source.tar.gz'
                download(lock['source_url'],netbird)
                if sha256(netbird)!=lock['source_sha256']:raise ValueError('NetBird source checksum mismatch')
                inputs.append((netbird,'netbird-source.tar.gz','sources'))
            elif source is not None:
                origin=source_identity
            else:
                subprocess.run(['apt-get','source','--download-only','--only-source',source_name+'='+source_version],cwd=component,check=True)
                dsc=list(component.glob('*.dsc'))
                if len(dsc)!=1:raise ValueError('Exact Debian source descriptor missing')
                origin=sha256(dsc[0]);source=component
            if not source.is_dir():raise ValueError('Corresponding custom source directory missing')
            for path,relative in files(source):inputs.append((path,'source/'+relative,'sources'))
            inputs.extend([(copyright_file,'copyright','license_files'),(project,'project-source.tar','build_inputs')])
            archive=output/f'shirube-os-{version}-{arch}-source-{len(cached):04d}.tar.gz'
            common=rootfs/'usr/share/common-licenses'
            if common.is_dir():
                for legal_file,relative in files(common):
                    inputs.append((legal_file,'common-licenses/'+relative,'license_files'))
            records=bundle(inputs,archive,1773859634)
            external=json.loads(Path('/src/native/external-sources.lock.json').read_text())
            keys=[]
            location=str(source)
            for prefix, names in [('/extra/vpp/',['vpp','vpp-patches']),('/extra/modules/rtsp',['nat-rtsp']),('/extra/modules/netflow',['ipt-netflow']),('/extra/base/utils',['vyos-utils']),('/extra/base/i2util',['i2util']),('/extra/kea/',['kea-packaging']),('/extra/salt/',['salt-tools'])]:
                if location.startswith(prefix):keys.extend(names)
            provenance={'stream_archive_sha256':source_identity,'external_git':{key:external[key] for key in keys}}
            if name=='shirube-integration':
                provenance={'netbird_source_commit':lock['source_commit'],'netbird_source_sha256':lock['source_sha256'],'headless_asset_sha256':lock['assets'][arch]['sha256'],'integration_license_sha256':sha256(Path('/src/LICENSE'))}
                origin=hashlib.sha256(json.dumps(provenance,sort_keys=True).encode()).hexdigest()
            if keys:origin=hashlib.sha256(json.dumps(provenance,sort_keys=True).encode()).hexdigest()
            approval=approvals.get(source_name+'@'+source_version,{})
            cleared=(approval.get('source_identity')==origin and approval.get('redistributable') is True
                     and isinstance(approval.get('license'),str) and bool(approval.get('license')))
            cached[key]=(records,approval.get('license','UNREVIEWED'),cleared,origin,provenance)
            assets.append(archive)
        records,license_name,cleared,origin,provenance=cached[key]
        components.append({**package,'source_package':source_name,'source_version':source_version,
                           'source_identity':origin,'source_origin':provenance,'license':license_name,'redistributable':cleared,**records})
    inventory={'schema_version':1,'shipped_packages':shipped,'components':components}
    path=output/f'shirube-os-{version}-{arch}-licenses.json';path.write_text(json.dumps(inventory,indent=2)+'\n')
    return {'inventory':path,'archives':assets,'cleared':all(x['redistributable'] for x in components)}
