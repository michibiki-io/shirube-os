"""No Docker invocation: synthetic docker-save files and mocked load/inspect."""
import hashlib
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from producer import compiler_artifact as ca
from producer.sources import sha256


class CompilerArtifactTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name) / 'artifact'
        self.directory.mkdir()
        self.expected = dict(schema_version=1, source_commit='a'*40, architecture='amd64',
                             os='linux', run_id='123', inputs_id='b'*64)
        self.config = json.dumps({'architecture': 'amd64', 'os': 'linux'}).encode()
        self.image = 'sha256:' + hashlib.sha256(self.config).hexdigest()
        self.write_archive()
        self.manifest = {**self.expected, 'image_id': self.image,
                         'archive_bytes': self.archive.stat().st_size,
                         'archive_sha256': sha256(self.archive)}
        self.write_manifest()

    @property
    def archive(self):
        return self.directory / 'compiler.tar'

    def write_archive(self, extra=None, config=None, count=1):
        config = self.config if config is None else config
        entries = [('config.json', config), ('manifest.json', json.dumps(
            [{'Config': 'config.json', 'RepoTags': [], 'Layers': []}]*count).encode())]
        if extra: entries.append(extra)
        with tarfile.open(self.archive, 'w') as tar:
            for name, data in entries:
                info=tarfile.TarInfo(name);info.size=len(data)
                tar.addfile(info,io.BytesIO(data))

    def write_manifest(self):
        (self.directory/'compiler.json').write_text(json.dumps(self.manifest))

    def refreshed_archive(self):
        self.manifest.update(archive_bytes=self.archive.stat().st_size, archive_sha256=sha256(self.archive))
        self.write_manifest()

    def rejected_before_load(self):
        with patch.object(ca.subprocess,'run') as run:
            with self.assertRaises((ValueError,KeyError)):
                ca.load(self.directory,self.expected)
            run.assert_not_called()

    def test_exact_load_and_inspect_for_both_architectures(self):
        for arch in ('amd64','arm64'):
            with self.subTest(architecture=arch):
                self.expected['architecture']=arch
                config=json.dumps({'architecture':arch,'os':'linux'}).encode()
                self.image='sha256:'+hashlib.sha256(config).hexdigest()
                self.write_archive(config=config)
                self.manifest.update(self.expected,image_id=self.image)
                self.refreshed_archive()
                with patch.object(ca.subprocess,'run') as run, patch.object(ca,'inspect') as inspect:
                    self.assertEqual(ca.load(self.directory,self.expected),self.image)
                    run.assert_called_once_with(['docker','image','load','--input',str(self.archive)],check=True)
                    inspect.assert_called_once_with(self.image,arch)

    def test_manifest_mismatches_fail_before_docker(self):
        for key,value in [('source_commit','c'*40),('architecture','arm64'),('run_id','124'),
                          ('inputs_id','d'*64),('os','windows'),('schema_version',2),
                          ('archive_bytes',1),('archive_sha256','0'*64),('image_id','mutable:tag')]:
            with self.subTest(field=key):
                original=self.manifest[key];self.manifest[key]=value;self.write_manifest()
                self.rejected_before_load()
                self.manifest[key]=original
        self.manifest['extra']='unexpected';self.write_manifest();self.rejected_before_load()

    def test_missing_extra_symlink_and_corruption_fail_before_docker(self):
        for name in ('compiler.json','compiler.tar'):
            path=self.directory/name;data=path.read_bytes();path.unlink()
            self.rejected_before_load();path.write_bytes(data)
        extra=self.directory/'other-architecture.tar';extra.write_bytes(b'x')
        self.rejected_before_load();extra.unlink()
        extra.symlink_to(self.archive);self.rejected_before_load();extra.unlink()
        path=self.directory/'compiler.json';data=path.read_bytes();path.unlink()
        target=Path(self.temp.name)/'manifest';target.write_bytes(data);path.symlink_to(target)
        self.rejected_before_load();path.unlink();path.write_bytes(data)
        with self.archive.open('ab') as stream:stream.write(b'corrupt')
        self.rejected_before_load()

    def test_archive_config_platform_id_duplicates_and_extra_images(self):
        for options in ({'config':b'{"architecture":"arm64","os":"linux"}'},
                        {'config':b'{"architecture":"amd64","os":"windows"}'},
                        {'count':2}, {'extra':('../escape',b'x')},
                        {'extra':('config.json',self.config)}):
            with self.subTest(options=options):
                self.write_archive(**options);self.refreshed_archive();self.rejected_before_load()
        self.write_archive();self.manifest['image_id']='sha256:'+'f'*64
        self.refreshed_archive();self.rejected_before_load()

    def test_loaded_image_identity_and_platform_are_checked(self):
        for field,value in [('Id','sha256:'+'f'*64),('Architecture','arm64'),('Os','windows')]:
            record=dict(Id=self.image,Architecture='amd64',Os='linux');record[field]=value
            with patch.object(ca.subprocess,'check_output',return_value=json.dumps([record]).encode()):
                with self.assertRaises(ValueError):ca.inspect(self.image,'amd64')

    def test_export_uses_only_immutable_image_and_records_archive(self):
        destination=Path(self.temp.name)/'export'
        def save(command,check):
            self.assertEqual(command,['docker','image','save','--output',str(destination/'compiler.tar'),self.image])
            (destination/'compiler.tar').write_bytes(self.archive.read_bytes())
        with patch.object(ca,'inspect') as inspect, patch.object(ca.subprocess,'run',side_effect=save):
            ca.export(destination,self.expected,self.image)
        inspect.assert_called_once_with(self.image,'amd64')
        self.assertEqual(json.loads((destination/'compiler.json').read_text()),self.manifest)

    def test_identity_binds_all_tracked_image_inputs_and_base(self):
        fixture=Path(self.temp.name)/'Dockerfile';fixture.write_text('FROM locked')
        def output(command,**kwargs):
            if command[:3]==['git','rev-parse','HEAD']:return 'a'*40+'\n'
            if command[:3]==['git','status','--porcelain']:return ''
            self.assertEqual(command,['git','ls-files','-z','--',*ca.INPUTS])
            return (str(fixture)+'\0').encode()
        with patch.object(ca.subprocess,'check_output',side_effect=output):
            first=ca.identity('a'*40,'amd64','123')
            fixture.write_text('FROM changed')
            second=ca.identity('a'*40,'amd64','123')
            arm=ca.identity('a'*40,'arm64','123')
            self.assertNotEqual(first['inputs_id'],second['inputs_id'])
            self.assertNotEqual(second['inputs_id'],arm['inputs_id'])
        with patch.object(ca.subprocess,'check_output',return_value='wrong'):
            with self.assertRaises(ValueError):ca.identity('a'*40,'amd64','123')
