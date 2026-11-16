"""Executable automatic seed contracts; bounded tiny ZIPs, never kernel builds."""
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from producer import kernel_seed as seed
from producer import release_plan
from scripts import kernel_compile_cache as host
from scripts import kernel_seed_plan as planner
from tests import test_kernel_compile_cache as kernel_tests
from tests.test_workflow_cache_ci import workflow, expression_value
from tests.component_fixture import component_project


def measurements(arch):
    return dict(scope=dict(schema_version=1, architecture=arch, toolchain='d'*64),
                namespace='e'*64, export=dict(entries=3, skipped_capacity=0))


class IndexedRemote:
    def __init__(self, kind='automatic-seed', arch='amd64', key='f'*64):
        _, self.run, self.export, self.receipt, self.pr, self.job = kernel_tests.ProducerTests().fixture(arch)
        self.arch, self.key, self.kind = arch, key, kind
        filename, event, prefix = host.PRODUCER_KINDS[kind]
        self.run.update(path='.github/workflows/'+filename, event=event)
        self.job['name'] = f"{prefix} / kernel ({arch}, ubuntu-24.04{'-arm' if arch == 'arm64' else ''})"
        self.receipt.update(schema_version=3, producer_kind=kind, workflow_sha='a'*40,
                            source_ref='refs/heads/main', coverage_key=key, measured=measurements(arch))
        if event == 'workflow_dispatch':
            self.receipt.pop('source_pr')
            self.run.update(head_sha='a'*40, head_branch='main')
            self.job['head_sha'] = 'a'*40
            self.export['workflow_run']['head_sha'] = 'a'*40
        self.export.update(id=4, name=host.export_name(arch, 1))
        self.index = copy.deepcopy(self.export)
        self.index.update(id=5, name=host.index_name(arch, key), created_at='2026-01-01T02:01:00Z')
        self.payload = b'small unsigned compiler fixture'
        self.receipt.update(export_bytes=len(self.payload), export_sha256=hashlib.sha256(self.payload).hexdigest())
        self.record = dict(schema_version=1, receipt=self.receipt, export_artifact={})
        self.count = 1
        self.entries = [self.index]
        self.calls, self.downloads = [], []
        self.refresh()

    def refresh(self):
        def zipped(files):
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, 'w') as archive:
                for name, value in files.items(): archive.writestr(name, value)
            return buffer.getvalue()
        self.export_zip = zipped({'export.tar':self.payload, 'provenance.json':json.dumps(self.receipt)})
        self.export.update(size_in_bytes=len(self.export_zip), digest='sha256:'+hashlib.sha256(self.export_zip).hexdigest())
        self.record['export_artifact'] = host.artifact_binding(self.export)
        self.index_zip = zipped({'index.json':json.dumps(self.record)})
        self.index.update(size_in_bytes=len(self.index_zip), digest='sha256:'+hashlib.sha256(self.index_zip).hexdigest())

    def json(self, path):
        self.calls.append(path)
        if path.startswith('actions/artifacts?name='):
            return dict(total_count=self.count, artifacts=self.entries)
        if path == 'actions/artifacts/4': return self.export
        if path == 'actions/artifacts/5': return self.index
        if path == 'actions/runs/10': return self.run
        if path == 'pulls/2': return self.pr
        if path == 'actions/runs/10/attempts/1/jobs?per_page=100':
            return dict(total_count=1, jobs=[self.job])
        raise AssertionError('Unexpected API call: '+path)

    def download(self, identity, target, size):
        self.downloads.append(identity)
        payload = self.index_zip if identity == 5 else self.export_zip
        assert len(payload) == size
        target.write_bytes(payload)


