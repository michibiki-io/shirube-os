#!/usr/bin/env python3
"""Refresh a retained source set without compiling or certifying OS binaries."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys
import tarfile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from producer.collect import approval_for_notice,approval_for
from producer.bundles import bundle
from producer.release import filename,verify_sources
from producer.source_reuse import apply
from producer.sources import sha256


def prune_archive(source,destination,needed):
    """Retain only referenced original bytes; excluded inputs cannot linger."""
    observed=set()
    with Path(destination).open('xb') as raw,gzip.GzipFile(filename='',fileobj=raw,mode='wb',compresslevel=1,mtime=0) as compressed:
        with tarfile.open(fileobj=compressed,mode='w|') as target,tarfile.open(source) as archive:
            for member in archive:
                if member.name not in needed:continue
                path=Path(member.name)
                if (not member.isfile() or member.name in observed or path.is_absolute()
                        or '..' in path.parts or '.git' in path.parts or str(path)!=member.name or '\\' in member.name):
                    raise ValueError('Unsafe retained source member')
                observed.add(member.name)
                header=tarfile.TarInfo(member.name);header.size=member.size
                header.mode=0o755 if member.mode & 0o111 else 0o644;header.mtime=member.mtime
                digest=hashlib.sha256()
                with archive.extractfile(member) as incoming:
                    class Checked:
                        def read(self,size):
                            data=incoming.read(size);digest.update(data);return data
                    target.addfile(header,Checked())
                if digest.hexdigest()!=needed[member.name]:raise ValueError('Retained source bytes changed')
    if observed!=needed.keys():raise ValueError('Retained source member missing')


def groups(inventory):
    result={}
    def visit(c):
        for category in ['sources','license_files','build_inputs']:
            for r in c[category]:
                previous=result.setdefault(filename(r['asset']),{}).setdefault(r['member'],r['sha256'])
                if previous!=r['sha256']:raise ValueError('Conflicting source member binding')
        for child in c.get('bundled_components',[]):visit(child)
    for c in inventory['components']:visit(c)
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for key in ['base','supplement','packages','notices','output']:p.add_argument('--'+key,type=Path,required=True)
    p.add_argument('--architecture',choices=['amd64','arm64'],required=True)
    args=p.parse_args();repo=Path(__file__).resolve().parents[1]
    if not args.output.is_dir() or any(args.output.iterdir()):raise ValueError('Empty owned output required')
    manifest=json.loads((args.base/'manifest.json').read_text());assets=manifest['assets']
    inventories=[a for a in assets if a['role']=='licenses' and a.get('architecture') in {args.architecture,'all'}]
    if len(inventories)!=1:raise ValueError('One exact retained component inventory required')
    original=args.base/filename(inventories[0]['name'])
    if original.is_symlink() or sha256(original)!=inventories[0]['sha256']:
        raise ValueError('Retained source inventory changed')
    inventory=json.loads(original.read_text());supplement=json.loads((args.supplement/'source-closures.json').read_text())
    if (supplement.get('kind')!='preferred-source-closure-supplement'
            or supplement.get('target_architecture')!=args.architecture
            or supplement.get('version')!=manifest['version']):
        raise ValueError('Source supplement belongs to another target')
    rows={}
    for line in args.packages.read_text().splitlines():
        name,*row=line.split('\t')
        if (not re.fullmatch('[a-z0-9][a-z0-9+.-]*',name) or len(row)!=4 or name in rows
                or row[1] not in {args.architecture,'all'}):raise ValueError('Exact native package identities required')
        rows[name]=row
    lock=json.loads((repo/'source-lock.json').read_text())
    for c in inventory['components']:
        origin=c.get('source_origin',{}).get('stream_archive_sha256')
        if origin is not None and origin!=lock['stream']['sha256']:raise ValueError('Retained public Stream baseline differs')
    sdk=json.loads((repo/'containers/native/salt-sources.lock.json').read_text())
    decisions=apply(inventory,supplement['fragment'],rows,lock,sdk)
    approvals=json.loads((repo/'license-audit.json').read_text())['components'];notice_inputs=[];changed={}
    def child_permission(c):
        matches=[approval_for(approvals,c['name'],c['version'],c['source_identity'],repo,
                             copyright_sha256=r['sha256']) for r in c['license_files']]
        approved=next((r for r in matches if r.get('redistributable') is True and r.get('license')),None)
        if approved is None:raise ValueError('Unreviewed retained dependency: '+c['name']+' '+c['version'])
        c.update(redistributable=True,license=approved['license'])
        for child in c.get('bundled_components',[]):child_permission(child)
    for c in inventory['components']:
        notice=args.notices/c['name']/'copyright'
        if notice.is_symlink() or not notice.is_file() or not notice.resolve().is_relative_to(args.notices.resolve()):
            raise ValueError('Regular actual installed notice required')
        approved=approval_for_notice(approvals,c['source_package'],c['source_version'],c['source_identity'],notice,repo)
        if approved.get('redistributable') is not True or not approved.get('license'):
            raise ValueError('Unreviewed target source/notice: '+c['name'])
        c.update(redistributable=True,license=approved['license'])
        if approved.get('notice_derivation'):c['notice_derivation']=approved['notice_derivation']
        matches=[r for r in c['license_files'] if r['sha256']==sha256(notice)]
        if matches:c['installed_notice']=matches[0]
        else:
            member='installed-notices/'+c['name']+'/copyright';changed[c['name']]=member
            notice_inputs.append((notice,member,'license_files'))
        for child in c.get('bundled_components',[]):child_permission(child)
    receipt=args.output/'refresh-recipe.json'
    receipt.write_text(json.dumps({'kind':'preferred-source-refresh-only','original_source_commit':manifest['source_commit'],
        'original_inventory_sha256':inventories[0]['sha256'],'canonical_source_commit':supplement['canonical_source_commit'],
        'preferred_source_bindings':decisions,'binary_compilation':'NOT_PERFORMED','release_clearance':'NOT_CLEARED'},indent=2)+'\n')
    bindings=args.output/f"shirube-os-{manifest['version']}-{args.architecture}-source-refresh-inputs.tar.gz"
    records=bundle(notice_inputs+[(Path(__file__).resolve(),'refresh-retained-sources.py','sources'),
        (repo/'producer/source_reuse.py','source_reuse.py','sources'),(repo/'LICENSE','LICENSE','license_files'),
        (receipt,'refresh-recipe.json','build_inputs')],bindings,1773859634)
    for c in inventory['components']:
        c['build_inputs'].extend(records['build_inputs'])
        if c['name'] in changed:
            reference=next(r for r in records['license_files'] if r['member']==changed[c['name']])
            c['license_files'].append(reference);c['installed_notice']=reference
    wanted=groups(inventory);outputs=[];remap={}
    for a in supplement['assets']:
        source=args.supplement/filename(a['name'])
        if source.is_symlink() or source.stat().st_size!=a['size'] or sha256(source)!=a['sha256']:
            raise ValueError('Preferred source supplement changed')
        shutil.copyfile(source,args.output/source.name);outputs.append(a)
    sources=[a for a in assets if a['role']=='source' and a.get('architecture') in {args.architecture,'all'}]
    for index,a in enumerate(sources):
        name=filename(a['name']);needed=wanted.get(name,{})
        if not needed:continue
        source=args.base/name
        if source.is_symlink() or source.stat().st_size!=a['size'] or sha256(source)!=a['sha256']:
            raise ValueError('Retained source asset changed')
        present=set()
        with tarfile.open(source) as archive:
            for member in archive:
                if member.isdir():continue
                if member.name in present:raise ValueError('Duplicate retained source member')
                present.add(member.name)
        if not needed.keys()<=present:raise ValueError('Retained build input missing')
        if needed.keys()==present:
            destination=args.output/name;shutil.copyfile(source,destination)
        else:
            destination=args.output/f"shirube-os-{manifest['version']}-{args.architecture}-retained-source-{index:04d}.tar.gz"
            prune_archive(source,destination,needed)
            remap[name]=destination.name
        outputs.append({'name':destination.name,'role':'source','architecture':args.architecture,
                        'size':destination.stat().st_size,'sha256':sha256(destination)})
        print('RETAINED_SOURCE_VERIFIED: '+str(index+1)+'/'+str(len(sources)),flush=True)
    def rename(c):
        for category in ['sources','license_files','build_inputs']:
            for r in c[category]:
                if r['asset'] in remap:r['asset']=remap[r['asset']]
        for child in c.get('bundled_components',[]):rename(child)
    for c in inventory['components']:rename(c)
    outputs.append({'name':bindings.name,'role':'source','architecture':args.architecture,
                    'size':bindings.stat().st_size,'sha256':sha256(bindings)})
    print('STAGE: verify all refreshed source bytes, original grants and target bindings',flush=True)
    verify_sources(args.output,inventory,outputs)
    target=args.output/f"shirube-os-{manifest['version']}-{args.architecture}-licenses.json"
    target.write_text(json.dumps(inventory,indent=2)+'\n')
    outputs.append({'name':target.name,'role':'licenses','architecture':args.architecture,
                    'size':target.stat().st_size,'sha256':sha256(target)})
    result={**manifest,'assets':outputs,'source_license_clearance':'PASS','release_clearance':'NOT_CLEARED',
            'source_refresh':'Verified source-only refresh; target binaries require independent verification',
            'upstream':{'version':lock['stream']['version'],'sha256':lock['stream']['sha256'],
                        'vpp_commit':lock['external_git']['vpp']['commit'],
                        'vpp_patches_commit':lock['external_git']['vpp-patches']['commit']}}
    (args.output/'manifest.json').write_text(json.dumps(result,indent=2)+'\n')
    print('SOURCE_REFRESH_PASS; no binary/release clearance',flush=True)


if __name__=='__main__':main()
