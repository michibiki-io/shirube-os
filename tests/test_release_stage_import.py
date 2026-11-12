"""Small offline fixtures only; no historical tar or compiler execution."""
import copy
from datetime import datetime, timezone
import importlib.util
import io
import json
from pathlib import Path
import shutil
import stat
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import zipfile

from component_fixture import component_project
from producer.component_inputs import CACHEABLE, outer_key
from producer import staged
from producer.stage_artifacts import pack
from producer.sources import sha256
from test_workflow_cache_ci import workflow, expression_value, mappings

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('release_stage_import', ROOT/'scripts/import_release_stage.py')
importer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(importer)
NOW = datetime(2026, 11, 12, 10, tzinfo=timezone.utc)


class ImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = self.root/'src'; self.project.mkdir()
        component_project(self.project, container=True)
        self.manifest = importer.load_manifest(ROOT/'scripts/release-stage-import.json')
        self.entry = next(e for e in self.manifest['entries'] if (e['stage'],e['architecture']) == ('native','amd64'))
        self.compiler = self.entry['compiler_fingerprint']
        self.scope = staged.build_scope(self.project,'native','amd64',self.compiler,{})
        self.entry['input_id'] = self.scope['input_id']
        self.payload = self.root/'payload'; self.payload.mkdir()
        (self.payload/'fixture.deb').write_text('small synthetic package')
        (self.payload/'preferred-source.c').write_text('small synthetic source')
        self.tar = self.root/'native.tar'
        self.meta = pack(self.payload,self.tar,self.scope,importer.COMMIT,self.manifest['version'])
        self.metadata = self.root/'native.json'; self.metadata.write_text(json.dumps(self.meta))
        self.zip = self.root/'fixture.zip'
        self.make_zip()
        self.entry['transfer'] = {k:self.meta[k] for k in ('bytes','sha256')}
        self.env = {'GH_TOKEN':'fixture-only','CACHE_WRITE':'true','CACHE_MODE':'warm','CACHE_HIT':'',
            'GITHUB_REPOSITORY':importer.REPOSITORY, 'GITHUB_EVENT_NAME':'pull_request_target',
            'GITHUB_REF':'refs/heads/main'}
        repo = {'id':importer.REPOSITORY_ID,'full_name':importer.REPOSITORY}
        self.commit = 'c'*40
        self.event = {'repository':repo,'action':'closed','pull_request':{'state':'closed','merged':True,
            'merge_commit_sha':self.commit,'base':{'ref':'main','repo':repo}}}
        self.cache = self.root/'cache'
        self.run = {'id':importer.RUN,'workflow_id':self.manifest['workflow_id'],
            'path':self.manifest['workflow_path'],'event':'pull_request_target','run_attempt':1,
            'head_sha':importer.HEAD,'head_branch':self.manifest['head_branch'],
            'repository':repo,'head_repository':repo,'referenced_workflows':[self.manifest['referenced_workflow']],
            'pull_requests':[], 'conclusion':'failure','created_at':'2026-11-12T05:50:46Z'}
        self.pr = {'number':21,'state':'closed','merged':True,'closed_at':'2026-11-12T05:50:42Z',
            'merged_at':'2026-11-12T05:50:42Z','merge_commit_sha':importer.COMMIT,
            'head':{'sha':importer.HEAD,'ref':'feature/#20','repo':repo},'base':{'ref':'main','repo':repo}}
        self.job = {**self.entry['job'],'status':'completed'}
        self.artifact = {**self.entry['artifact'],'expired':False,'workflow_run':{
            'id':importer.RUN,'repository_id':importer.REPOSITORY_ID,'head_repository_id':importer.REPOSITORY_ID,
            'head_sha':importer.HEAD,'head_branch':'feature/#20'}}
        self.api = Mock()
        self.api.json.side_effect = lambda path: {'actions/runs/'+str(importer.RUN):self.run,
            'pulls/21':self.pr,'actions/jobs/'+str(self.entry['job']['id']):self.job,
            'actions/artifacts/'+str(self.entry['artifact']['id']):self.artifact}[path]
        self.api.download.side_effect = lambda ident,dest,size: shutil.copyfile(self.zip,dest)
        self.factory = Mock(return_value=self.api)

    def make_zip(self, records=None):
        with zipfile.ZipFile(self.zip,'w') as out:
            if records is None:
                out.write(self.tar,'native.tar'); out.write(self.metadata,'native.json')
            else:
                for name, data in records: out.writestr(name,data)
        self.entry['artifact']['digest'] = 'sha256:'+sha256(self.zip)
        self.entry['artifact']['size_in_bytes'] = self.zip.stat().st_size

    def invoke(self, finished_at=NOW, **overrides):
        args = dict(project=self.project, cache=self.cache, stage='native', architecture='amd64',
            key=outer_key(self.scope),commit=self.commit,env=self.env,event=self.event,
            manifest=self.manifest,api_factory=self.factory,now=NOW)
        args.update(overrides)
        # The migration cutoff must not make these offline fixtures expire.
        with patch.object(importer, 'datetime', wraps=datetime) as clock:
            clock.now.return_value = finished_at
            return importer.import_stage(**args)

    def assert_clean(self):
        self.assertFalse((self.cache/self.scope['input_id']).exists())
        if self.cache.exists(): self.assertEqual(list(self.cache.iterdir()),[])

    def test_real_staged_hit_repack_and_immutable_original_pair(self):
        self.assertEqual(self.invoke(),'IMPORTED')
        entry=self.cache/self.scope['input_id']
        self.assertEqual((entry/'transfer.tar').read_bytes(),self.tar.read_bytes())
        self.assertEqual((entry/'transfer.json').read_bytes(),self.metadata.read_bytes())
        original_path=Path
        def mapped(value='.',*parts):
            value=str(value)
            for prefix,name in [('/src','src'),('/build-private','work'),('/stage-dependencies','dependencies')]:
                if value==prefix or value.startswith(prefix+'/'):
                    return original_path(self.root/name/value[len(prefix):].lstrip('/'),*parts)
            return original_path(value,*parts)
        output=self.root/'output'; output.mkdir()
        compile_mock=Mock(side_effect=AssertionError('Compilation must not execute'))
        with patch.object(staged,'Path',side_effect=mapped), patch('producer.architecture.native'), \
             patch('producer.library_cache.compiler_fingerprint',return_value=self.compiler), \
             patch.object(staged,'restore_inputs',return_value=({},{})), \
             patch.dict(sys.modules,{'build':SimpleNamespace(main=compile_mock)}), \
             patch.object(sys,'argv',['staged','native','amd64','--commit',self.commit,'--version','v2026.03.9',
                                      '--output',str(output),'--cache',str(self.cache)]), \
             patch.object(sys,'path',list(sys.path)):
            staged.main()
        compile_mock.assert_not_called()
        result=json.loads((output/'transfer.json').read_text())
        self.assertEqual(json.loads((output/'resources.json').read_text())['cache'],'HIT')
        self.assertEqual(result['source_commit'],self.commit)
        self.assertEqual(result['version'],'v2026.03.9')
        self.assertEqual(result['content_source_commit'],importer.COMMIT)
        self.assertEqual(result['payload_id'],self.meta['payload_id'])
        self.assertEqual((entry/'transfer.tar').read_bytes(),self.tar.read_bytes())
        self.assertEqual((entry/'transfer.json').read_bytes(),self.metadata.read_bytes())

    def test_cutoff_crossed_during_download_rejects_and_cleans(self):
        with self.assertRaises(ValueError):
            self.invoke(finished_at=importer.timestamp(importer.CUTOFF))
        self.api.download.assert_called_once()
        self.assert_clean()

    def test_negative_current_contexts_and_scope_never_fetch(self):
        for key,values in {'CACHE_WRITE':['false',''],'CACHE_MODE':['cold','bad'], 'CACHE_HIT':['true'],
            'GITHUB_EVENT_NAME':['workflow_dispatch','pull_request','push'],
            'GITHUB_REPOSITORY':['fork/shirube-os'],'GITHUB_REF':['refs/heads/feature/x']}.items():
            for value in values:
                with self.subTest(key=key,value=value), self.assertRaises(ValueError):
                    self.invoke(env={**self.env,key:value})
        for path,value in [(('action',),'opened'),(('repository','id'),1),
                (('pull_request','merged'),False),(('pull_request','state'),'open'),
                (('pull_request','base','ref'),'feature'),(('pull_request','base','repo','id'),2),
                (('pull_request','merge_commit_sha'),'e'*40)]:
            event=copy.deepcopy(self.event); parent=event
            for key in path[:-1]: parent=parent[key]
            parent[path[-1]]=value
            with self.subTest(path=path),self.assertRaises(ValueError): self.invoke(event=event)
        for args in ({'now':importer.timestamp(importer.CUTOFF)}, {'key':'wrong'}, {'commit':'invalid'}):
            with self.assertRaises(ValueError): self.invoke(**args)
        self.entry['compiler_fingerprint']='sha256:'+'e'*64
        with self.assertRaises(ValueError): self.invoke()
        self.factory.assert_not_called()
        self.assertFalse(self.cache.exists())

    def test_every_pinned_remote_field_mismatch_fails_without_download(self):
        # Mutate every field the verifier compares, plus repository/PR/ownership relations.
        targets=[(self.run,(k,)) for k in ('id','workflow_id','path','event','run_attempt','head_sha','head_branch','referenced_workflows')]
        targets += [(self.run,(side,k)) for side in ('repository','head_repository') for k in ('id','full_name')]
        targets += [(self.pr,(k,)) for k in ('number','state','merged','merge_commit_sha','closed_at','merged_at')]
        targets += [(self.pr,('head',k)) for k in ('sha','ref')]+[(self.pr,('base','ref'))]
        targets += [(self.pr,(side,'repo',k)) for side in ('head','base') for k in ('id','full_name')]
        targets += [(self.job,(k,)) for k in (*self.entry['job'],'status')]
        targets += [(self.artifact,(k,)) for k in (*self.entry['artifact'],'expired')]
        targets += [(self.artifact,('workflow_run',k)) for k in self.artifact['workflow_run']]
        for target,path in targets:
            parent=target
            for key in path[:-1]: parent=parent[key]
            original=parent[path[-1]]; parent[path[-1]]=None
            try:
                with self.subTest(path=path),self.assertRaises((ValueError,TypeError,KeyError)):
                    self.invoke()
                self.api.download.assert_not_called(); self.assert_clean()
            finally: parent[path[-1]]=original

    def test_api_failures_and_download_failures_cleanup(self):
        import urllib.error
        for error in (TimeoutError(),urllib.error.HTTPError('redacted',403,'',{},None),
                      urllib.error.HTTPError('redacted',404,'',{},None),OSError()):
            with self.subTest(error=type(error).__name__):
                with patch.object(self.api,'json',side_effect=error),self.assertRaises(Exception): self.invoke()
                self.api.download.assert_not_called(); self.assert_clean()
        def partial(ident,dest,size):
            dest.write_bytes(b'partial'); raise TimeoutError()
        self.api.download.side_effect=partial
        with self.assertRaises(TimeoutError): self.invoke()
        self.assert_clean()

    def test_disk_destination_and_symlink_guards(self):
        with patch.object(importer.shutil,'disk_usage',return_value=SimpleNamespace(free=0)),self.assertRaises(ValueError):
            self.invoke()
        self.factory.assert_not_called()
        destination=self.cache/self.scope['input_id']; destination.mkdir()
        with self.assertRaises(ValueError): self.invoke()
        destination.rmdir(); destination.symlink_to(self.payload,target_is_directory=True)
        with self.assertRaises(ValueError): self.invoke()
        self.factory.assert_not_called(); destination.unlink()
        self.cache.rmdir(); self.cache.symlink_to(self.payload,target_is_directory=True)
        with self.assertRaises(ValueError): self.invoke()
        self.factory.assert_not_called()

    def test_atomic_install_never_clobbers_existing_empty_directory(self):
        source=self.root/'new'; source.mkdir(); dest=self.root/'existing'; dest.mkdir()
        with self.assertRaises(OSError): importer.install_fresh(source,dest)
        self.assertTrue(source.is_dir()); self.assertTrue(dest.is_dir())

    def test_bad_zip_layout_and_member_limits_cleanup(self):
        cases=[ [('native.tar',b'x')], [('native.tar',b'x'),('native.tar',b'x')],
                [('../native.tar',b'x'),('native.json',b'{}')],
                [('native.tar',b'x'),('native.json',b'{}'),('extra',b'x')],
                [('native.tar/',b'x'),('native.json',b'{}')]]
        link=zipfile.ZipInfo('native.tar');link.create_system=3;link.external_attr=(stat.S_IFLNK|0o777)<<16
        cases.append([(link,b'target'),('native.json',b'{}')])
        special=zipfile.ZipInfo('native.tar');special.create_system=3;special.external_attr=(stat.S_IFIFO|0o600)<<16
        cases.append([(special,b'x'),('native.json',b'{}')])
        for records in cases:
            with self.subTest(records=str(records)[:80]):
                self.make_zip(records); self.artifact.update(self.entry['artifact'])
                with self.assertRaises(ValueError): self.invoke()
                self.assert_clean()
        self.make_zip(); self.artifact.update(self.entry['artifact'])
        with patch.object(importer,'METADATA_LIMIT',1),self.assertRaises(ValueError): self.invoke()
        self.assert_clean()
        with zipfile.ZipFile(self.zip,'w',compression=zipfile.ZIP_DEFLATED) as out:
            out.write(self.tar,'native.tar');out.write(self.metadata,'native.json')
        self.entry['artifact'].update(digest='sha256:'+sha256(self.zip),size_in_bytes=self.zip.stat().st_size)
        self.artifact.update(self.entry['artifact'])
        with self.assertRaises(ValueError):self.invoke()
        self.assert_clean()

    def test_corrupt_zip_tar_receipt_metadata_and_inventory_cleanup(self):
        self.zip.write_bytes(b'corrupt')
        with self.assertRaises(ValueError):self.invoke()
        self.assert_clean()
        original=self.tar.read_bytes()
        for field,value in [('source_commit',importer.HEAD),('version','v2026.03.9'),
                ('content_source_commit',self.commit),('scope',{}),('bytes',1),('sha256','0'*64),('files',[])]:
            meta={**self.meta,field:value};self.metadata.write_text(json.dumps(meta))
            self.make_zip();self.artifact.update(self.entry['artifact'])
            with self.subTest(field=field),self.assertRaises(ValueError):self.invoke()
            self.assert_clean()
        self.metadata.write_text(json.dumps(self.meta))
        # Matching outer tar hash still cannot authorize corrupted payload/inventory.
        data=bytearray(original); index=data.index(b'small synthetic package');data[index]^=1
        self.tar.write_bytes(data)
        self.entry['transfer']['sha256']=sha256(self.tar)
        meta={**self.meta,'sha256':sha256(self.tar)};self.metadata.write_text(json.dumps(meta))
        self.make_zip();self.artifact.update(self.entry['artifact'])
        with self.assertRaises(ValueError):self.invoke()
        self.assert_clean()

    def test_malformed_tar_members_receipts_and_secrets_cleanup(self):
        import tarfile
        from producer.stage_artifacts import digest
        with tarfile.open(self.tar) as source:
            original=[(m,source.extractfile(m).read()) for m in source]
        def changed_receipt(field,value):
            entries=copy.deepcopy(original)
            receipt=json.loads(entries[0][1]);receipt[field]=value
            data=json.dumps(receipt).encode();entries[0][0].size=len(data);entries[0]=(entries[0][0],data)
            return entries
        cases=[original[:-1], original+original[-1:]]
        for name,kind in [('../escape',tarfile.REGTYPE),('payload/link',tarfile.SYMTYPE),
                          ('payload/hardlink',tarfile.LNKTYPE),('payload/fifo',tarfile.FIFOTYPE)]:
            header=tarfile.TarInfo(name);header.type=kind;header.size=0
            cases.append(original+[(header,b'')])
        cases.extend(changed_receipt(field,value) for field,value in [
            ('source_commit',importer.HEAD),('scope',{}),('payload_id','0'*64),('files',[])])
        for entries in cases:
            with tarfile.open(self.tar,'w') as out:
                for header,data in entries:out.addfile(header,io.BytesIO(data) if header.isfile() else None)
            self.entry['transfer']={'sha256':sha256(self.tar),'bytes':self.tar.stat().st_size}
            self.make_zip();self.artifact.update(self.entry['artifact'])
            with self.assertRaises((ValueError,tarfile.TarError)):self.invoke()
            self.assert_clean()
        # Even a self-consistent receipt/inventory must pass the secret scanner.
        secret=b'-----BEGIN RSA PRIVATE KEY-----\n'+b'A'*64+b'\n-----END RSA PRIVATE KEY-----\n'
        import hashlib
        files=[{'name':'renamed.txt','sha256':hashlib.sha256(secret).hexdigest(),'size':len(secret),'executable':False}]
        receipt={k:v for k,v in self.meta.items() if k not in ('bytes','sha256')}
        receipt.update(files=files,payload_id=digest(files))
        with tarfile.open(self.tar,'w') as out:
            for name,data in [('receipt.json',json.dumps(receipt).encode()),('payload/renamed.txt',secret)]:
                header=tarfile.TarInfo(name);header.size=len(data);out.addfile(header,io.BytesIO(data))
        self.entry['transfer']={'sha256':sha256(self.tar),'bytes':self.tar.stat().st_size}
        self.metadata.write_text(json.dumps({**receipt,**self.entry['transfer']}))
        self.make_zip();self.artifact.update(self.entry['artifact'])
        with self.assertRaises(ValueError):self.invoke()
        self.assert_clean()

    def test_encrypted_zip_and_tar_size_limit_reject(self):
        data=bytearray(self.zip.read_bytes())
        # Set encryption in both local headers and central directory records.
        for signature,offset in ((b'PK\x03\x04',6),(b'PK\x01\x02',8)):
            start=0
            while True:
                index=data.find(signature,start)
                if index<0:break
                data[index+offset]|=1;start=index+4
        self.zip.write_bytes(data)
        self.entry['artifact'].update(digest='sha256:'+sha256(self.zip))
        self.artifact.update(self.entry['artifact'])
        with self.assertRaises(ValueError):self.invoke()
        self.assert_clean()
        self.make_zip();self.artifact.update(self.entry['artifact'])
        self.entry['transfer']['bytes']=1
        with self.assertRaises(ValueError):self.invoke()
        self.assert_clean()

    def test_schema_is_fixed_and_has_ten_unique_unsigned_entries(self):
        source=ROOT/'scripts/release-stage-import.json'
        m=importer.load_manifest(source)
        self.assertEqual(len(m['entries']),10)
        for field in ('repository_id','run_attempt','head_sha','source_commit','expires_at','referenced_workflow'):
            d=copy.deepcopy(m);d[field]=None
            path=self.root/'manifest.json';path.write_text(json.dumps(d))
            with self.subTest(field=field),self.assertRaises((ValueError,TypeError)):
                importer.load_manifest(path)