class IndexTests(unittest.TestCase):
    def test_all_kinds_arches_metadata_only_and_indexed_reader_before_legacy(self):
        for kind in host.PRODUCER_KINDS:
            for arch in seed.ARCHITECTURES:
                with self.subTest(kind=kind, arch=arch):
                    remote = IndexedRemote(kind, arch)
                    status, choice = host.find_index(remote, arch, remote.key)
                    self.assertEqual(status, 'covered')
                    self.assertEqual(remote.downloads, [5])
                    # No current-main query, fake PR for dispatch or large export probe.
                    self.assertFalse(any('main' in p for p in remote.calls))
                    self.assertEqual(any(p.startswith('pulls/') for p in remote.calls),
                                     host.PRODUCER_KINDS[kind][1] == 'pull_request_target')
                    with tempfile.TemporaryDirectory() as temp:
                        self.assertEqual(host.import_cache(remote, Path(temp), arch, remote.key), 'RESTORED_TRUSTED_ARTIFACT')
                        self.assertEqual((Path(temp)/'seed.tar').read_bytes(), remote.payload)
                    self.assertEqual(remote.downloads, [5, 5, 4])
                    self.assertFalse(any('actions/workflows/' in p for p in remote.calls))

    def test_independent_index_export_authority_and_every_modern_kind(self):
        mutations = [
            ('run','repository',{}), ('run','head_repository',{}), ('run','path','.github/workflows/test.yml'),
            ('run','event','pull_request'), ('run','run_attempt',2), ('run','referenced_workflows',[]),
            ('run','head_sha','9'*40), ('receipt','source_commit','9'*40), ('receipt','workflow_sha','9'*40),
            ('receipt','source_ref','refs/heads/feature'), ('receipt','producer_kind','forged'),
            ('receipt','run_id',11), ('receipt','run_attempt',2), ('receipt','architecture','foreign'),
            ('job','name','build / kernel (foreign)'), ('job','run_id',11), ('job','head_sha','9'*40),
            ('job','conclusion','failure'), ('job','status','in_progress'),
            ('index','workflow_run',dict(id=10, repository_id=1, head_sha='b'*40)),
            ('index','created_at','2026-01-01T03:00:00Z'), ('index','digest','sha256:'+'9'*64),
            ('export','workflow_run',dict(id=11, repository_id=host.REPO_ID, head_sha='b'*40)),
            ('export','created_at','2026-01-01T03:00:00Z'),
            ('export','name','signed-kernel-stage'),
        ]
        for kind in host.PRODUCER_KINDS:
            for target, field, value in mutations:
                remote = IndexedRemote(kind)
                getattr(remote, target)[field] = value
                # Integrity alone is insufficient: re-sign fixture bytes where
                # appropriate, while preserving deliberately broken metadata.
                if field != 'digest': remote.refresh()
                with self.subTest(kind=kind, target=target, field=field):
                    self.assertEqual(host.find_index(remote,'amd64',remote.key)[0], 'unknown')
                    self.assertNotIn(4, remote.downloads)

    def test_zero_entries_malformed_measurements_key_and_archive_are_unknown(self):
        for mutation in ('zero','scope','namespace','key','schema','extra','bad-zip','digest','large','export-binding'):
            remote = IndexedRemote()
            if mutation == 'zero': remote.receipt['measured']['export']['entries'] = 0
            if mutation == 'scope': remote.receipt['measured']['scope']['architecture'] = 'arm64'
            if mutation == 'namespace': remote.receipt['measured']['namespace'] = 'bad'
            if mutation == 'key': remote.receipt['coverage_key'] = '0'*64
            if mutation == 'schema': remote.record['schema_version'] = 2
            if mutation == 'extra': remote.record['private.pem'] = 'never a cache'
            remote.refresh()
            if mutation == 'bad-zip':
                remote.index_zip = b'broken'; remote.index.update(size_in_bytes=6, digest='sha256:'+hashlib.sha256(b'broken').hexdigest())
            if mutation == 'digest': remote.index['digest'] = 'sha256:'+'0'*64
            if mutation == 'large': remote.index['size_in_bytes'] = host.INDEX_LIMIT+1
            if mutation == 'export-binding': remote.export['digest'] = 'sha256:'+'0'*64
            with self.subTest(mutation=mutation):
                self.assertEqual(host.find_index(remote,'amd64',remote.key)[0], 'unknown')
                self.assertNotIn(4, remote.downloads)

    def test_no_indexes_expiry_failure_partial_inflight_and_search_bounds(self):
        remote = IndexedRemote()
        remote.entries=[]; remote.count=0
        self.assertEqual(host.find_index(remote,'amd64',remote.key)[0], 'missing')
        remote.count=11
        self.assertEqual(host.find_index(remote,'amd64',remote.key)[0], 'unknown')
        self.assertEqual(remote.downloads, [])
        for conclusion in ('failure','cancelled'):
            remote=IndexedRemote();remote.run['conclusion']=conclusion
            self.assertEqual(host.find_index(remote,'amd64',remote.key)[0], 'missing')
            self.assertEqual(remote.downloads, [])
        remote=IndexedRemote();remote.run.update(status='in_progress', conclusion=None)
        self.assertEqual(host.find_index(remote,'amd64',remote.key)[0], 'unknown')
        self.assertEqual(remote.downloads, [])
        for target in ('index','export'):
            remote=IndexedRemote();getattr(remote,target)['expired']=True
            self.assertEqual(host.find_index(remote,'amd64',remote.key)[0], 'missing')
            self.assertNotIn(4, remote.downloads)
        class Broken:
            def json(self, _): raise OSError('private response')
        self.assertEqual(host.find_index(Broken(),'amd64','f'*64), ('unknown',None))
        # Complete metadata with no indexes proves no new coverage, regardless
        # of historic legacy export availability; it never downloads one.
        remote=IndexedRemote();remote.entries=[];remote.count=0
        self.assertEqual(host.find_index(remote,'amd64',remote.key), ('missing',None))


class InputAndDecisionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.before=component_project(self.root/'before')
        self.after=component_project(self.root/'after')

    def test_source_only_stable_across_commits_and_orchestration_but_closure_mutates(self):
        with patch('producer.component_inputs.compiler_fingerprint', side_effect=AssertionError('host compiler')):
            original=seed.key(self.before,'amd64')
            self.assertEqual(original,seed.key(self.after,'amd64'))
            with patch.object(seed,'PROTOCOL',2):
                self.assertNotEqual(original,seed.key(self.after,'amd64'))
            self.assertNotEqual(original,seed.key(self.after,'arm64'))
            for name in ('README.md','docs/fixture.md','producer/geoip.py','scripts/kernel_seed_plan.py',
                         '.github/workflows/staged-candidate.yml'):
                path=self.after/name;path.parent.mkdir(parents=True,exist_ok=True)
                saved=path.read_bytes() if path.exists() else None
                path.write_bytes((saved or b'')+b'\n# orchestration or unrelated\n')
                self.assertEqual(original,seed.key(self.after,'amd64'),name)
                if saved is None:path.unlink()
                else:path.write_bytes(saved)
            for name in ('producer/compile_kernel.py','producer/compile_vpp.py','containers/native/Dockerfile',
                         'containers/native/install_gems.py','containers/native/kernel-cc','patches/kernel/new.patch'):
                path=self.after/name;saved=path.read_bytes() if path.exists() else None
                path.write_bytes((saved or b'')+b'\n# mutation\n')
                self.assertNotEqual(original,seed.key(self.after,'amd64'),name)
                if saved is None:path.unlink()
                else:path.write_bytes(saved)
            path=self.after/'containers/native/external-sources.lock.json';data=json.loads(path.read_text())
            data['vpp']['mutation']=True;path.write_text(json.dumps(data))
            self.assertNotEqual(original,seed.key(self.after,'amd64'))

    def test_relevance_before_after_rename_delete_new_dependencies_and_malformed_graph(self):
        self.assertTrue(seed.relevant(['.github/workflows/staged-candidate.yml'],self.before,self.after))
        self.assertFalse(seed.relevant(['README.md','tests/test_other.py','producer/geoip.py'],self.before,self.after))
        name='docs/compile-input.txt';path=self.before/name;path.parent.mkdir(parents=True);path.write_text('old')
        declaration=self.before/'containers/native/inputs-kernel.json';data=json.loads(declaration.read_text())
        data['roots'].append(name);data['graph'][name]=[];declaration.write_text(json.dumps(data))
        self.assertTrue(seed.relevant([name,'docs/new-name.txt'],self.before,self.after))
        self.assertTrue(seed.relevant(['patches/kernel/deleted.patch'],self.before,self.after))
        self.assertTrue(seed.relevant(['containers/native/install_gems.py'],self.before,self.after))
        self.assertTrue(seed.relevant(['producer/compile_vpp.py'],self.before,self.after))
        declaration=self.after/'containers/native/inputs-kernel.json';declaration.write_text('{broken')
        with self.assertRaises(ValueError):seed.relevant(['README.md'],self.before,self.after)

    def test_bootstrap_missing_covered_partial_unknown_cold_and_postqueue_recheck(self):
        class Remote:
            def json(self,_):return dict(total_count=0,artifacts=[])
        api=Remote()
        first=planner.plan(api,self.after,automatic=True,before=self.before,changed=['scripts/kernel_seed_plan.py'])
        self.assertEqual(first['selected'],list(seed.ARCHITECTURES))
        for coverage, selected, status in [
            ({'amd64':'covered','arm64':'covered'},[],'covered'),
            ({'amd64':'covered','arm64':'missing'},['arm64'],'seed-required'),
            ({'amd64':'unknown','arm64':'missing'},['arm64'],'pending-unknown'),
            ({'amd64':'unknown','arm64':'unknown'},[],'pending-unknown')]:
            with patch.object(planner,'find_index', side_effect=lambda _,a,k:(coverage[a],None)):
                result=planner.plan(api,self.after)
            self.assertEqual((result['selected'],result['status']),(selected,status))
        with patch.object(planner,'find_index',side_effect=AssertionError('cold fetch')):
            self.assertEqual(planner.plan(None,self.after,requested=['arm64'],cold=True)['selected'],['arm64'])
        with patch.object(planner,'find_index',side_effect=AssertionError('unnecessary probe')):
            self.assertEqual(planner.plan(api,self.after,full_planned=True)['status'],'existing-required-work')
            self.assertEqual(planner.plan(api,self.after,automatic=True,before=self.before,changed=['README.md'])['status'],'irrelevant')
        with patch.object(planner,'find_index',return_value=('covered',None)):
            # This invocation represents the later run AFTER obtaining its slot.
            self.assertEqual(planner.plan(api,self.after)['selected'],[])
        (self.after/'producer/compile_kernel.py').write_text((self.after/'producer/compile_kernel.py').read_text()+'\n# newer input\n')
        with patch.object(planner,'find_index',side_effect=AssertionError('superseded probe')):
            result=planner.plan(api,self.before,automatic=True,before=self.before,changed=['scripts/kernel_seed_plan.py'],current=self.after)
        self.assertEqual(result['status'],'pending-superseded');self.assertEqual(result['selected'],[])
        for invalid in ([],['amd64','amd64'],['unknown']):
            with self.assertRaises(ValueError):seed.matrix(invalid)


