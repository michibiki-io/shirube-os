"""Synthetic transfers only; no images, package builds or network access."""
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest

from producer.stage_artifacts import digest, inventory, pack, scope, unpack
from producer.sources import sha256


class TransferTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.payload = self.root/'payload'; self.payload.mkdir()
        (self.payload/'notice').write_text('public source and notice fixture')
        self.expected = scope('vpp','amd64',{'source':'a'*64},'sha256:'+'b'*64,{})
        self.commit='c'*40; self.version='v2026.03.0'

    def transfer(self):
        archive=self.root/'transfer.tar'
        return archive,pack(self.payload,archive,self.expected,self.commit,self.version)

    def restore(self,archive,record,**changes):
        args=dict(archive=archive,destination=self.root/'restored',expected=self.expected,
                  commit=self.commit,version=self.version,archive_sha256=record['sha256'])
        args.update(changes);return unpack(**args)

    def rewrite(self,archive,change):
        with tarfile.open(archive) as source:
            entries=[(m,source.extractfile(m).read()) for m in source]
        with tarfile.open(archive,'w') as target:
            for m,data in change(entries): target.addfile(m,io.BytesIO(data) if m.isfile() else None)
        return {'sha256':sha256(archive)}

    def test_round_trip_and_current_commit_binding(self):
        archive,record=self.transfer();restored=self.restore(archive,record)
        self.assertEqual(restored['payload_id'],digest(inventory(self.payload)))
        self.assertEqual((self.root/'restored/notice').read_bytes(),(self.payload/'notice').read_bytes())

    def test_content_identity_does_not_depend_on_commit_attribution(self):
        _,one=self.transfer()
        two=pack(self.payload,self.root/'other.tar',self.expected,'d'*40,self.version)
        self.assertEqual(one['scope']['input_id'],two['scope']['input_id'])
        self.assertEqual(one['payload_id'],two['payload_id'])
        self.assertNotEqual(one['source_commit'],two['source_commit'])

    def test_wrong_commit_arch_recipe_compiler_and_dependency_rejected(self):
        archive,record=self.transfer()
        for value in ('d'*40,):
            with self.assertRaises(ValueError):self.restore(archive,record,commit=value)
        for field,value in [('architecture','arm64'),('inputs',{'source':'d'*64}),
                            ('compiler','sha256:'+'e'*64),('dependencies',{'native':'f'*64})]:
            expected={**self.expected,field:value}
            with self.subTest(field=field),self.assertRaises(ValueError):self.restore(archive,record,expected=expected)
        self.assertFalse((self.root/'restored').exists())

    def test_bad_archive_hash_and_absent_archive_rejected(self):
        archive,record=self.transfer()
        with self.assertRaises(ValueError):self.restore(archive,record,archive_sha256='0'*64)
        with self.assertRaises(FileNotFoundError):self.restore(self.root/'missing.tar',record)

    def test_missing_extra_duplicate_changed_and_link_members_rejected_before_write(self):
        archive,record=self.transfer()
        original=archive.read_bytes()
        def extra(entries):
            m=tarfile.TarInfo('payload/extra');m.size=1
            return entries+[(m,b'x')]
        def link(entries):
            m=tarfile.TarInfo('payload/link');m.type=tarfile.SYMTYPE;m.linkname='/etc/passwd'
            return entries+[(m,b'')]
        def changed(entries):return [(m,b'X'*len(data) if m.name=='payload/notice' else data) for m,data in entries]
        for change in (lambda e:e[:-1],extra,lambda e:e+e[-1:],changed,link):
            archive.write_bytes(original);record=self.rewrite(archive,change)
            with self.subTest(change=change),self.assertRaises(ValueError):self.restore(archive,record)
            self.assertFalse((self.root/'restored').exists())

    def test_source_keys_compiler_scratch_and_symlinks_never_export(self):
        for name,data in [('signing_key.pem',b'private'),('renamed.txt',b'-----BEGIN RSA PRIVATE KEY-----\n'+b'A'*64+b'\n-----END RSA PRIVATE KEY-----\n'),
                          ('machine-id',b'host identity'),('object.o',b'compiler object')]:
            path=self.payload/name;path.write_bytes(data)
            with self.subTest(name=name),self.assertRaises(ValueError):inventory(self.payload)
            path.unlink()
        (self.payload/'link').symlink_to('notice')
        with self.assertRaises(ValueError):inventory(self.payload)

    def test_path_traversal_and_noncanonical_names_rejected(self):
        archive,record=self.transfer()
        for name in ('../outside','/absolute','payload/../outside','payload//notice','payload/\\evil'):
            def change(entries):
                m=tarfile.TarInfo(name);m.size=1
                return entries+[(m,b'x')]
            record=self.rewrite(archive,change)
            with self.subTest(name=name),self.assertRaises(ValueError):self.restore(archive,record)
            self.assertFalse((self.root/'restored').exists())

    def test_delimiter_in_parser_source_is_not_a_private_key(self):
        (self.payload/'parser.c').write_bytes(b'const char *delimiter = "-----BEGIN PRIVATE KEY-----";')
        self.assertEqual(len(inventory(self.payload)),2)
