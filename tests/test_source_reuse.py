"""Retained native identities cannot be replaced by an unrelated source closure."""
import copy
import hashlib
import io
from pathlib import Path
import runpy
import tarfile
import tempfile
import unittest
from producer.source_reuse import apply,upgrade_binding

prune=runpy.run_path(str(Path(__file__).resolve().parents[1]/'scripts/refresh-retained-sources.py'))['prune_archive']


def ref(member,sha='a'*64,asset='retained.tar.gz'):
    return {'asset':asset,'member':member,'sha256':sha}


def component(name):
    return {'name':name,'version':'1','architecture':'arm64','source_package':name,'source_version':'1',
            'source_identity':'a'*64,'source_origin':{},'license':'UNREVIEWED','redistributable':False,
            'sources':[ref('source/'+name)],'license_files':[ref('copyright')],
            'build_inputs':[ref('project-source.tar')],'bundled_components':[]}


class SourceReuseTests(unittest.TestCase):
    def test_unreferenced_original_source_payload_is_physically_removed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);original=root/'old.tar';target=root/'new.tar.gz'
            with tarfile.open(original,'w') as archive:
                for name,content in [('library.c',b'preferred library source'),('excluded-original.tar.xz',b'unreviewed source-only material')]:
                    info=tarfile.TarInfo(name);info.size=len(content);info.mode=0o755
                    archive.addfile(info,io.BytesIO(content))
            prune(original,target,{'library.c':hashlib.sha256(b'preferred library source').hexdigest()})
            with tarfile.open(target) as archive:
                self.assertEqual(archive.getnames(),['library.c'])
                self.assertEqual(archive.getmember('library.c').mode,0o755)

    def test_pruning_refuses_changed_source_bytes_and_escaping_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);original=root/'old.tar'
            with tarfile.open(original,'w') as archive:
                info=tarfile.TarInfo('library.c');info.size=1;archive.addfile(info,io.BytesIO(b'x'))
            with self.assertRaisesRegex(ValueError,'bytes changed'):prune(original,root/'changed.tar.gz',{'library.c':'f'*64})
            with tarfile.open(root/'unsafe.tar','w') as archive:
                info=tarfile.TarInfo('../escape');info.size=1;archive.addfile(info,io.BytesIO(b'x'))
            with self.assertRaisesRegex(ValueError,'Unsafe'):prune(root/'unsafe.tar',root/'unsafe.tar.gz',{'../escape':hashlib.sha256(b'x').hexdigest()})

    def fixture(self):
        alpha=component('alpha');vpp=component('vpp');integration=component('shirube-integration')
        vpp['source_origin']={'stream_archive_sha256':'a'*64,'external_git':{}}
        raw=ref('source/build/external/downloads/dependency.tar.gz');vpp['sources'].append(raw)
        integration['source_origin']={'netbird_source_commit':'b'*40,'netbird_source_sha256':'b'*64,
                                      'headless_asset_sha256':'c'*64}
        child=component('vpp-native:dependency');child['architecture']='all'
        child['sources']=[ref('vpp-preferred-inputs/dependency.tar.gz','b'*64,'supplement.tar.gz')]
        child['build_inputs']=[{**raw,'asset':'canonical-amd64.tar.gz'}]
        preferred=copy.deepcopy(vpp['source_origin']);preferred['vpp_retained_inputs']={'dependency':'fixture'}
        fragment={'parents':{},'children':[],
                  'vpp':{'source_identity':'d'*64,'source_origin':preferred,'bundled_components':[child]},
                  'geoip':{'origin':{'geoip_source_sha256':'e'*64},'children':[component('data:dbip-country-lite')]}}
        inventory={'components':[alpha,vpp,integration]}
        rows={c['name']:['1','arm64',c['name'],'1'] for c in inventory['components']}
        lock={'netbird':{'source_commit':'b'*40,'source_sha256':'b'*64,'assets':{'arm64':{'sha256':'c'*64}}}}
        return inventory,fragment,rows,lock,{'components':[]}

    def test_unknown_source_change_is_not_accepted_by_matching_version(self):
        old=component('sdk:library');new=copy.deepcopy(old);new['source_identity']='b'*64
        new['sources']=[ref('preferred','b'*64)]
        self.assertFalse(upgrade_binding(old,new,{'components':[]}))
        new['version']='2'
        with self.assertRaisesRegex(ValueError,'version changed'):upgrade_binding(old,new,{'components':[]})

    def test_locked_sdk_export_preserves_arm_binary_and_replaces_original_member_references(self):
        args=self.fixture();inventory,fragment,rows,lock,sdk=args
        old=component('sdk:library');old['binary_input']={'architecture':'arm64','sha256':'f'*64}
        new=copy.deepcopy(old);new['source_identity']='b'*64;new['sources']=[ref('preferred','b'*64,'supplement.tar.gz')]
        new['binary_input']={'architecture':'amd64','sha256':'e'*64}
        inventory['components'][0]['bundled_components']=[old]
        inventory['components'][0]['sources'].extend(old['sources'])
        fragment['children']=[new]
        sdk['components']=[{'name':'sdk:library','version':'1','sha256':'a'*64,'source_export':{'sha256':'b'*64}}]
        # Use a distinct original archive digest; shared fixture recipes and
        # copyright references must not accidentally model the same source blob.
        old['source_identity']='1'*64;old['sources'][0]['sha256']='1'*64
        inventory['components'][0]['sources'][-1]['sha256']='1'*64;sdk['components'][0]['sha256']='1'*64
        apply(*args)
        actual=inventory['components'][0]['bundled_components'][0]
        self.assertEqual(actual['binary_input'],{'architecture':'arm64','sha256':'f'*64})
        self.assertFalse(any(r['sha256']=='1'*64 for c in inventory['components']
                             for k in ['sources','license_files','build_inputs'] for r in c[k]))

    def test_vpp_uses_actual_retained_raw_archive_not_canonical_other_architecture_input(self):
        args=self.fixture();apply(*args)
        vpp=args[0]['components'][1];child=vpp['bundled_components'][0]
        raw=[r for r in child['build_inputs'] if r['member'].startswith('source/build/external/downloads/')]
        self.assertEqual(len(raw),1);self.assertEqual(raw[0]['asset'],'retained.tar.gz')
        self.assertEqual(vpp['architecture'],'arm64')

    def test_different_vpp_source_or_raw_input_is_rejected(self):
        args=self.fixture();args[1]['vpp']['source_origin']['external_git']={'vpp':{'commit':'changed'}}
        with self.assertRaisesRegex(ValueError,'VPP source revisions'):apply(*args)
        args=self.fixture();args[1]['vpp']['bundled_components'][0]['build_inputs'][0]['sha256']='f'*64
        with self.assertRaisesRegex(ValueError,'VPP input differs'):apply(*args)

    def test_netbird_other_architecture_asset_cannot_replace_actual_target_binding(self):
        args=self.fixture();args[0]['components'][2]['source_origin']['headless_asset_sha256']='0'*64
        with self.assertRaisesRegex(ValueError,'NetBird binary/source binding'):apply(*args)

    def test_debian_preferred_source_requires_original_descriptor_identity(self):
        args=self.fixture();replacement=copy.deepcopy(args[0]['components'][0])
        replacement['source_origin']={'debian_preferred_source_export':{'original_descriptor_sha256':'f'*64}}
        args[1]['parents']={'alpha':replacement}
        with self.assertRaisesRegex(ValueError,'original descriptor'):apply(*args)