class AdmissionAndQueueTests(unittest.TestCase):
    def invoke(self,kind,arch='amd64,arm64',mode='warm',override=None):
        filename,event,prefix=host.PRODUCER_KINDS[kind]
        repo=dict(full_name=host.REPO,id=host.REPO_ID)
        data=dict(repository=repo,action='closed',pull_request=dict(number=40,state='closed',merged=True,
                  merge_commit_sha='a'*40,base=dict(ref='main',repo=repo)))
        env=dict(SOURCE_COMMIT='a'*40,CACHE_MODE=mode,PURPOSE='seed' if 'seed' in kind else 'release' if kind=='build' else 'validation',
                 PRODUCER_KIND=kind,ARCHITECTURES=arch,GITHUB_REPOSITORY=host.REPO,GITHUB_EVENT_NAME=event,
                 GITHUB_REF='refs/heads/main',GITHUB_SHA='a'*40,GITHUB_WORKFLOW_SHA='a'*40,
                 GITHUB_WORKFLOW_REF=host.REPO+'/.github/workflows/'+filename+'@refs/heads/main')
        env.update(override or {})
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'event').write_text(json.dumps(data))
            env.update(GITHUB_EVENT_PATH=str(root/'event'),GITHUB_OUTPUT=str(root/'output'))
            result=subprocess.run(['bash','-e','-c',workflow('staged-candidate.yml')['jobs']['admission']['steps'][0]['run']],env=env,capture_output=True)
            output=dict(line.split('=',1) for line in (root/'output').read_text().splitlines()) if (root/'output').exists() else {}
            return result.returncode, output, env, data

    def test_all_explicit_kinds_inline_and_host_authority_warm_cold_selected_and_full(self):
        for kind in host.PRODUCER_KINDS:
            for mode in ('cold','warm'):
                for architectures in ('amd64','arm64','amd64,arm64'):
                    status,out,env,data=self.invoke(kind,architectures,mode)
                    allowed='seed' in kind or architectures=='amd64,arm64'
                    with self.subTest(kind=kind,mode=mode,architectures=architectures):
                        self.assertEqual(status==0,allowed)
                        if allowed:
                            self.assertEqual(out['kernel-write'],'true')
                            self.assertEqual(out['cache-write'],str(kind in {'build','validate-main'}).lower())
                            self.assertEqual(json.loads(out['matrix']),seed.matrix(architectures.split(',')))
                            env['KERNEL_CACHE_WRITE']='true'
                            self.assertEqual(host.producer_context(env,data,'a'*40),kind)
                            for key,value in [('GITHUB_REF','refs/heads/feature'),('GITHUB_REPOSITORY','foreign/repo'),
                                              ('GITHUB_EVENT_NAME','pull_request'),('KERNEL_CACHE_WRITE','false'),
                                              ('GITHUB_WORKFLOW_REF',host.REPO+'/.github/workflows/test.yml@refs/heads/main')]:
                                with self.assertRaises(ValueError):host.producer_context(dict(env,**{key:value}),data,'a'*40)
            for override in ({'GITHUB_REF':'refs/heads/feature'},{'GITHUB_EVENT_NAME':'pull_request'},
                             {'GITHUB_REPOSITORY':'foreign/repo'},{'PRODUCER_KIND':'invalid'}):
                self.assertNotEqual(self.invoke(kind,override=override)[0],0)

    def test_shared_top_level_queue_untrusted_isolation_no_nested_lock_and_workflow_conditions(self):
        for filename in ('release.yml','kernel-seed.yml','validate-staged.yml'):
            caller=workflow(filename);queue=caller['concurrency']
            self.assertEqual(queue['queue'],'max');self.assertEqual(queue['cancel-in-progress'],'false')
            context={'github.repository':host.REPO,'github.ref':'refs/heads/main','github.event_name':'workflow_dispatch',
                     'github.event.pull_request.number':''}
            group=queue['group']
            self.assertEqual(expression_value(group,context) if group.startswith('${{') else group,'shirube-os-release')
            if filename!='release.yml':
                for ref in ('refs/heads/feature','refs/pull/40/merge'):
                    self.assertNotEqual(expression_value(group,dict(context,**{'github.ref':ref})),'shirube-os-release')
        staged=workflow('staged-candidate.yml')
        self.assertNotIn('concurrency',staged)
        for job in staged['jobs'].values():self.assertNotIn('concurrency',job)
        for name in ('compiler','vpp','kernel'):
            self.assertEqual(staged['jobs'][name]['strategy']['matrix'],'${{ fromJSON(needs.admission.outputs.matrix) }}')
        release=workflow('release.yml')['jobs']
        for publication,execution,probe in [('publish','candidate',False),('none','candidate',False),('hold','candidate',False),('none','focused',True)]:
            self.assertEqual(expression_value(release['seed-plan']['if'],{'needs.resolve.outputs.publication':publication,
                             'needs.resolve.outputs.automatic-execution':execution}),probe)
        for caller, name, need in [(release,'kernel-seed','seed-plan'),(workflow('kernel-seed.yml')['jobs'],'seed','plan')]:
            for selected in ('','amd64','arm64','amd64,arm64'):
                self.assertEqual(expression_value(caller[name]['if'],{f'needs.{need}.outputs.selected':selected}),bool(selected))


class EvidenceTests(unittest.TestCase):
    def test_seed_outcomes_never_clear_required_full_gates_and_failures_still_fail(self):
        from producer import actions
        for status in ('covered','pending-unknown','pending-superseded','seed-required'):
            for seed_result in ('skipped','success','failure','cancelled'):
                with self.subTest(status=status,seed_result=seed_result), tempfile.TemporaryDirectory() as temp:
                    root=Path(temp)
                    plan=release_plan.plan(['.github/workflows/staged-candidate.yml'],'a'*40,run='10',attempt='1')
                    self.assertEqual(plan['validation'],'candidate')
                    (root/'plan.json').write_text(json.dumps(plan)); before=(root/'plan.json').read_bytes()
                    jobs={'resolve':{'result':'success'},'seed-plan':{'result':'success','outputs':{'status':status}},
                          'kernel-seed':{'result':seed_result},'candidate-validation':{'result':'skipped'},
                          'candidate-check':{'result':'skipped'}}
                    env=dict(SHIRUBE_PLAN_DIRECTORY=str(root),SHIRUBE_SOURCE_COMMIT='a'*40,
                             SHIRUBE_JOB_RESULTS=json.dumps(jobs),GITHUB_RUN_ID='10',GITHUB_RUN_ATTEMPT='1',
                             GITHUB_STEP_SUMMARY=str(root/'summary'))
                    with patch.dict(os.environ,env),patch.object(actions,'identity'):
                        if seed_result in {'failure','cancelled'}:
                            with self.assertRaises(ValueError):actions.evidence()
                        else:actions.evidence()
                    result=json.loads((root/'result.json').read_text())
                    self.assertEqual(result['status'],'failed' if seed_result in {'failure','cancelled'} else 'pending-required-validation')
                    self.assertEqual(result['gates']['candidate'],'required-but-deferred')
                    self.assertEqual(result['gates']['aggregate'],'required-but-deferred')
                    self.assertEqual(result['kernel_seed_status'],status)
                    self.assertEqual((root/'plan.json').read_bytes(),before)
        for paths,automatic in [(['source-lock.json'],'candidate'),(['new-unknown'],'candidate'),
                                (['schemas/schema.json'],'candidate'),(['scripts/kernel_seed_plan.py','source-lock.json'],'candidate')]:
            result=release_plan.plan(paths,'a'*40)
            self.assertEqual(result['automatic_execution'],automatic)
            self.assertEqual(result['execution_deferred_gates'],[])
            self.assertEqual(result['prior_guest_evidence'],'NOT_REUSED')

    def test_plan_cli_cold_no_api_and_unknown_evidence_bound_to_source(self):
        _,_,env,data=AdmissionAndQueueTests().invoke('manual-seed')
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'event').write_text(json.dumps(data))
            env.update(GITHUB_EVENT_PATH=str(root/'event'),GITHUB_OUTPUT=str(root/'output'),
                       GITHUB_STEP_SUMMARY=str(root/'summary'),GITHUB_RUN_ID='10',GITHUB_RUN_ATTEMPT='2',
                       KERNEL_CACHE_WRITE='true')
            for mode in ('cold','warm'):
                argv=['planner','--commit','a'*40,'--architectures','arm64','--cache-mode',mode,'--output',str(root/'plan.json')]
                with patch.dict(os.environ,env,clear=True),patch.object(planner.sys,'argv',argv), \
                     patch.object(planner.subprocess,'check_output',return_value='a'*40), \
                     patch.object(planner,'API',side_effect=OSError('private response')) as api, \
                     patch.object(planner,'key',return_value='f'*64):
                    planner.main()
                result=json.loads((root/'plan.json').read_text())
                self.assertEqual(result['source_commit'],'a'*40)
                self.assertEqual(result['source_keys'],{'arm64':'f'*64})
                self.assertEqual((result['run_id'],result['run_attempt']),('10','2'))
                self.assertEqual(result['full_validation'],'NOT_RUN')
                self.assertEqual(result['selected'],['arm64'] if mode=='cold' else [])
                self.assertEqual(result['status'],'seed-required' if mode=='cold' else 'pending-unknown')
                self.assertNotIn('private', (root/'plan.json').read_text())
                self.assertEqual(api.call_count,int(mode=='warm'))


