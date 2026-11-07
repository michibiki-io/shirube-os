"""Synthetic metadata fixtures test rejection; they are never boot evidence."""
import copy
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from producer.release import CHECKS, validate, reconcile_published
from producer.sources import sha256


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.commit, self.version = 'a'*40, 'v0.1.0'
        self.manifest = {'schema_version':1, 'manifest_kind':'release', 'provider':'michibiki-io', 'project':'Shirube OS', 'version':self.version,
                         'source_commit':self.commit, 'source_clean':True, 'upstream':{'version':'2026.03', 'sha256':'e9f5b20261b166b72c60b591d00b173673fe704c5f6aaf90404db457c2804910', 'signature':'PASS', 'vpp_commit':'8a3f587695fe6ad4d8297bd4b2f331b7262698d9', 'vpp_patches_commit':'9cab3c94679592ef27a58e6a0e9becdd9f847754', 'substitution':'public VPP substitute, not vendor-identical'},
                         'architectures':{}, 'assets':[]}
        source = self.root/'source.tar.gz'
        with tarfile.open(source, 'w:gz') as archive:
            for name in ['source', 'LICENSE', 'recipe']:
                member = tarfile.TarInfo(name); data = ('synthetic '+name).encode(); member.size = len(data)
                archive.addfile(member, io.BytesIO(data))
        self.add(source, 'source', 'all', None)
        import hashlib
        def reference(name):
            return {'asset':source.name, 'member':name, 'sha256':hashlib.sha256(('synthetic '+name).encode()).hexdigest()}
        for architecture in ['amd64', 'arm64']:
            generation = ('b' if architecture == 'amd64' else 'c')*64
            self.manifest['architectures'][architecture] = {
                'checks':{x:'PASS' for x in CHECKS}, 'oci':'NOT_RUN', 'virtual_size':50*1024**3,
                'standalone':True, 'vmdk_subformat':'streamOptimized', 'signing_certificate_sha256':'d'*64,
                'repository_fingerprint':'TEST-FIXTURE', 'generation':generation, 'firmware':'UEFI',
                'console':{'device':'ttyAMA0' if architecture=='arm64' else 'ttyS0','baud':115200},
                'provisioning':{'method':'console-enrollment-v1','default_credentials':False}}
            for role in ['iso', 'qcow2', 'vmdk']:
                file = self.root/f'shirube-os-{self.version}-{architecture}.{role}'; file.write_bytes(b'synthetic fixture, never a real image')
                self.add(file, role, architecture, generation)
            inventory = self.root/f'{architecture}-licenses.json'
            package = {'name':'synthetic', 'version':'1', 'architecture':architecture}
            inventory.write_text(json.dumps({'shipped_packages':[package], 'components':[{
                **package, 'license':'MIT', 'redistributable':True, 'sources':[reference('source')],
                'license_files':[reference('LICENSE')], 'build_inputs':[reference('recipe')]}]}))
            self.add(inventory, 'licenses', architecture, generation)
            evidence = self.root/f'{architecture}-evidence.json'
            evidence.write_text(json.dumps({'source_commit':self.commit, 'version':self.version,
                'architecture':architecture, 'generation':generation,
                'checks':{x:{'status':'PASS','observations':['synthetic validator fixture only']} for x in CHECKS}}))
            self.add(evidence, 'evidence', architecture, generation)
        self.write()

    def add(self, path, role, architecture, generation):
        item = {'name':path.name, 'size':path.stat().st_size, 'sha256':sha256(path),
                'role':role, 'architecture':architecture}
        if generation:
            item['generation'] = generation
        self.manifest['assets'].append(item)

    def write(self):
        (self.root/'manifest.json').write_text(json.dumps(self.manifest))
        names = [x['name'] for x in self.manifest['assets']] + ['manifest.json']
        (self.root/'SHA256SUMS').write_text(''.join(f'{sha256(self.root/name)}  {name}\n' for name in names))

    def test_complete_metadata_contract(self):
        validate(self.root, self.commit, self.version)

    def test_required_skips_failures_missing_checks_block(self):
        for value in ['SKIP', 'FAIL', 'NOT_RUN', None]:
            with self.subTest(value=value):
                checks = self.manifest['architectures']['arm64']['checks']
                if value is None:
                    checks.pop('local_boot')
                else:
                    checks['local_boot'] = value
                self.write()
                with self.assertRaises(ValueError):
                    validate(self.root, self.commit, self.version)
                checks['local_boot'] = 'PASS'

    def test_wrong_commit_mixed_generation_oci_claim_and_missing_architecture_block(self):
        original = copy.deepcopy(self.manifest)
        mutations = [lambda x: x.update(source_commit='e'*40),
                     lambda x: x['architectures'].pop('arm64'),
                     lambda x: x['architectures']['amd64'].update(oci='PASS'),
                     lambda x: x['architectures']['amd64'].update(generation='f'*64)]
        for mutation in mutations:
            self.manifest = copy.deepcopy(original); mutation(self.manifest); self.write()
            with self.assertRaises(ValueError):
                validate(self.root, self.commit, self.version)

    def test_asset_tampering_and_missing_checksum_block(self):
        (self.root/f'shirube-os-{self.version}-amd64.iso').write_bytes(b'tampered')
        with self.assertRaises(ValueError):
            validate(self.root, self.commit, self.version)

    def test_unreviewed_license_blocks_even_when_all_claimed_checks_pass(self):
        path=self.root/'amd64-licenses.json'
        inventory=json.loads(path.read_text());inventory['components'][0]['redistributable']=False
        path.write_text(json.dumps(inventory))
        asset=next(x for x in self.manifest['assets'] if x['name']==path.name)
        asset.update(size=path.stat().st_size,sha256=sha256(path));self.write()
        with self.assertRaises(ValueError):validate(self.root,self.commit,self.version)

    def test_published_release_is_immutable(self):
        existing = {'manifest.json':'a'*64, 'SHA256SUMS':'b'*64, 'image.iso':'c'*64}
        self.assertEqual(reconcile_published(existing, dict(existing)), 'NO_OP')
        with self.assertRaises(ValueError):
            reconcile_published(existing, {'manifest.json':'a'*64})
