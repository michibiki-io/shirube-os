"""Small real compiler + adversarial archives; no kernel/image build."""
import hashlib
import io
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import unittest
import zipfile
from unittest.mock import patch

from producer import kernel_compile_cache as cache
from producer.signing import verify_module, MAGIC
from scripts import kernel_compile_cache as host


class CacheArchiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.scope = cache.envelope('amd64', 'a'*64)
        self.store = self.root/'store'; self.store.mkdir()
        self.result = self.store/'a/b/12345R'; self.result.parent.mkdir(parents=True)
        self.result.write_bytes(b'synthetic compiler result')

    def test_roundtrip_rejects_foreign_corrupt_and_never_exports_administration(self):
        (self.store/'ccache.conf').write_text('remote_storage = malicious')
        archive = self.root/'cache.tar'
        cache.export(self.store, archive, self.scope)
        with tarfile.open(archive) as source:
            self.assertEqual(source.getnames(), ['manifest.json', 'a/b/12345R'])
        cache.restore(archive, self.root/'good', self.scope)
        self.assertEqual((self.root/'good/a/b/12345R').read_bytes(), self.result.read_bytes())
        for expected in [cache.envelope('arm64', 'a'*64), cache.envelope('amd64', 'b'*64)]:
            with self.assertRaises(ValueError): cache.restore(archive, self.root/'bad', expected)
            self.assertFalse((self.root/'bad').exists())
        data = archive.read_bytes().replace(b'synthetic compiler result', b'corrupted compiler result')
        archive.write_bytes(data)
        with self.assertRaises(ValueError): cache.restore(archive, self.root/'bad', self.scope)
        self.assertFalse((self.root/'bad').exists())

    def test_launcher_rejects_aliases_overlap_and_non_kernel_mounts(self):
        spec = importlib.util.spec_from_file_location('kernel_cache_launcher', 'scripts/run-native-stage.py')
        launcher = importlib.util.module_from_spec(spec); spec.loader.exec_module(launcher)
        mount = self.root/'mount'; mount.mkdir()
        other = self.root/'inputs'; other.mkdir()
        launcher.validate_kernel_cache('kernel', mount, (other,))
        for stage, root, others in [('native',mount,(other,)),('kernel',mount,(self.root,)),
                                    ('kernel',mount,(mount/'nested',))]:
            with self.assertRaises(ValueError):launcher.validate_kernel_cache(stage,root,others)
        alias=self.root/'alias';alias.symlink_to(mount,target_is_directory=True)
        with self.assertRaises(ValueError):launcher.validate_kernel_cache('kernel',alias,())
        child=mount/'child';child.mkdir()
        with self.assertRaises(ValueError):launcher.validate_kernel_cache('kernel',alias/'child',())
        child.rmdir(); (mount/'signing_key.pem').write_text('not a cache input')
        with self.assertRaises(ValueError):launcher.validate_kernel_cache('kernel',mount,())

    def test_backend_content_identity_changes_with_preserved_timestamps(self):
        paths = {}
        for name in ['gcc','as','ld','ccache','cc1','collect2','lto1','lto-wrapper','liblto_plugin.so']:
            paths[name] = self.root/name; paths[name].write_text(name)
        with patch.object(cache.shutil, 'which', side_effect=lambda n: str(paths[n])), \
             patch.object(cache.subprocess, 'check_output', side_effect=lambda a, **kw: str(paths[a[-1].split('=')[1]])), \
             patch.object(cache, 'compiler_fingerprint', return_value='sha256:'+'a'*64):
            for name, path in paths.items():
                before = cache.tools(); stamp = path.stat()
                path.write_text(path.read_text()+'changed')
                os.utime(path, ns=(stamp.st_atime_ns,stamp.st_mtime_ns))
                self.assertNotEqual(before, cache.tools(), name)

    def test_bounded_export_and_unsafe_members(self):
        with patch.object(cache, 'FILE_LIMIT', 4):
            with self.assertRaises(ValueError): cache.export(self.store, self.root/'too-big', self.scope)
        for name, kind in [('a/b/leakR', tarfile.SYMTYPE), ('../escape', tarfile.REGTYPE),
                           ('signing_key.pem', tarfile.REGTYPE), ('a/b/12345R', tarfile.FIFOTYPE)]:
            archive = self.root/'unsafe.tar'
            with tarfile.open(archive, 'w') as output:
                member = tarfile.TarInfo(name); member.type = kind
                output.addfile(member)
            with self.subTest(name=name, kind=kind), self.assertRaises(ValueError):
                cache.restore(archive, self.root/'unsafe', self.scope)
            self.assertFalse((self.root/'unsafe').exists())
        (self.store/'signed.ko').write_bytes(b'never export')
        with self.assertRaises(ValueError): cache.export(self.store, self.root/'signed.tar', self.scope)


