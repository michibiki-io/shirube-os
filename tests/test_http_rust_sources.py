"""Original Rust source/legal coverage and exact shipped-extension bindings."""
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from producer.http_rust_sources import retain,write_notices,extensions
from producer.sources import sha256


class HttpRustSourceTests(unittest.TestCase):
    def fixture(self,root,variant=None):
        cache=root/'cache';cache.mkdir();archive=cache/'rustc-1.82.0-src.tar.gz'
        with tarfile.open(archive,'w:gz') as target:
            for name,data in [('LICENSE-MIT',b'fixture MIT terms'),('LICENSE-APACHE',b'fixture Apache terms'),
                              ('COPYRIGHT',b'fixture copyright'),('library/example/LICENSE',b'embedded terms')]:
                if variant=='missing' and name=='COPYRIGHT':continue
                member=tarfile.TarInfo('rustc-1.82.0-src/'+name);member.size=len(data)
                target.addfile(member,io.BytesIO(data))
            if variant in {'link','escape','cycle'}:
                member=tarfile.TarInfo('rustc-1.82.0-src/library/LICENSE');member.type=tarfile.SYMTYPE
                member.linkname={'link':'../LICENSE-MIT','escape':'../../outside/LICENSE','cycle':'LICENSE'}[variant];target.addfile(member)
        record={'filename':archive.name,'url':'https://example.test/'+archive.name,'sha256':sha256(archive)}
        lock=root/'lock.json';lock.write_text(json.dumps({'version':'1.82.0','rustc_version_output':'rustc 1.82.0 (fixture)','source':record}))
        return cache,archive,lock

    def test_original_source_and_nested_legal_files_are_retained_without_network(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);cache,archive,lock=self.fixture(root)
            inputs=root/'inputs';inputs.mkdir()
            with patch('producer.http_rust_sources.download') as download:
                data=retain(lock,inputs,root/'notices',cache);download.assert_not_called()
            self.assertEqual(sha256(inputs/archive.name),sha256(archive))
            self.assertIn('rustc-1.82.0-src/library/example/LICENSE',data['source_legal_members'])
            self.assertIn(b'embedded terms',(root/'notices/copyright').read_bytes())
            members=write_notices(archive,'1.82.0',root/'recreated')
            self.assertEqual(members,data['source_legal_members'])
            self.assertEqual(sha256(root/'recreated'),data['notice_sha256'])
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);_,archive,_=self.fixture(root,'link')
            members=write_notices(archive,'1.82.0',root/'notice')
            self.assertIn('rustc-1.82.0-src/library/LICENSE',members)
            self.assertEqual((root/'notice').read_bytes().count(b'fixture MIT terms'),2)

    def test_missing_original_legal_files_links_and_changed_inputs_are_rejected(self):
        for variant in ['missing','escape','cycle','hash']:
            with self.subTest(variant=variant),tempfile.TemporaryDirectory() as temporary:
                root=Path(temporary);cache,archive,lock=self.fixture(root,variant)
                inputs=root/'inputs';inputs.mkdir()
                if variant=='hash':archive.write_bytes(b'changed public source')
                with self.assertRaises(ValueError):retain(lock,inputs,root/'notices',cache)

    def test_source_compiler_and_original_wheel_are_bound_to_actual_extension_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);site=root/'site';source=root/'source';(source/'inputs').mkdir(parents=True)
            records=[]
            for name in ['pydantic-core','watchfiles']:
                directory=site/name.replace('-','_');directory.mkdir(parents=True)
                binary=directory/'extension.so';binary.write_bytes(('fixture '+name).encode())
                archive=source/'inputs'/(name+'.whl')
                with zipfile.ZipFile(archive,'w') as target:target.writestr(str(binary.relative_to(site)),binary.read_bytes())
                records.append({'name':name,'cargo':{},'wheel':{'filename':archive.name,'sha256':sha256(archive)}})
            compiler='rustc version 1.82.0 (fixture)'
            with patch('producer.http_rust_sources.subprocess.check_output',return_value='  [0] '+compiler+'\n'):
                self.assertEqual(len(extensions(site,source,records,compiler)),2)
                binary.write_bytes(b'changed installed code')
                with self.assertRaisesRegex(ValueError,'differs from the pinned'):extensions(site,source,records,compiler)
            with patch('producer.http_rust_sources.subprocess.check_output',return_value='  [0] rustc version 1.81.0\n'):
                with self.assertRaisesRegex(ValueError,'does not match'):extensions(site,source,records,compiler)