class TransportTests(unittest.TestCase):
    def response(self, data, length=None):
        response=io.BytesIO(data)
        response.headers={} if length is None else {'Content-Length':str(length)}
        return response

    def test_redirect_strips_credentials_and_forbids_insecure_redirect(self):
        import urllib.error
        with tempfile.TemporaryDirectory() as temp:
            api=importer.API('secret-fixture-token')
            error=urllib.error.HTTPError('api',302,'',{'Location':'https://storage.example/signed'},None)
            with patch.object(api.opener,'open',side_effect=[error,self.response(b'abc',3)]) as opened:
                api.download(1,Path(temp)/'zip',3)
            first,second=[call.args[0] for call in opened.call_args_list]
            self.assertIn('Authorization',first.headers)
            self.assertNotIn('Authorization',second.headers)
            for location in ('http://storage.example/a','https://user:pass@storage.example/a','file:///tmp/x'):
                error=urllib.error.HTTPError('api',302,'',{'Location':location},None)
                with patch.object(api.opener,'open',side_effect=error) as opened,self.assertRaises(ValueError):
                    api.download(1,Path(temp)/'other',3)
                self.assertEqual(opened.call_count,1)
        self.assertIsNone(importer.NoRedirect().redirect_request(None,None,302,'',{},'https://example.org'))

    def test_download_size_metadata_and_deadline_bounds(self):
        api=importer.API('fixture-token')
        for body,length in [(b'ab',2),(b'abcd',4),(b'ab',None),(b'abcd',None)]:
            with self.subTest(body=body,length=length),tempfile.TemporaryDirectory() as temp:
                with patch.object(api.opener,'open',return_value=self.response(body,length)),self.assertRaises(ValueError):
                    api.download(1,Path(temp)/'zip',3)
        with patch.object(importer,'API_LIMIT',2),patch.object(api.opener,'open',return_value=self.response(b'{}x')):
            with self.assertRaises(ValueError):api.json('small')
        with patch.object(importer.time,'monotonic',return_value=api.deadline+1):
            with self.assertRaises(ValueError):api.timeout()

    def test_cli_timeout_and_failures_emit_safe_warning_without_exception_data(self):
        import contextlib
        with tempfile.TemporaryDirectory() as temp:
            event=Path(temp)/'event'; event.write_text('{}')
            argv=['importer','--cache',temp,'--stage','native','--architecture','amd64','--key','key','--commit','c'*40]
            for error in (TimeoutError('secret-token'),ValueError('signed-url-secret')):
                output=io.StringIO()
                with patch.object(sys,'argv',argv),patch.dict('os.environ',{'GITHUB_EVENT_PATH':str(event)}), \
                     patch.object(importer,'import_stage',side_effect=error), \
                     patch.object(importer.signal,'signal') as handler,patch.object(importer.signal,'alarm') as alarm, \
                     contextlib.redirect_stdout(output):
                    importer.main()
                    timeout_handler=handler.call_args_list[0].args[1]
                    with self.assertRaises(TimeoutError):timeout_handler(None,None)
                self.assertIn('SKIPPED',output.getvalue());self.assertNotIn('secret',output.getvalue())
                self.assertEqual(alarm.call_args_list[0].args,(600,))
                self.assertEqual(alarm.call_args_list[-1].args,(0,))