@unittest.skipUnless(shutil.which('ccache') and shutil.which('gcc') and shutil.which('openssl'),
                     'small real compiler fixture requires ccache, gcc and openssl')
class RealCompilerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.source = self.base/'source'; self.source.mkdir()
        (self.source/'certs').mkdir()
        (self.source/'certs/certificate.c').write_text('const int cert_generation=1;\n')
        (self.source/'header.h').write_text('#define VALUE 1\n')
        (self.source/'generated.h').write_text('#define GENERATED 1\n')
        (self.source/'.config').write_text('CONFIG_MODULE_SIG_ALL=y\n')
        (self.source/'Module.symvers').write_text('fixture-abi-1\n')
        (self.source/'plain.c').write_text('#include "header.h"\n#include "generated.h"\nint value(void){return VALUE+GENERATED;}\n')
        self.mount = self.base/'mount'; self.mount.mkdir()
        self.state = self.base/'state.json'
        self.data = cache.prepare(self.source, self.mount, 'amd64', state=self.state)

    def compile(self, source='plain.c', extra=()):
        args = ['-c', str(self.source/source), '-o', str(self.base/'source/out.o'), *extra]
        command, env = cache.compile_command(args, self.data)
        subprocess.run(command, env=env, check=True, capture_output=True)
        return (self.source/'out.o').read_bytes(), command

    def stats(self):
        text = subprocess.check_output(['ccache', '--print-stats'], env=cache.environment(self.data), text=True)
        return {k:int(v) for k,v in (line.split() for line in text.splitlines())}

    def test_cold_warm_headers_flags_config_arch_compiler_and_abi(self):
        cold, _ = self.compile(); warm, _ = self.compile()
        self.assertEqual(cold, warm)
        self.assertGreaterEqual(self.stats()['cache_miss'], 1)
        self.assertGreaterEqual(self.stats()['preprocessed_cache_hit'], 1)
        misses = self.stats()['cache_miss']
        for header in ['header.h', 'generated.h']:
            p = self.source/header; p.write_text(p.read_text().replace('1', '2'))
            self.compile(); self.assertGreater(self.stats()['cache_miss'], misses)
            misses = self.stats()['cache_miss']
        self.compile(extra=('-DCHANGED_OPTION=1',)); self.assertGreater(self.stats()['cache_miss'], misses)
        # A fresh pre-build source/config namespace, independently for each arch.
        # No claim that this native compiler emits an ARM64 kernel.
        for arch in ['amd64', 'arm64']:
            for member in ['.config', 'Module.symvers', 'plain.c']:
                before = self.data['namespace']
                p = self.source/member; p.write_text(p.read_text()+'\n/* content change */\n')
                self.state.unlink(); shutil.rmtree(self.base/'kernel-ccache')
                self.data = cache.prepare(self.source, self.mount, arch, state=self.state)
                self.assertNotEqual(before, self.data['namespace'])
                self.compile(); self.assertEqual(self.stats()['preprocessed_cache_hit'], 0)
        self.state.unlink(); shutil.rmtree(self.base/'kernel-ccache')
        with patch.object(cache, 'tools', return_value='b'*64):
            changed = cache.prepare(self.source, self.mount, 'arm64', state=self.state)
        self.assertNotEqual(changed['namespace'], self.data['namespace'])

    def test_strict_environment_and_bypass(self):
        for source in ['certs/certificate.c', 'plain.mod.c']:
            (self.source/source).write_text('int certificate_generation=1;\n')
            _, command = self.compile(source)
            self.assertEqual(command[0], '/usr/bin/gcc')
        for args in [['-c', 'a.S', '-o', 'a.o'], ['a.o', '-o', 'vmlinux'], ['@response'],
                     ['-c', 'plain.c', '-o', 'out.o', '-fplugin=plugin.so']]:
            self.assertFalse(cache.eligible(args, self.source))
        with patch.dict(os.environ, {'CCACHE_SLOPPINESS':'time_macros', 'CCACHE_REMOTE_STORAGE':'bad',
                                     'CCACHE_CONFIGPATH':'foreign', 'CCACHE_COMPILERCHECK':'none'}):
            env = cache.environment(self.data)
            self.assertEqual({k for k in env if k.startswith('CCACHE_')}, {'CCACHE_CONFIGPATH'})
        config = Path(self.data['config']).read_text()
        for required in ['direct_mode = false', 'depend_mode = false', 'compiler_check = content',
                         'sloppiness =\n', 'remote_storage =\n', 'max_size = 1GiB']:
            self.assertIn(required, config)

    def test_key_rotation_restored_compiler_hit_and_new_signature_only(self):
        body, _ = self.compile()
        cache.finish(state=self.state)
        # Export contains compiler results only, never cert/private build state.
        with tarfile.open(self.mount/'export.tar') as archive:
            self.assertTrue(all(n == 'manifest.json' or cache.RESULT.fullmatch(n) for n in archive.getnames()))
        shutil.copyfile(self.mount/'export.tar', self.mount/'seed.tar')
        self.state.unlink(); shutil.rmtree(self.base/'kernel-ccache')
        (self.source/'out.o').unlink()
        self.data = cache.prepare(self.source, self.mount, 'amd64', state=self.state)
        # Keep source namespace stable as Kbuild's mrproper does; out.o is not an input.
        # The small fixture removes its output before namespace creation below.
        self.assertEqual(self.data['status'], 'RESTORED')
        warm, _ = self.compile()
        self.assertEqual(body, warm)
        self.assertGreater(self.stats()['preprocessed_cache_hit'], 0)
        keys = []; linked = []; signatures = []
        (self.base/'plain.o').write_bytes(body)
        for generation in [1, 2]:
            key=self.base/f'key{generation}.pem'; cert=self.base/f'cert{generation}.der'
            subprocess.run(['openssl','req','-new','-x509','-newkey','rsa:2048','-nodes',
                            '-keyout',str(key),'-outform','DER','-out',str(cert),'-days','1',
                            '-subj','/CN=Fixture/'],check=True,capture_output=True)
            keys.append(cert)
            cert_source=self.source/'certs/certificate.c'
            cert_source.write_text('const char certificate[]="'+hashlib.sha256(cert.read_bytes()).hexdigest()+'";\n')
            _, command = self.compile('certs/certificate.c'); self.assertEqual(command[0], '/usr/bin/gcc')
            # Actual final link always runs; it is outside the compiler wrapper.
            subprocess.run(['ld','-r','-o',str(self.base/'linked.o'),str(self.base/'plain.o'),str(self.source/'out.o')],check=True)
            linked.append((self.base/'linked.o').read_bytes())
            (self.base/'module').write_bytes(body)
            subprocess.run(['openssl','cms','-sign','-binary','-in',str(self.base/'module'),'-signer',str(cert),
                            '-inkey',str(key),'-outform','DER','-out',str(self.base/'signature')],
                           check=True,capture_output=True)
            sig=(self.base/'signature').read_bytes()
            header=bytes([0,0,2,0,0,0,0,0])+len(sig).to_bytes(4,'big')
            signed=body+sig+header+MAGIC
            verify_module(signed, cert)
            signatures.append(signed)
            if generation == 2:
                self.assertNotEqual(linked[0], linked[1])
                with self.assertRaises(ValueError): verify_module(signed, keys[0])
                with self.assertRaises(ValueError): verify_module(signatures[0], cert)

    def test_invalid_seed_falls_back_to_fresh_compile(self):
        self.state.unlink(); shutil.rmtree(self.base/'kernel-ccache')
        (self.mount/'seed.tar').write_bytes(b'corrupt')
        self.data = cache.prepare(self.source, self.mount, 'amd64', state=self.state)
        self.assertEqual(self.data['status'], 'MISS_INVALID')
        self.compile(); self.assertGreater(self.stats()['cache_miss'], 0)


