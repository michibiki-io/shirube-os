#!/usr/bin/env python3
"""Combine verified cached sources, source additions and initialization overlays.

This produces a source-only candidate. It never certifies binary provenance,
boot results, sanitization or a release; callers must verify their reused image
independently. Existing input archives are never modified.
"""
import argparse
import errno
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tarfile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from producer.bundles import bundle
from producer.collect import approval_for_notice
from producer.policy import require_stream_version
from producer.release import filename,verify_sources
from producer.sources import sha256


def cleared(component):
    if (component.get('redistributable') is not True or not isinstance(component.get('license'),str)
            or not component['license']):
        raise ValueError('Uncleared cached source component')
    for child in component.get('bundled_components',[]):cleared(child)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ['base','additions','packages','notices','overlays','project-source','output','work']:
        parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--commit',required=True)
    parser.add_argument('--architecture',choices=['amd64','arm64'],required=True)
    parser.add_argument('--version',required=True)
    args=parser.parse_args();repo=Path(__file__).resolve().parents[1]
    lock=json.loads((repo/'source-lock.json').read_text())
    require_stream_version(args.version,lock['stream']['version'])
    if not re.fullmatch('[0-9a-f]{40}',args.commit):raise ValueError('Exact source commit required')
    with tarfile.open(args.project_source) as project:
        if project.pax_headers.get('comment')!=args.commit:raise ValueError('Project archive commit differs')
    if not args.output.is_dir() or any(args.output.iterdir()):raise ValueError('Empty owned output required')
    args.work.mkdir(mode=0o700)
    base=json.loads((args.base/'manifest.json').read_text())
    upstream=base.get('upstream',{})
    if (base.get('project')!='Shirube OS' or base.get('version')!=args.version
            or upstream.get('version')!=lock['stream']['version']
            or upstream.get('sha256')!=lock['stream']['sha256']
            or upstream.get('vpp_commit')!=lock['external_git']['vpp']['commit']
            or upstream.get('vpp_patches_commit')!=lock['external_git']['vpp-patches']['commit']):
        raise ValueError('Cached source baseline/version differs')
    old_assets=[a for a in base['assets'] if a['role'] in {'source','licenses'} and a.get('architecture') in {args.architecture,'all'}]
    inventories=[a for a in old_assets if a['role']=='licenses']
    if len(inventories)!=1:raise ValueError('One cached component inventory required')
    delta=json.loads((args.additions/'source-delta.json').read_text())
    if delta.get('kind')!='partial-source-additions':raise ValueError('Explicit source additions required')
    assets=[]
    for directory,records in [(args.base,old_assets),(args.additions,delta['assets'])]:
        for asset in records:
            source=directory/filename(asset['name'])
            if source.is_symlink() or not source.is_file() or source.stat().st_size!=asset['size'] or sha256(source)!=asset['sha256']:
                raise ValueError('Cached source asset missing or changed')
            if asset['role']=='source':
                destination=args.output/source.name
                if destination.exists():raise ValueError('Source asset name collision')
                try:os.link(source,destination)
                except OSError as error:
                    if error.errno!=errno.EXDEV:raise
                    shutil.copyfile(source,destination)
                assets.append({**asset,'architecture':args.architecture})
    inventory=json.loads((args.base/inventories[0]['name']).read_text())
    components={c['name']:c for c in inventory['components']}
    if len(components)!=len(inventory['components']):raise ValueError('Ambiguous cached package identity')
    for component in delta['components']:
        if component['name'] in components:raise ValueError('Source addition replaces an existing package')
        components[component['name']]=component
    if any(not re.fullmatch('[a-z0-9][a-z0-9+.-]*',name) for name in components):
        raise ValueError('Invalid cached Debian package name')
    packages=[]
    for row in args.packages.read_text().splitlines():
        fields=row.split('\t')
        if len(fields)!=5:raise ValueError('Exact installed package/source identities required')
        name,version,arch,_,_=fields
        if arch not in {args.architecture,'all'}:raise ValueError('Installed architecture differs')
        packages.append({'name':name,'version':version,'architecture':arch})
    expected={(p['name'],p['version'],p['architecture']) for p in packages}
    actual={(c['name'],c['version'],c['architecture']) for c in components.values()}
    if len(expected)!=len(packages) or expected!=actual:raise ValueError('Cached/additional sources differ from installed packages')
    for component in components.values():cleared(component)
    approvals=json.loads((repo/'license-audit.json').read_text())['components']
    inputs=[(args.project_source,'project-source.tar','build_inputs'),
            (Path(__file__).resolve(),'compose-initialization-sources.py','sources'),
            (repo/'LICENSE','LICENSE','license_files')]
    changed_notices={}
    for name,component in components.items():
        notice=args.notices/name/'copyright'
        if (notice.is_symlink() or not notice.is_file()
                or not notice.resolve().is_relative_to(args.notices.resolve())):
            raise ValueError('Exact current installed notice required')
        # source_batches preserves each original namespace below
        # components/<original-archive-stem>/. Nested upstream/distribution
        # notices are separate grants, never the installed package notice.
        def installed_notice(reference):
            parts=Path(reference['member']).parts
            return parts==('copyright',) or (len(parts)==3 and parts[0]=='components' and parts[2]=='copyright')
        primary=[r for r in component['license_files'] if installed_notice(r)]
        if len(primary)!=1:raise ValueError('One cached installed notice binding required')
        current_notice=component.get('installed_notice',primary[0])
        if current_notice not in component['license_files']:raise ValueError('Cached installed notice binding differs')
        if sha256(notice)!=current_notice['sha256']:
            approval=approval_for_notice(approvals,component['source_package'],component['source_version'],component['source_identity'],notice,repo)
            if approval.get('redistributable') is not True or approval.get('copyright_sha256')!=sha256(notice):
                raise ValueError('Changed installed notice lacks an exact permission decision')
            component['license']=approval['license']
            if approval.get('notice_derivation'):component['notice_derivation']=approval['notice_derivation']
            else:component.pop('notice_derivation',None)
            member='installed-notices/'+name+'/copyright';changed_notices[name]=member
            inputs.append((notice,member,'license_files'))
    overlay_map=json.loads((args.overlays/'overlays.json').read_text())
    members={}
    for name,paths in overlay_map.items():
        if name not in components:raise ValueError('Overlay has no installed source component')
        members[name]=[]
        for relative in paths:
            path=Path(relative)
            if path.is_absolute() or '..' in path.parts or '.git' in path.parts or str(path)!=relative or '\\' in relative:
                raise ValueError('Unsafe initialization source overlay')
            member='modified-source/'+name+'/'+relative;members[name].append(member)
            source=args.overlays/name/path
            if not source.resolve().is_relative_to(args.overlays.resolve()):raise ValueError('Escaping initialization source overlay')
            inputs.append((source,member,'sources'))
        inputs.append((args.notices/name/'copyright','overlay-notices/'+name+'/copyright','license_files'))
    archive=args.output/f'shirube-os-{args.version}-{args.architecture}-initialization-overlay-source.tar.gz'
    extra=bundle(inputs,archive,1773859634)
    project_ref=next(r for r in extra['build_inputs'] if r['member']=='project-source.tar')
    def bind(component):
        # Preserve the original compilation recipes and add the actual source
        # composition/initialization input; do not relabel old native builds.
        component['build_inputs'].append(project_ref)
        for child in component.get('bundled_components',[]):bind(child)
    for name,component in components.items():
        bind(component)
        component['sources'].extend(r for r in extra['sources'] if r['member'] in members.get(name,[]))
        component['license_files'].extend(r for r in extra['license_files'] if r['member'] in {
            'overlay-notices/'+name+'/copyright',changed_notices.get(name)})
        if name in changed_notices:
            component['installed_notice']=next(r for r in extra['license_files'] if r['member']==changed_notices[name])
    assets.append({'name':archive.name,'role':'source','architecture':args.architecture,'size':archive.stat().st_size,'sha256':sha256(archive)})
    result={'schema_version':1,'shipped_packages':packages,'components':list(components.values())}
    print('STAGE: verify complete reused/additional/modified source references and permission',flush=True)
    verify_sources(args.output,result,assets)
    destination=args.output/f'shirube-os-{args.version}-{args.architecture}-licenses.json'
    destination.write_text(json.dumps(result,indent=2)+'\n')
    receipt={'kind':'composed-source-set-only','source_commit':args.commit,'version':args.version,'architecture':args.architecture,
        'base_source_commit':base['source_commit'],'base_inventory_sha256':inventories[0]['sha256'],
        'inventory':destination.name,'archives':[a['name'] for a in assets],'source_license_clearance':'PASS',
        'release_clearance':'NOT_CLEARED','binary_compilation':'NOT_PERFORMED','binary_provenance':'MUST_BE_VERIFIED_SEPARATELY',
        'complete_source_restoration':'NOT_RUN'}
    (args.output/'source-set.json').write_text(json.dumps(receipt,indent=2)+'\n')
    print(json.dumps(receipt),flush=True)


if __name__=='__main__':main()