class IndexCLITests(unittest.TestCase):
    def invoke(self, remote, env, event, reject=False):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root/'provenance.json').write_text(json.dumps(remote.receipt))
            (root/'event').write_text(json.dumps(event))
            env = dict(env, GITHUB_EVENT_PATH=str(root/'event'),
                       GITHUB_RUN_ID='10', GITHUB_RUN_ATTEMPT='1', KERNEL_CACHE_WRITE='true',
                       EXPORT_ARTIFACT_ID='4',
                       EXPORT_ARTIFACT_DIGEST=hashlib.sha256(remote.export_zip).hexdigest(),
                       GH_TOKEN='fixture-host-only')
            argv = ['cache', 'index', '--commit', 'a'*40, '--architecture', remote.arch,
                    '--directory', str(root)]
            with patch.dict(os.environ, env, clear=True), patch.object(host.sys, 'argv', argv), \
                 patch.object(host, 'API', return_value=remote) as api:
                if reject:
                    with self.assertRaises(ValueError):
                        host.main()
                    self.assertFalse((root/'index.json').exists())
                else:
                    host.main()
                    record = json.loads((root/'index.json').read_text())
                    self.assertEqual(record['export_artifact'], host.artifact_binding(remote.export))
                    self.assertEqual(record['receipt'], remote.receipt)
                    # The published identities must also satisfy the independent
                    # completed-run/PR/reusable/job authority used by readers.
                    host.verify_producer(remote, remote.run, remote.export, record['receipt'], remote.arch)
                if api.called:
                    api.assert_called_once_with('fixture-host-only')
                else:
                    self.assertTrue(reject)
            self.assertEqual(remote.downloads, [])

    def context(self, remote):
        _, _, env, event = AdmissionAndQueueTests().invoke(remote.kind)
        if remote.run['event'] == 'pull_request_target':
            event['pull_request'].update(remote.pr, number=remote.receipt['source_pr'])
            self.assertNotEqual(remote.run['head_sha'], remote.receipt['source_commit'])
            self.assertEqual(env['GITHUB_SHA'], remote.receipt['source_commit'])
        return env, event

    def test_all_five_kinds_both_arches_publish_with_exact_event_run_identity(self):
        for kind in host.PRODUCER_KINDS:
            for arch in seed.ARCHITECTURES:
                with self.subTest(kind=kind, arch=arch):
                    remote = IndexedRemote(kind, arch)
                    self.invoke(remote, *self.context(remote))

    def test_all_five_kinds_reject_wrong_head_run_repo_attempt_source_and_provenance(self):
        for kind in host.PRODUCER_KINDS:
            for mutation in ('head', 'run', 'repo', 'attempt', 'source', 'digest', 'entries'):
                with self.subTest(kind=kind, mutation=mutation):
                    remote = IndexedRemote(kind)
                    env, event = self.context(remote)
                    if mutation == 'head': remote.export['workflow_run']['head_sha'] = 'c'*40
                    if mutation == 'run': remote.export['workflow_run']['id'] = 11
                    if mutation == 'repo': remote.export['workflow_run']['repository_id'] = 1
                    if mutation == 'attempt': remote.receipt['run_attempt'] = 2
                    if mutation == 'source': remote.receipt['source_commit'] = 'c'*40
                    if mutation == 'digest': remote.export['digest'] = 'sha256:'+'0'*64
                    if mutation == 'entries': remote.receipt['measured']['export']['entries'] = 0
                    self.invoke(remote, env, event, reject=True)

    def test_merged_head_cannot_be_replaced_by_source_or_missing_event_head(self):
        for kind in ('build', 'candidate-validation', 'automatic-seed'):
            for mutation in ('source-as-head', 'event-head', 'missing-event-head'):
                with self.subTest(kind=kind, mutation=mutation):
                    remote = IndexedRemote(kind)
                    env, event = self.context(remote)
                    if mutation == 'source-as-head': remote.export['workflow_run']['head_sha'] = env['GITHUB_SHA']
                    if mutation == 'event-head': event['pull_request']['head']['sha'] = 'c'*40
                    if mutation == 'missing-event-head': event['pull_request'].pop('head')
                    self.invoke(remote, env, event, reject=True)

    def test_dispatch_source_sha_and_workflow_sha_must_all_match(self):
        for kind in ('manual-seed', 'validate-main'):
            for field in ('GITHUB_SHA', 'GITHUB_WORKFLOW_SHA'):
                with self.subTest(kind=kind, field=field):
                    remote = IndexedRemote(kind)
                    env, event = self.context(remote)
                    env[field] = 'b'*40
                    # Even matching artifact metadata cannot override a dispatch
                    # source/workflow mismatch.
                    remote.export['workflow_run']['head_sha'] = 'b'*40
                    self.invoke(remote, env, event, reject=True)