class ProducerTests(unittest.TestCase):
    def fixture(self, arch='amd64'):
        repo=dict(full_name=host.REPO,id=host.REPO_ID)
        run=dict(id=10,run_attempt=1,event='pull_request_target',path='.github/workflows/release.yml',
                 status='completed',conclusion='success',repository=repo,head_repository=repo,
                 head_sha='b'*40,head_branch='feature/#2',created_at='2026-01-01T01:00:00Z',
                 referenced_workflows=[dict(path=host.REPO+'/.github/workflows/staged-candidate.yml@'+'a'*40,
                                           sha='a'*40,ref='refs/heads/main')])
        receipt=dict(schema_version=1,source_commit='a'*40,source_pr=2,run_id=10,run_attempt=1,
                     architecture=arch,export_sha256='c'*64,export_bytes=100)
        artifact=dict(workflow_run=dict(id=10,repository_id=host.REPO_ID,head_sha='b'*40),expired=False,
                      expires_at='2099-01-01T00:00:00Z',digest='sha256:'+'c'*64,created_at='2026-01-01T02:00:00Z')
        runner='ubuntu-24.04'+('-arm' if arch=='arm64' else '')
        job=dict(name=f'build / kernel ({arch}, {runner})',run_id=10,run_attempt=1,head_sha='b'*40,
                 status='completed',conclusion='success',started_at='2026-01-01T01:30:00Z',completed_at='2026-01-01T02:30:00Z')
        pr=dict(state='closed',merged=True,merge_commit_sha='a'*40,base=dict(ref='main',repo=repo),
                head=dict(sha='b'*40,ref='feature/#2'),merged_at='2026-01-01T00:00:00Z')
        class API:
            def json(self, path): return pr if path.startswith('pulls/') else dict(total_count=1,jobs=[job])
        return API(), run, artifact, receipt, pr, job

    def test_digest_alone_cannot_admit_untrusted_foreign_or_failed_producer(self):
        for arch in ['amd64','arm64']:
            api,run,artifact,receipt,pr,job=self.fixture(arch)
            host.verify_producer(api,run,artifact,receipt,arch)
            for obj,key,value in [(run,'event','pull_request'),(run,'path','.github/workflows/test.yml'),
                                  (run,'conclusion','failure'),(run,'repository',dict(id=1,full_name='foreign/repo')),
                                  (run,'referenced_workflows',[]),(receipt,'architecture','foreign'),
                                  (pr,'merged',False),(pr,'merge_commit_sha','f'*40),
                                  (job,'conclusion','failure'),(artifact,'digest','not-a-digest'),
                                  (artifact,'expired',True)]:
                old=obj[key];obj[key]=value
                with self.subTest(key=key,value=value),self.assertRaises(ValueError):
                    host.verify_producer(api,run,artifact,receipt,arch)
                obj[key]=old

    def test_actual_import_checks_zip_digest_and_provenance_before_seed(self):
        for arch in ['amd64','arm64']:
            for invalid in ['none','digest','untrusted','zip-link','export-digest']:
                api,run,artifact,receipt,pr,job=self.fixture(arch)
                with tempfile.TemporaryDirectory() as temp:
                    root=Path(temp); directory=root/'admitted'; directory.mkdir()
                    payload=b'small compiler tar fixture'
                    receipt['export_sha256']=hashlib.sha256(payload).hexdigest()
                    receipt['export_bytes']=len(payload)
                    if invalid=='export-digest': receipt['export_sha256']='f'*64
                    buffer=io.BytesIO()
                    with zipfile.ZipFile(buffer,'w') as z:
                        info=zipfile.ZipInfo('export.tar')
                        if invalid=='zip-link': info.external_attr=(0o120777 << 16)
                        z.writestr(info,payload);z.writestr('provenance.json',json.dumps(receipt))
                    content=buffer.getvalue(); artifact.update(id=4,name=f'shirube-kernel-compiler-v1-{arch}-1',
                                                              size_in_bytes=len(content),digest='sha256:'+hashlib.sha256(content).hexdigest())
                    if invalid=='digest': artifact['digest']='sha256:'+'f'*64
                    if invalid=='untrusted': run['event']='pull_request'
                    class Remote:
                        def json(self, path):
                            if path.startswith('actions/workflows/'):return {'workflow_runs':[run]}
                            if path.endswith('/artifacts?per_page=100'):return {'total_count':1,'artifacts':[artifact]}
                            if path.startswith('actions/artifacts/'):return artifact
                            if path.startswith('pulls/'):return pr
                            if '/jobs?' in path:return {'total_count':1,'jobs':[job]}
                            return run
                        def download(self, identity, target, size):
                            self_identity=(identity,size)
                            if self_identity != (4,len(content)):raise AssertionError('download target differs')
                            target.write_bytes(content)
                    if invalid=='none':
                        self.assertEqual(host.import_cache(Remote(),directory,arch),'RESTORED_TRUSTED_ARTIFACT')
                        self.assertEqual((directory/'seed.tar').read_bytes(),payload)
                    else:
                        with self.subTest(arch=arch,invalid=invalid),self.assertRaises(ValueError):
                            host.import_cache(Remote(),directory,arch)
                        self.assertFalse((directory/'seed.tar').exists())

    def test_current_writer_requires_exact_merged_main_and_existing_admission(self):
        _,_,_,_,pr,_=self.fixture();pr['number']=2
        event=dict(repository=pr['base']['repo'],action='closed',pull_request=pr)
        env=dict(CACHE_WRITE='true',GITHUB_EVENT_NAME='pull_request_target',GITHUB_REPOSITORY=host.REPO,GITHUB_REF='refs/heads/main')
        host.current_context(env,event,'a'*40)
        for key,value in [('CACHE_WRITE','false'),('GITHUB_EVENT_NAME','pull_request'),('GITHUB_REF','refs/heads/feature')]:
            changed=dict(env,**{key:value})
            with self.assertRaises(ValueError):host.current_context(changed,event,'a'*40)


if __name__ == '__main__':
    unittest.main()
