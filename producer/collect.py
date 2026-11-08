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


def http_dependencies(rootfs):
    from email.parser import BytesParser
    normalize=lambda value:re.sub('[-_.]+','-',value).lower()
    doc=path_in_root(rootfs,'usr/share/doc/vyos-http-api-tools')
    bom=doc/'components.json'
    declarations=json.loads(bom.read_text())['components']
    actual=set()
    site=path_in_root(rootfs,'usr/share/vyos-http-api-tools/lib/python3.11/site-packages')
    for metadata in site.glob('*.dist-info/METADATA'):
        parsed=BytesParser().parsebytes(metadata.read_bytes())
        actual.add((normalize(parsed['Name']),parsed['Version']))
    expected={(normalize(record['name']),record['version']) for record in declarations}
    if not expected or actual!=expected or len(expected)!=len(declarations):
        raise ValueError('API dependency inventory does not match installed distributions')
    result=[]
    for record in declarations:
        name=normalize(record['name'])
        if not re.fullmatch('[a-z0-9][a-z0-9-]*',name):raise ValueError('Invalid API dependency name')
        expected_path='third-party/'+record['name']+'/copyright'
        if record['license_notice']['path']!=expected_path:raise ValueError('Unexpected dependency notice path')
        notice=path_in_root(rootfs,'usr/share/doc/vyos-http-api-tools/'+expected_path)
        if not notice.is_file() or sha256(notice)!=record['license_notice']['sha256']:
            raise ValueError('Missing or differing dependency legal notice')
        result.append((record,notice,'third-party-notices/'+name+'/copyright'))
    return bom,result


def netbird_dependencies(rootfs,correspondence,arch):
    prefix='usr/share/doc/shirube-integration/'
    bom=path_in_root(rootfs,prefix+'netbird-components.json')
    if not bom.is_file() or sha256(bom)!=sha256(correspondence/'components.json'):
        raise ValueError('Installed NetBird dependency inventory differs from build inputs')
    data=json.loads(bom.read_text())
    if (data['architecture']!=arch or data['restored_offline_component_rebuild']!='PASS'
            or data['reference_binary_sha256']!=sha256(path_in_root(rootfs,'usr/libexec/shirube/netbird'))):
        raise ValueError('NetBird source proof does not match the shipped binary')
    children=[];seen=set()
    for record in data['linked_modules']:
        identity=(record['module'],record['version'])
        if identity in seen:raise ValueError('Duplicate linked NetBird module')
        seen.add(identity)
        member=record['source']['path'];notice_member=record['license_notice']['path']
        if not re.fullmatch(r'inputs/[0-9a-f]{64}\.zip',member) or not re.fullmatch(r'notices/[0-9a-f]{64}\.txt',notice_member):
            raise ValueError('Invalid linked module input path')
        notice=path_in_root(rootfs,prefix+'netbird-licenses/'+notice_member)
        if (not notice.is_file() or sha256(notice)!=record['license_notice']['sha256']
                or sha256(correspondence/'linked'/member)!=record['source']['sha256']):
            raise ValueError('Missing or differing linked module source/notice')
        children.append({'name':'go:'+record['module'],'version':record['version'],
            'source_identity':record['source_identity'],'source_member':'netbird/linked/'+member,
            'source_sha256':record['source']['sha256'],'notice':notice,
            'notice_member':'netbird/linked/'+notice_member})
    if not children:raise ValueError('Empty linked NetBird module inventory')
    notice=path_in_root(rootfs,prefix+'netbird-licenses/go-copyright')
    if not notice.is_file() or sha256(notice)!=data['go_notice_sha256']:
        raise ValueError('Missing or differing Go source legal notices')
    children.append({'name':'go-stdlib','version':data['compiler'],'source_identity':data['go_source_sha256'],
        'source_member':'netbird/inputs/go-source.tar.gz','source_sha256':data['go_source_sha256'],
        'notice':notice,'notice_member':'netbird/go-copyright'})
    return children