class IndexArchiveTests(unittest.TestCase):
    def test_index_zip_links_paths_duplicates_extra_and_expansion_bound_reject(self):
        for mutation in ('link','path','duplicate','extra','expanded'):
            remote=IndexedRemote()
            buffer=io.BytesIO()
            with zipfile.ZipFile(buffer,'w',compression=zipfile.ZIP_DEFLATED) as archive:
                info=zipfile.ZipInfo('../index.json' if mutation=='path' else 'index.json')
                info.compress_type=zipfile.ZIP_DEFLATED
                if mutation=='link':info.external_attr=0o120777 << 16
                archive.writestr(info,b'x'*(host.INDEX_LIMIT+1) if mutation=='expanded' else json.dumps(remote.record).encode())
                if mutation=='duplicate':
                    import warnings
                    with warnings.catch_warnings():
                        warnings.simplefilter('ignore',UserWarning)
                        archive.writestr('index.json','{}')
                if mutation=='extra':archive.writestr('private.pem','not a compiler cache')
            remote.index_zip=buffer.getvalue()
            remote.index.update(size_in_bytes=len(remote.index_zip),digest='sha256:'+hashlib.sha256(remote.index_zip).hexdigest())
            with self.subTest(mutation=mutation):
                self.assertEqual(host.find_index(remote,'amd64',remote.key)[0],'unknown')
                self.assertNotIn(4,remote.downloads)
                if mutation=='expanded':
                    self.assertLess(len(remote.index_zip),host.INDEX_LIMIT)
                    self.assertEqual(remote.downloads,[5])

    def test_incompatible_or_corrupt_older_index_cannot_hide_compatible_other_kind(self):
        old=IndexedRemote('build');good=IndexedRemote('manual-seed')
        old.index['id']=6;old.receipt['coverage_key']='0'*64;old.refresh()
        class Remote:
            def json(self,path):
                if path.startswith('actions/artifacts?name='):
                    return dict(total_count=2,artifacts=[old.index,good.index])
                if path=='actions/artifacts/6':return old.index
                return good.json(path)
            def download(self,identity,target,size):
                if identity==6:target.write_bytes(old.index_zip)
                else:good.download(identity,target,size)
        self.assertEqual(host.find_index(Remote(),'amd64',good.key)[0],'covered')
        self.assertNotIn(4,good.downloads)


