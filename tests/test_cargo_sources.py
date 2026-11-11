"""Authenticated parent lock coverage and original crate notice retention."""
import hashlib
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from producer.cargo_sources import inventory, retain
from producer.collect import http_cargo_dependencies
from producer.sources import sha256


class CargoSourceTests(unittest.TestCase):
    def fixture(self, root, variant=None):
        cache=root/'cache';cache.mkdir()
        crate=cache/'example-1.0.0.crate'
        with tarfile.open(crate,'w:gz') as target:
            for name,data in [('Cargo.toml',b'[package]\nname="example"\nversion="1.0.0"\n'),
                              ('LICENSE',b'fixture upstream legal terms\n')]:
                member=tarfile.TarInfo('example-1.0.0/'+name);member.size=len(data)
                target.addfile(member,io.BytesIO(data))
        text=('version = 3\n[[package]]\nname="parent"\nversion="1.0.0"\n'
              '[[package]]\nname="example"\nversion="1.0.0"\n'
              'source="registry+https://github.com/rust-lang/crates.io-index"\n'
              'checksum="'+sha256(crate)+'"\n')
        if variant=='git':text=text.replace('registry+https://github.com/rust-lang/crates.io-index','git+https://example.test/source')
        if variant=='local':text+='[[package]]\nname="hidden"\nversion="1.0.0"\n'
        if variant=='duplicate':text+=text[text.index('[[package]]\nname="example"'):]
        raw=text.encode();archive=root/'parent.tar.gz'
        with tarfile.open(archive,'w:gz') as target:
            member=tarfile.TarInfo('parent/Cargo.lock');member.size=len(raw)
            target.addfile(member,io.BytesIO(raw))
        declaration={'source_sha256':sha256(archive),'member':'parent/Cargo.lock',
                     'sha256':hashlib.sha256(raw).hexdigest(),'package':{'name':'parent','version':'1.0.0'}}
        return archive,declaration,cache

    def test_complete_registry_sources_original_notices_and_collector_binding(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);archive,declaration,cache=self.fixture(root)
            source=root/'source';(source/'inputs').mkdir(parents=True)
            original=source/'inputs/parent.tar.gz';original.write_bytes(archive.read_bytes())
            destination=source/'cargo/parent'
            with patch('producer.salt_cargo.download') as download:
                _,records=retain(original,declaration,destination,cache)
                download.assert_not_called()
            record={'name':'parent','cargo':{'parent':declaration,'components':records}}
            guest=root/'guest';(guest/'etc').mkdir(parents=True)
            crate=records[0];installed='third-party/parent/cargo/example-1.0.0/copyright'
            path=guest/'usr/share/doc/vyos-http-api-tools'/installed;path.parent.mkdir(parents=True)
            path.write_bytes((destination/crate['notice_member']).read_bytes())
            self.assertIn(b'fixture upstream legal terms',path.read_bytes())
            crate['license_notice']={'path':installed,'sha256':sha256(path)}
            fixed={'name':'parent','cargo_lock':{k:v for k,v in declaration.items() if k!='source_sha256'},
                   'source':{'filename':'parent.tar.gz','sha256':sha256(original)}}
            lock=root/'http.lock';lock.write_text(json.dumps({'packages':[fixed]}))
            real_read=Path.read_text
            def read(path,*args,**kwargs):
                return real_read(lock if path==Path('/src/native/http-api.lock.json') else path,*args,**kwargs)
            with patch.object(Path,'read_text',read):
                children=http_cargo_dependencies(guest,source,record)
                self.assertEqual(children[0]['name'],'cargo:example')
                record['cargo']['components']=[]
                with self.assertRaisesRegex(ValueError,'incomplete'):http_cargo_dependencies(guest,source,record)
                record['cargo']['components']=records
                path.write_text('changed legal terms')
                with self.assertRaisesRegex(ValueError,'notice differs'):http_cargo_dependencies(guest,source,record)
            proof=json.loads((destination/'components.json').read_text())
            self.assertEqual(proof['binary_reconstruction'],'NOT_RUN')
            self.assertEqual(proof['license_clearance'],'NOT_CLEARED')

    def test_source_lock_checksums_and_unfixed_or_hidden_packages_are_rejected(self):
        for variant in ['git','local','duplicate','parent-hash','lock-hash']:
            with self.subTest(variant=variant),tempfile.TemporaryDirectory() as temporary:
                archive,declaration,_=self.fixture(Path(temporary),variant)
                if variant=='parent-hash':declaration['source_sha256']='0'*64
                if variant=='lock-hash':declaration['sha256']='0'*64
                with self.assertRaises(ValueError):inventory(archive,declaration)

    def test_fixed_git_sources_require_exact_commit_archive_and_package_binding(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);archive,declaration,cache=self.fixture(root)
            # Keep one registry dependency, adding a Git workspace dependency.
            with tarfile.open(archive) as source:raw=source.extractfile('parent/Cargo.lock').read()
            commit='a'*40;git_id='git+https://github.com/example/fixture.git?branch=release#'+commit
            raw+=('\n[[package]]\nname="git-example"\nversion="2.0.0"\nsource="'+git_id+'"\n').encode()
            with tarfile.open(archive,'w:gz') as target:
                member=tarfile.TarInfo('parent/Cargo.lock');member.size=len(raw);target.addfile(member,io.BytesIO(raw))
            original=root/'git.tar.gz'
            with tarfile.open(original,'w:gz') as target:
                for name,data in [('LICENSE',b'fixture Git source notice\n'),
                                  ('crate/Cargo.toml',b'[package]\nname="git-example"\nversion="2.0.0"\n')]:
                    member=tarfile.TarInfo('fixture-'+commit+'/'+name);member.size=len(data);target.addfile(member,io.BytesIO(data))
            fixed={'source':git_id,'url':'https://codeload.github.com/example/fixture/tar.gz/'+commit,
                   'sha256':sha256(original),'archive_root':'fixture-'+commit,'packages':{'git-example':'crate'}}
            declaration.update(source_sha256=sha256(archive),sha256=hashlib.sha256(raw).hexdigest(),git_sources=[fixed])
            (cache/(sha256(original)+'.tar.gz')).write_bytes(original.read_bytes())
            with patch('producer.salt_cargo.download') as registry_download,patch('producer.cargo_sources.download') as git_download:
                _,records=retain(archive,declaration,root/'retained',cache)
                registry_download.assert_not_called();git_download.assert_not_called()
            self.assertEqual(len(records),2)
            git_record=records[-1]
            self.assertIn('git-archives/',git_record['source_member'])
            self.assertIn(b'fixture Git source notice',(root/'retained'/git_record['notice_member']).read_bytes())
            fixed['packages']['unused']='other'
            with self.assertRaisesRegex(ValueError,'Unused'):inventory(archive,declaration)
            del fixed['packages']['unused'];fixed['url']=fixed['url'].replace(commit,'b'*40)
            with self.assertRaisesRegex(ValueError,'Git identity'):inventory(archive,declaration)