def approval_for(approvals,name,version,identity):
    key=name+'@'+version
    return approvals.get(key+'#'+identity,approvals.get(key,{}))


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
        print('SOURCE: '+name+' '+binary_version+' '+architecture,flush=True)
        package={'name':name,'version':binary_version,'architecture':architecture};shipped.append(package)
        source_name=source_name or name;source_version=source_version or binary_version
        copyright_file=path_in_root(rootfs,'usr/share/doc/'+name+'/copyright')
        if not copyright_file.is_file() or not copyright_file.read_bytes().strip():raise ValueError('Exact shipped copyright/license notice missing or empty')
        key=(source_name,source_version,sha256(copyright_file))
        if key not in cached:
            component=work/re.sub('[^A-Za-z0-9_.-]','_',source_name+'-'+source_version+'-'+str(len(cached)));component.mkdir()
            source=custom.get(name)
            if source is None:
                match=next((path for prefix,path in extras.items() if name.startswith(prefix)),None)
                if match:source=Path('/extra')/match
            inputs=[];nested=[]
            if name=='shirube-integration':
                source=Path('/src/guest');origin=source_identity
                lock=json.loads(Path('/src/source-lock.json').read_text())['netbird']
                correspondence=Path('/guest-package/netbird-correspondence')
                nested=netbird_dependencies(rootfs,correspondence,arch)
                for path,relative in files(correspondence):
                    category=('license_files' if relative.startswith('linked/notices/') or relative=='go-copyright'
                              else 'sources' if relative.startswith(('inputs/','linked/inputs/')) or relative=='vendor-source.tar.gz'
                              else 'build_inputs')
                    inputs.append((path,'netbird/'+relative,category))
            elif source is not None:
                origin=source_identity
            else:
                subprocess.run(['apt-get','source','--download-only','--only-source',source_name+'='+source_version],cwd=component,check=True)
                dsc=list(component.glob('*.dsc'))
                if len(dsc)!=1:raise ValueError('Exact Debian source descriptor missing')
                origin=sha256(dsc[0]);source=component
            if not source.is_dir():raise ValueError('Corresponding custom source directory missing')
            for path,relative in files(source):inputs.append((path,'source/'+relative,'sources'))
            if name=='vyos-http-api-tools':
                bom,dependencies=http_dependencies(rootfs)
                inputs.append((bom,'bundled-components.json','build_inputs'))
                for dependency,notice,member in dependencies:
                    inputs.append((notice,member,'license_files'))
                    nested.append({'name':'pypi:'+re.sub('[-_.]+','-',dependency['name']).lower(),
                        'version':dependency['version'],'source_identity':dependency['source']['sha256'],
                        'source_member':'source/inputs/'+dependency['source']['filename'],
                        'source_sha256':dependency['source']['sha256'],'notice':notice,'notice_member':member,
                        'binary_input':dependency['wheel']})
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
            if name=='vyos-http-api-tools':
                provenance={'packaging_recipe_sha256':sha256(Path('/src/native/http_api.py')),
                    'dependency_lock_sha256':sha256(Path('/src/native/http-api.lock.json')),
                    'packaging_license_sha256':sha256(Path('/src/LICENSE')),
                    'dependency_baseline':json.loads(Path('/src/native/http-api.lock.json').read_text())['dependency_baseline']}
                origin=hashlib.sha256(json.dumps(provenance,sort_keys=True).encode()).hexdigest()
            children=[]
            for dependency in nested:
                reviewed=approval_for(approvals,dependency['name'],dependency['version'],dependency['source_identity'])
                child_clear=(reviewed.get('source_identity')==dependency['source_identity']
                    and reviewed.get('copyright_sha256')==sha256(dependency['notice'])
                    and reviewed.get('redistributable') is True
                    and isinstance(reviewed.get('license'),str) and bool(reviewed.get('license')))
                source_refs=[record for record in records['sources'] if record['member']==dependency['source_member'] and record['sha256']==dependency['source_sha256']]
                if len(source_refs)!=1:raise ValueError('Exact bundled dependency source is missing')
                notice_refs=[record for record in records['license_files'] if record['member']==dependency['notice_member'] and record['sha256']==sha256(dependency['notice'])]
                if len(notice_refs)!=1:raise ValueError('Exact bundled dependency legal notice is missing')
                children.append({'name':dependency['name'],'version':dependency['version'],
                    'source_identity':dependency['source_identity'],'license':reviewed.get('license','UNREVIEWED'),
                    'redistributable':child_clear,'sources':source_refs,
                    'license_files':notice_refs,
                    'build_inputs':records['build_inputs'],
                    **({'binary_input':dependency['binary_input']} if 'binary_input' in dependency else {})})
            approval=approval_for(approvals,source_name,source_version,origin)
            cleared=(approval.get('source_identity')==origin and approval.get('redistributable') is True
                     and approval.get('copyright_sha256')==sha256(copyright_file)
                     and isinstance(approval.get('license'),str) and bool(approval.get('license')))
            cleared=cleared and all(child['redistributable'] for child in children)
            cached[key]=(records,approval.get('license','UNREVIEWED'),cleared,origin,provenance,children)
            assets.append(archive)
        records,license_name,cleared,origin,provenance,children=cached[key]
        components.append({**package,'source_package':source_name,'source_version':source_version,
                           'source_identity':origin,'source_origin':provenance,'license':license_name,'redistributable':cleared,
                           'bundled_components':children,**records})
    inventory={'schema_version':1,'shipped_packages':shipped,'components':components}
    path=output/f'shirube-os-{version}-{arch}-licenses.json';path.write_text(json.dumps(inventory,indent=2)+'\n')
    return {'inventory':path,'archives':assets,'cleared':all(x['redistributable'] for x in components)}