class WorkflowImportTests(unittest.TestCase):
    def test_effective_grants_and_only_exact_host_steps_receive_token(self):
        staged_workflow=workflow('staged-candidate.yml');release=workflow('release.yml');validation=workflow('validate-staged.yml')
        self.assertNotIn('permissions',staged_workflow)
        self.assertEqual(release['jobs']['build']['permissions'],{'contents':'read','actions':'read'})
        for caller in (release['jobs']['build'],validation['jobs']['validate'],validation['jobs']['validate-main']):
            for name,job in staged_workflow['jobs'].items():
                effective=job.get('permissions',caller['permissions'])
                self.assertEqual(effective,caller['permissions'] if name in CACHEABLE else {'contents':'read'})
        for name,job in staged_workflow['jobs'].items():
            steps=[s for s in job['steps'] if 'GH_TOKEN' in s.get('env',{})]
            self.assertEqual(len(steps),1 if name in CACHEABLE else 0)
            if not steps: continue
            step=steps[0]
            self.assertEqual(step['env']['GH_TOKEN'],'${{ github.token }}')
            self.assertIn('scripts/import_release_stage.py',step['run'])
            self.assertNotIn('docker',step['run'])
            self.assertEqual(step['env']['STAGE_KEY'],'${{ steps.scope.outputs.key }}')
            restore=next(s for s in job['steps'] if s.get('id')=='cache')
            execute=next(s for s in job['steps'] if 'scripts/run-native-stage.py' in s.get('run',''))
            self.assertLess(job['steps'].index(restore),job['steps'].index(step))
            self.assertLess(job['steps'].index(step),job['steps'].index(execute))
            for admitted in ('true','false'):
                for event in ('pull_request_target','pull_request','workflow_dispatch'):
                    for mode in ('cold','warm'):
                        for hit in ('true','false',''):
                            context={'needs.admission.outputs.cache-write':admitted,'github.event_name':event,
                                'inputs.cache-mode':mode,'steps.cache.outputs.cache-hit':hit}
                            self.assertEqual(expression_value(step['if'],context),admitted=='true' and
                                event=='pull_request_target' and mode=='warm' and hit!='true')
            for other in job['steps']:
                if other is not step:
                    self.assertFalse(any('GH_TOKEN' in node for node in mappings(other)))


if __name__=='__main__': unittest.main()