class MergedInventoryCLITests(unittest.TestCase):
    """Real tiny Git histories drive the automatic CLI, with no remote service."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = component_project(self.root/'source')
        self.old_cwd = Path.cwd()
        os.chdir(self.source)
        self.addCleanup(os.chdir, self.old_cwd)
        self.git('init', '-b', 'main')
        self.git('config', 'user.email', 'fixture@example.invalid')
        self.git('config', 'user.name', 'Fixture')
        self.git('config', 'commit.gpgsign', 'false')
        # An input consumed only by the old graph; deleting both it and its
        # declaration must still be relevant against the complete before tree.
        self.name = 'docs/old-kernel-input.txt'
        path = self.source/self.name
        path.parent.mkdir(); path.write_text('input\n')
        declaration = self.source/'containers/native/inputs-kernel.json'
        self.declaration = json.loads(declaration.read_text())
        self.declaration['roots'].append(self.name)
        self.declaration['graph'][self.name] = []
        declaration.write_text(json.dumps(self.declaration))
        self.commit('baseline')
        self.base = self.git('rev-parse', 'HEAD')

    def git(self, *args):
        return subprocess.check_output(['git', *args], text=True, stderr=subprocess.PIPE).strip()

    def commit(self, message):
        self.git('add', '-A'); self.git('commit', '-m', message)
        return self.git('rev-parse', 'HEAD')

    def history(self, mode, change='deleted-graph'):
        self.git('checkout', '-b', 'feature')
        if change == 'deleted-graph':
            (self.source/self.name).unlink()
            declaration = dict(self.declaration)
            declaration['roots'] = [p for p in declaration['roots'] if p != self.name]
            declaration['graph'] = {p: edges for p, edges in declaration['graph'].items() if p != self.name}
            (self.source/'containers/native/inputs-kernel.json').write_text(json.dumps(declaration))
        elif change == 'orchestration':
            (self.source/'scripts').mkdir(exist_ok=True)
            (self.source/'scripts/kernel_seed_plan.py').write_text('# bootstrap\n')
        elif change == 'rename':
            self.git('mv', self.name, 'docs/new-kernel-input.txt')
            declaration = json.loads(json.dumps(self.declaration).replace(self.name, 'docs/new-kernel-input.txt'))
            (self.source/'containers/native/inputs-kernel.json').write_text(json.dumps(declaration))
        else:
            (self.source/'docs/unrelated.md').write_text('unrelated\n')
        self.commit('first relevant change')
        (self.source/'README.md').write_text('last docs only\n')
        self.head = self.commit('last docs only')
        raw = self.git('diff', '--name-status', '--no-renames', self.base, self.head)
        self.pages = [[dict(status={'A':'added','D':'removed','M':'modified'}[line.split('\t')[0]],
                            filename=line.split('\t')[1]) for line in raw.splitlines()]]
        self.git('checkout', 'main')
        if mode == 'fast-forward':
            self.main_before = self.base
        else:
            (self.source/'docs/main-only.md').write_text('main advanced\n')
            self.main_before = self.commit('main advanced independently')
        if mode == 'merge':
            self.git('merge', '--no-ff', 'feature', '-m', 'merge')
        elif mode == 'squash':
            self.git('merge', '--squash', 'feature'); self.commit('squash')
        elif mode == 'fast-forward':
            self.git('merge', '--ff-only', 'feature')
        else:
            self.git('checkout', 'feature'); self.git('rebase', 'main')
            self.git('checkout', 'main'); self.git('merge', '--ff-only', 'feature')
        self.merged = self.git('rev-parse', 'HEAD')
        self.git('remote', 'add', 'origin', str(self.source))
        _, _, self.env, self.event = AdmissionAndQueueTests().invoke('automatic-seed')
        self.event['pull_request'].update(number=40, merge_commit_sha=self.merged,
            changed_files=len(self.pages[0]), commits=2, head=dict(sha=self.head))
        self.event['pull_request']['base']['sha'] = self.main_before
        self.env.update(GITHUB_SHA=self.merged, GITHUB_WORKFLOW_SHA=self.merged,
                        GITHUB_RUN_ID='10', GITHUB_RUN_ATTEMPT='2', KERNEL_CACHE_WRITE='true')
        pr = self.event['pull_request']
        self.record = dict(source_commit=self.merged, run_id='10', run_attempt='2',
            seed_trigger=dict(schema_version=1, repository=self.event['repository'],
                pr_number=40, source_commit=self.merged, base_commit=self.main_before,
                head_commit=self.head, changed_files=pr['changed_files'], commits=2, pages=self.pages))

    def invoke(self, record=True, full=False):
        event_path = self.root/'event.json'; event_path.write_text(json.dumps(self.event))
        inventory_path = self.root/'release-plan.json'
        if record == 'malformed': inventory_path.write_text('{broken')
        elif record: inventory_path.write_text(json.dumps(self.record))
        elif inventory_path.exists(): inventory_path.unlink()
        output = self.root/'plan.json'
        env = dict(self.env, GITHUB_EVENT_PATH=str(event_path),
                   GITHUB_OUTPUT=str(self.root/'output'), GITHUB_STEP_SUMMARY=str(self.root/'summary'))
        argv = ['planner', '--automatic', '--commit', self.merged,
                '--release-plan', str(inventory_path), '--output', str(output)]
        if full: argv.append('--full-planned')
        with patch.dict(os.environ, env, clear=True), patch.object(planner.sys, 'argv', argv), \
             patch.object(planner, 'API', return_value=None), \
             patch.object(planner, 'find_index', return_value=('missing', None)) as probe:
            planner.main()
        return json.loads(output.read_text()), probe.call_count

    def test_resolve_saved_plan_round_trips_into_automatic_trigger(self):
        from producer import actions
        self.history('rebase', 'orchestration')
        event_path = self.root/'resolve-event.json'
        event_path.write_text(json.dumps(self.event))
        directory = self.root/'resolve-plan'
        env = dict(self.env, GITHUB_EVENT_PATH=str(event_path),
                   SHIRUBE_PLAN_DIRECTORY=str(directory),
                   SHIRUBE_SOURCE_COMMIT=self.merged)
        with patch.dict(os.environ, env, clear=True), \
             patch.object(actions, 'gh', return_value=self.pages), \
             patch.object(actions, 'identity'), \
             patch.object(actions, 'dependency_evidence', return_value={}), \
             patch.object(actions, 'emit'):
            actions.resolve()
            changed, before, trigger = planner.automatic_trigger(directory/'plan.json', self.event, self.merged)
        self.assertEqual(set(changed), {item['filename'] for page in self.pages for item in page})
        self.assertEqual(before, self.base)
        self.assertEqual(trigger['commits'], 2)
        self.assertEqual(trigger['head_commit'], self.head)

    def test_real_merge_squash_rebase_complete_inventory_and_old_graph(self):
        # Separate fixture instance per mode keeps all Git operations genuine.
        for mode in ('merge', 'squash', 'rebase', 'fast-forward'):
            with self.subTest(mode=mode):
                fixture = MergedInventoryCLITests()
                fixture.setUp()
                try:
                    fixture.history(mode)
                    if mode == 'rebase':
                        changed = fixture.git('diff', '--name-only', fixture.merged+'^1', fixture.merged).splitlines()
                        with planner.snapshot(fixture.merged+'^1') as old:
                            self.assertFalse(seed.relevant(changed, old, fixture.source))
                    result, calls = fixture.invoke()
                    self.assertEqual((result['status'], calls), ('seed-required', 2))
                    self.assertEqual(result['selected'], list(seed.ARCHITECTURES))
                    self.assertEqual(result['source_commit'], fixture.merged)
                    self.assertEqual(result['trigger_inventory']['before_commit'], fixture.base)
                    fixture.event['pull_request']['base']['sha'] = fixture.merged
                    fixture.record['seed_trigger']['base_commit'] = fixture.merged
                    result, calls = fixture.invoke()
                    self.assertEqual((result['status'], calls), ('seed-required', 2))
                    self.assertEqual(result['trigger_inventory']['before_commit'], fixture.base)
                finally:
                    fixture.doCleanups()

    def test_real_rebase_earlier_only_orchestration_and_unrelated(self):
        self.history('rebase', 'orchestration')
        result, calls = self.invoke()
        self.assertEqual((result['status'], calls), ('seed-required', 2))
        self.record['seed_trigger']['pages'] = [[dict(filename='README.md', status='modified')]]
        self.record['seed_trigger']['changed_files'] = 1
        self.event['pull_request']['changed_files'] = 1
        result, calls = self.invoke()
        self.assertEqual((result['status'], calls), ('irrelevant', 0))

    def test_missing_malformed_incomplete_provenance_unknown_before_probe_and_full_priority(self):
        self.history('rebase')
        original = copy.deepcopy(self.record)
        mutations = [lambda r: r.update(run_id='11'), lambda r: r.update(run_attempt='1'),
            lambda r: r.update(source_commit='f'*40),
            lambda r: r['seed_trigger'].update(base_commit='f'*40),
            lambda r: r['seed_trigger'].update(head_commit='f'*40),
            lambda r: r['seed_trigger'].update(source_commit='f'*40),
            lambda r: r['seed_trigger'].update(repository=dict(id=1, full_name=host.REPO)),
            lambda r: r['seed_trigger'].update(pr_number=41),
            lambda r: r['seed_trigger'].update(schema_version=2),
            lambda r: r['seed_trigger'].update(schema_version=True),
            lambda r: r['seed_trigger'].update(changed_files=True),
            lambda r: r['seed_trigger'].update(commits=True),
            lambda r: r['seed_trigger'].update(commits=0),
            lambda r: r['seed_trigger'].update(pages=[]),
            lambda r: r['seed_trigger'].update(pages=[r['seed_trigger']['pages'][0][:-1]]),
            lambda r: r['seed_trigger']['pages'][0][0].update(status='invalid'),
            lambda r: r['seed_trigger']['pages'][0][0].update(filename='../invalid'),
            lambda r: r['seed_trigger']['pages'][0][0].update(status='renamed'),
            lambda r: r.pop('seed_trigger')]
        for mutate in mutations:
            self.record = copy.deepcopy(original); mutate(self.record)
            result, calls = self.invoke()
            self.assertEqual((result['status'], result['selected'], calls), ('pending-unknown', [], 0))
            self.assertIn('status=pending-unknown', (self.root/'output').read_text())
        result, calls = self.invoke(record=False)
        self.assertEqual((result['status'], calls), ('pending-unknown', 0))
        result, calls = self.invoke(record='malformed')
        self.assertEqual((result['status'], calls), ('pending-unknown', 0))
        result, calls = self.invoke(record=False, full=True)
        self.assertEqual((result['status'], calls), ('existing-required-work', 0))
        for name in ('commits', 'changed_files'):
            self.record = copy.deepcopy(original)
            saved = self.event['pull_request'][name]
            self.record['seed_trigger'][name] = 1
            self.event['pull_request'][name] = True
            result, calls = self.invoke()
            self.assertEqual((result['status'], calls), ('pending-unknown', 0))
            self.event['pull_request'][name] = saved

    def test_rename_previous_path_and_deleted_input_survive_inventory(self):
        self.history('rebase', 'rename')
        self.record['seed_trigger']['pages'][0] = [
            dict(filename='docs/new-name.txt', previous_filename=self.name, status='renamed'),
            dict(filename='README.md', status='modified'),
            dict(filename='containers/native/inputs-kernel.json', status='modified')]
        self.record['seed_trigger']['changed_files'] = 3
        self.event['pull_request']['changed_files'] = 3
        result, calls = self.invoke()
        self.assertEqual((result['status'], calls), ('seed-required', 2))

    def test_real_unrelated_rebase_is_noop(self):
        self.history('rebase', 'unrelated')
        result, calls = self.invoke()
        self.assertEqual((result['status'], result['selected'], calls), ('irrelevant', [], 0))

    def test_malformed_old_graph_is_unknown(self):
        declaration = self.source/'containers/native/inputs-kernel.json'
        declaration.write_text('{malformed')
        self.base = self.commit('malformed baseline')
        # Fix the after tree, so it is specifically before-graph validation
        # which blocks the plan rather than source-key construction.
        self.git('checkout', '-b', 'repair')
        declaration.write_text(json.dumps(self.declaration))
        self.commit('repair')
        self.git('checkout', 'main')
        # history writes a valid final graph in its first commit.
        self.history('rebase')
        result, calls = self.invoke()
        self.assertEqual((result['status'], calls), ('pending-unknown', 0))

    def test_original_rebase_head_missing_from_main_checkout_is_fetched_by_sha(self):
        self.history('rebase')
        clone = self.root/'main-checkout'
        self.git('clone', '--no-local', '--single-branch', '--branch', 'main', str(self.source), str(clone))
        os.chdir(clone)
        self.assertNotEqual(subprocess.run(['git', 'cat-file', '-e', self.head+'^{commit}'],
                                          capture_output=True).returncode, 0)
        result, calls = self.invoke()
        self.assertEqual((result['status'], calls), ('seed-required', 2))
        self.assertEqual(self.git('rev-parse', 'HEAD'), self.merged)
        self.assertEqual(self.git('rev-parse', self.head+'^{commit}'), self.head)

    def test_workflow_current_run_inventory_download_failure_continues_to_unknown(self):
        job = workflow('release.yml')['jobs']['seed-plan']
        download = next(s for s in job['steps'] if s.get('name') == "Read this run's complete merged PR inventory")
        self.assertEqual(download['with']['name'], '${{ needs.resolve.outputs.plan-artifact }}')
        self.assertNotIn('run-id', download['with'])
        self.assertIn(download['continue-on-error'], (True, 'true'))
        self.assertEqual(job['permissions'], {'contents': 'read', 'actions': 'read'})
        self.assertIn('--release-plan', next(s for s in job['steps'] if s.get('id') == 'probe')['run'])
